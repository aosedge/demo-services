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

    config = (staged / _CONFIG_TEMPLATE).read_text(encoding="utf-8")
    config = (
        config.replace("@VERSION@", version)
        .replace("@MARKER@", marker)
        .replace("@CODENAME@", PROBE_CODENAME)
    )
    (staged / _CONFIG_RENDERED).write_text(config, encoding="utf-8")
    (staged / _CONFIG_TEMPLATE).unlink()

    if not sp_p12.is_file():
        raise ServiceError(f"service-provider certificate not found: {sp_p12}")
    shutil.copy2(sp_p12, staged / "aos-user-sp.p12")
    return staged


def publish_probe(cloud: CloudClient, config, marker: str) -> str:
    """Stage, sign and upload the probe, and return the version published."""
    version = next_version(cloud, PROBE_CODENAME)
    staged = stage_probe(
        config.suite_root, config.suite_root / ".run", marker, version, config.sp_p12
    )
    publish(staged)
    return version


def publish_snooper(cloud: CloudClient, config, marker: str,
                    targets: tuple[str, ...]) -> str:
    """Stage, sign and upload the isolation probe; return the version published."""
    version = next_version(cloud, SNOOPER_CODENAME)
    source = config.suite_root / "probes" / "snooper"
    work = config.suite_root / ".run"
    staged = work / "snooper"
    if staged.exists():
        shutil.rmtree(staged)
    shutil.copytree(source, staged)
    template = (staged / _CONFIG_TEMPLATE).read_text(encoding="utf-8")
    rendered = (
        template.replace("@VERSION@", version)
        .replace("@MARKER@", marker)
        .replace("@CODENAME@", SNOOPER_CODENAME)
        .replace("@TARGETS@", ":".join(targets))
    )
    (staged / _CONFIG_RENDERED).write_text(rendered, encoding="utf-8")
    (staged / _CONFIG_TEMPLATE).unlink()
    if not config.sp_p12.is_file():
        raise ServiceError(f"service-provider certificate not found: {config.sp_p12}")
    shutil.copy2(config.sp_p12, staged / "aos-user-sp.p12")
    publish(staged)
    return version


def wait_for_snoop_report(vm: VM, timeout_s: int = 900) -> str:
    """Wait for the isolation probe to write its report, and return it."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        path = vm.exec(
            f"find {INSTANCE_STORAGE} -name '*.snoop' 2>/dev/null | head -1", timeout=300
        ).text()
        if path:
            content = vm.exec(f"cat {path}", timeout=300).text()
            if content.strip().endswith("}"):
                return content
        time.sleep(_POLL_S)
    return ""


def publish(folder: pathlib.Path) -> None:
    """Sign and upload the staged probe with aos-signer."""
    completed = subprocess.run(
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
# The tamper check publishes into its own service so that its control bundle
# can never become the version the delivery tests are offered.
TAMPER_CODENAME = "security-probe-tamper-check"
# The isolation probe is its own service too, so its versions cannot become
# the ones the delivery tests are offered.
SNOOPER_CODENAME = "security-probe-snooper"
_VERSION_PREFIX = "1.0.0-rc."
_CONFIG_TEMPLATE = "config.yaml.in"
_CONFIG_RENDERED = "config.yaml"
_READY_STATE = "ready"
_READY_TIMEOUT_S = 900


def next_version(cloud: CloudClient, codename: str) -> str:
    """Return a version strictly above every version this service already has.

    The cloud keeps offering the highest existing version, and it orders
    pre-release identifiers as text, so a number with more digits outranks a
    larger-looking one. Reading the current versions and stepping past them is
    the only way to be sure the bundle just published is the one delivered.
    """
    service_id = cloud.find_service_id(codename)
    highest = 0
    if service_id:
        for version in cloud.service_versions(service_id):
            suffix = version[len(_VERSION_PREFIX):] if version.startswith(_VERSION_PREFIX) else ""
            if suffix.isdigit():
                highest = max(highest, int(suffix))
    return f"{_VERSION_PREFIX}{max(highest + 1, int(time.time()))}"


def wait_until_ready(cloud: CloudClient, service_id: str, version: str,
                     timeout_s: int = _READY_TIMEOUT_S) -> None:
    """Block until the uploaded version is usable.

    Uploading is not enough: the cloud validates the bundle first, and
    assigning a service whose version is not yet "ready" is refused with
    HTTP 400 ("without versions in \"ready\" state cannot be assigned").
    """
    deadline = time.time() + timeout_s
    state = None
    while time.time() < deadline:
        state = cloud.service_version_state(service_id, version)
        if state == _READY_STATE:
            _LOG.info("service %s version %s is ready", service_id, version)
            return
        time.sleep(_POLL_S)
    raise ServiceError(
        f"service {service_id} version {version} did not reach "
        f"{_READY_STATE!r} within {timeout_s}s (last state: {state!r})"
    )


def deliver(cloud: CloudClient, system_uid: str, unit_set_id: str, *,
            subject_id: str, version: str, codename: str = PROBE_CODENAME) -> None:
    """Make the cloud offer the probe to this unit.

    Three bindings are needed and all three are keyed the way the API expects:
    the service must hang off the test subject, the unit must be a member of the
    validation unit-set, and the unit must be attached to that subject. Without
    the unit-set membership the desired status arrives with no services at all
    and nothing reports an error.
    """
    service_id = cloud.find_service_id(codename)
    if service_id is None:
        raise ServiceError(
            f"service {codename!r} is not present in the tenant after upload"
        )
    wait_until_ready(cloud, service_id, version)
    _LOG.info("attaching service %s to subject %s", service_id, subject_id)
    cloud.attach_service_to_subject(subject_id, service_id)
    _LOG.info("attaching unit %s to unit-set and subject", system_uid)
    cloud.attach_unit(system_uid, unit_set_id, subject_id)


INSTANCE_STORAGE = "/var/aos/storages"


def find_marker_path(vm: VM, marker: str) -> str:
    """Path of the *data file* the probe wrote, as seen from the unit, or "".

    Scoped to the instance storage area on purpose. Searching all of /var/aos
    also matches the marker inside the service image blob, because the value is
    carried into the bundle as an environment variable - so a wider search
    would report success even if the instance had never written anything.
    """
    return vm.exec(
        f"grep -rl -- {marker!r} {INSTANCE_STORAGE} 2>/dev/null | head -1", timeout=900
    ).text()


def wait_for_marker(vm: VM, marker: str, timeout_s: int = 600) -> str:
    """Wait for the instance to write its marker, and return where it landed.

    Being reported active only means the runtime started the container; the
    payload writes its data a moment later, so a single look can miss it.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        path = find_marker_path(vm, marker)
        if path:
            return path
        time.sleep(_POLL_S)
    return ""


def read_marker_in_instance(vm: VM, marker: str) -> bool:
    """Whether the marker written by the probe is readable on the unit."""
    return bool(find_marker_path(vm, marker))


def storage_layout(vm: VM) -> str:
    """Short description of the instance storage area, for failure messages."""
    return vm.exec("ls -R /var/aos/storages 2>/dev/null | head -20").text()


def instance_log_tail(vm: VM, lines: int = 15) -> str:
    """Recent service-instance output, for failure messages."""
    return vm.exec(
        f"journalctl --no-pager -o cat --since -30min 2>/dev/null "
        f"| grep -iE 'marker|traceback|storage' | tail -{lines}"
    ).text()
