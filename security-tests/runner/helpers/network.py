"""Make the unit able to resolve names, and say so out loud.

The published image is built for the topology the platform's own tooling
creates: a host bridge at 10.0.0.1 that is both the gateway and the DNS server
(`/etc/systemd/network/*.network` sets `Gateway=10.0.0.1`, `DNS=10.0.0.100`
and `DNS=10.0.0.1`).

This suite boots the unit behind QEMU user-mode networking instead, so that it
needs no root, no bridge and no change to the host. That topology routes fine -
the gateway answers and the default route is correct - but it places its DNS
forwarder on a different address than the gateway, so the unit's configured
resolvers cannot answer. Without a fix the unit provisions but never reaches
the cloud, and stays Offline forever.

Pointing the unit's resolver at the emulated network's DNS is environment
setup, on a par with creating the bridge the other topology would need. It
changes nothing the tests assert about, and it is logged so a reader can see
exactly what was done to the unit.
"""
from __future__ import annotations

import logging

from .report import note
from .vm import VM

_LOG = logging.getLogger("security-tests.network")

# QEMU user-mode networking puts its DNS forwarder on the third address of the
# emulated subnet, while the gateway sits on the first.
EMULATED_DNS = "10.0.0.3"
_LINK = "enp0s3"


class NetworkError(RuntimeError):
    """Raised when the unit cannot resolve the cloud endpoint."""


def can_resolve(vm: VM, host: str) -> bool:
    """Whether the unit resolves a name with its current configuration."""
    return vm.exec(f"nslookup {host} >/dev/null 2>&1", timeout=90).ok


def ensure_name_resolution(vm: VM, host: str, dns: str = EMULATED_DNS) -> str | None:
    """Guarantee the unit can resolve *host*; return a note when it was changed.

    Returns None when nothing had to be done, so a bridged or otherwise
    correctly served topology is left completely untouched.
    """
    if can_resolve(vm, host):
        _LOG.info("unit resolves %s with its own configuration", host)
        return None

    # resolvectl can report a d-bus timeout on a slow emulated unit while still
    # applying the setting, so the result is judged by whether resolution works
    # afterwards rather than by its exit status.
    vm.exec(f"resolvectl dns {_LINK} {dns}", timeout=120)
    if not can_resolve(vm, host):
        raise NetworkError(
            f"unit cannot resolve {host} even after pointing {_LINK} at {dns}. "
            "The image expects its gateway to serve DNS; see runner/helpers/network.py."
        )
    return note(
        "environment",
        f"the unit could not resolve {host}: the image expects its gateway to serve DNS, which "
        f"user-mode networking does not. Its resolver was pointed at the emulated network's DNS "
        f"({dns}). This is environment setup and affects nothing the tests assert about",
    )
