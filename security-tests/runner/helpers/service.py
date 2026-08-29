"""Build, sign, upload and deliver a probe service.

Signing and upload use the public aos-signer CLI; delivery uses the cloud
sub-resource bindings in runner.cloud_client.
"""
from __future__ import annotations

import logging
import pathlib
import shutil
import subprocess
import time

from ..cloud_client import CloudClient
from .vm import VM

_LOG = logging.getLogger("security-tests.service")
_DELIVERY_TIMEOUT_S = 1800
_POLL_S = 15


class ServiceError(RuntimeError):
    """Raised when the probe service cannot be published or delivered."""


def stage_probe(suite_root: pathlib.Path, work_dir: pathlib.Path, marker: str,
                version: str, sp_p12: pathlib.Path) -> pathlib.Path:
    """Copy the probe, inject the marker and the signing key, return its folder."""
    source = suite_root / "probes" / "marker-writer"
    staged = work_dir / "marker-writer"
    if staged.exists():
        shutil.rmtree(staged)
    shutil.copytree(source, staged)

    config = (staged / "config.yaml.in").read_text(encoding="utf-8")
    config = config.replace("@VERSION@", version).replace("@MARKER@", marker)
    (staged / "config.yaml").write_text(config, encoding="utf-8")
    (staged / "config.yaml.in").unlink()

    if not sp_p12.is_file():
        raise ServiceError(f"service-provider certificate not found: {sp_p12}")
    shutil.copy2(sp_p12, staged / "aos-user-sp.p12")
    return staged


def publish(folder: pathlib.Path) -> None:
    """Sign and upload the staged probe with aos-signer."""
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["aos-signer", "go"], cwd=folder, capture_output=True, text=True,
        timeout=1800, check=False,
    )
    if completed.returncode != 0:
        raise ServiceError(
            f"aos-signer failed ({completed.returncode}):\n{completed.stdout}{completed.stderr}"
        )


def wait_until_running(vm: VM, timeout_s: int = _DELIVERY_TIMEOUT_S) -> bool:
    """Wait for any service instance to reach the active state on the unit."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        statuses = vm.exec(
            "journalctl -u aos-cm --no-pager -o cat --since -30min 2>/dev/null "
            "| grep 'Node instance status received' | grep 'service:' | tail -1"
        ).text()
        if "state=active" in statuses and "err=none" in statuses:
            return True
        time.sleep(_POLL_S)
    return False


PROBE_CODENAME = "security-probe-marker-writer"


def deliver(cloud: CloudClient, system_uid: str, unit_set_id: str, subject_id: str) -> None:
    """Make the cloud offer the probe to this unit.

    Three bindings are needed and all three are keyed the way the API expects:
    the service must hang off the test subject, the unit must be a member of the
    validation unit-set, and the unit must be attached to that subject. Without
    the unit-set membership the desired status arrives with no services at all
    and nothing reports an error.
    """
    service_id = cloud.find_service_id(PROBE_CODENAME)
    if service_id is None:
        raise ServiceError(
            f"service {PROBE_CODENAME!r} is not present in the tenant after upload"
        )
    _LOG.info("attaching service %s to subject %s", service_id, subject_id)
    cloud.attach_service_to_subject(subject_id, service_id)
    _LOG.info("attaching unit %s to unit-set and subject", system_uid)
    cloud.attach_unit(system_uid, unit_set_id, subject_id)


def read_marker_in_instance(vm: VM, marker: str) -> bool:
    """Whether the marker is readable through the instance storage mount."""
    found = vm.exec(
        f"grep -rl -- {marker!r} /var/aos/storages 2>/dev/null | head -1"
    ).text()
    return bool(found)
