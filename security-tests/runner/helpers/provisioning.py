"""Unit provisioning and local deprovisioning.

Provisioning uses the public aos-prov CLI. Local deprovisioning uses the
on-unit script that ships with AosCore, so the suite exercises the documented
path rather than a bespoke one.
"""
from __future__ import annotations

import logging
import re
import subprocess

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
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        command, capture_output=True, text=True, timeout=3600, check=False
    )
    output = completed.stdout + completed.stderr
    if completed.returncode != 0:
        raise ProvisioningError(f"aos-prov failed ({completed.returncode}):\n{output}")
    match = _SYSTEM_ID.search(output)
    if not match:
        raise ProvisioningError(f"could not read System ID from aos-prov output:\n{output}")
    return match.group(1)


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
