"""Unit provisioning and local deprovisioning.

Provisioning uses the public aos-prov CLI. Local deprovisioning uses the
on-unit script that ships with AosCore, so the suite exercises the documented
path rather than a bespoke one.
"""
from __future__ import annotations

import logging
import re
import subprocess
import time

from .vm import VM, VMError

_LOG = logging.getLogger("security-tests.provisioning")
_SYSTEM_ID = re.compile(r"System ID:\s*([0-9a-f]{32})")


class ProvisioningError(RuntimeError):
    """Raised when the unit cannot be provisioned or released."""


def provision(config, host: str = "127.0.0.1") -> str:
    """Register the VM with AosCloud and return its system id.

    --nodes 1 is mandatory: the default of 2 makes aos-prov wait indefinitely
    for a second node that a single-node unit never presents.
    """
    command = [
        "aos-prov",
        "provision",
        "-u",
        f"{host}:{config.prov_port}",
        "--nodes",
        "1",
        "-p",
        str(config.oem_p12),
    ]
    _LOG.info("provisioning: %s", " ".join(command))
    completed = subprocess.run(
        command, capture_output=True, text=True, timeout=3600, check=False
    )
    output = completed.stdout + completed.stderr
    if completed.returncode != 0:
        raise ProvisioningError(f"aos-prov failed ({completed.returncode}):\n{output}")
    match = _SYSTEM_ID.search(output)
    if not match:
        raise ProvisioningError(f"could not read System ID from aos-prov output:\n{output}")
    return match.group(1)


_ONLINE_TIMEOUT_S = 900
_ONLINE_POLL_S = 15
IDENTITY_SERVICE = "aos-iam"


def identity_service_condition(vm: VM, *, retry_start: bool = False) -> str:
    """Whether the post-provisioning identity service is allowed to run.

    The image ships a mirrored pair: aos-iam-prov runs only while the unit is
    unprovisioned and accepts registration, while aos-iam runs only once
    /var/aos/.provisionstate exists. Reading the condition result is a positive
    way to show which side of that boundary the unit is on - "the service is
    not running" on its own would not distinguish a refusal from a crash.
    """
    if retry_start:
        # ConditionResult reports the last start attempt, not the condition as
        # it stands now, so after the provisioning state changes systemd has to
        # be asked to start the service again before the answer means anything.
        # A held-back start still exits 0; the outcome is in the condition.
        vm.exec(f"systemctl start {IDENTITY_SERVICE}", timeout=120)
    return vm.exec(
        f"systemctl show {IDENTITY_SERVICE} -p ConditionResult --value"
    ).text()


def wait_until_online(cloud, system_uid: str, timeout_s: int = _ONLINE_TIMEOUT_S) -> bool:
    """Wait for the cloud to report the freshly registered unit as Online."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        record = cloud.find_unit(system_uid)
        if record and record.get("online_status") == "Online":
            return True
        time.sleep(_ONLINE_POLL_S)
    return False


def wait_until_offline(cloud, system_uid: str, timeout_s: int = _ONLINE_TIMEOUT_S) -> bool:
    """Wait for the cloud to stop seeing the unit as Online.

    The tenant refuses to release a unit that is still online, so this has to
    settle before the record can be deleted.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        record = cloud.find_unit(system_uid)
        if record is None or record.get("online_status") != "Online":
            return True
        time.sleep(_ONLINE_POLL_S)
    return False


def is_provisioned(vm: VM) -> bool:
    """Whether the unit currently holds a provisioning state."""
    return vm.exec("test -f /var/aos/.provisionstate").ok


def local_deprovision(vm: VM) -> None:
    """Run the unit's own deprovision path: clear disks, drop the state flag.

    Mirrors /opt/aos/deprovision.sh deterministically - the shipped 'async'
    mode backgrounds itself and skips clear_disks.
    """
    steps = (
        "systemctl stop -- $(systemctl show -p Wants aos.target | cut -d= -f2) || true",
        "/opt/aos/deprovision.sh || true",
        "rm -f /var/aos/.provisionstate",
        "nft delete table inet aos-provfw 2>/dev/null || true",
        "sync",
    )
    for step in steps:
        try:
            vm.exec(step, timeout=600)
        except VMError as error:
            _LOG.warning("deprovision step %r reported: %s", step, error)
