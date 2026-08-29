"""Raw block-device inspection inside the guest.

The encryption check has to look at the device itself, not at the mounted
filesystem, so the search runs in the guest against /dev directly.

The device path is discovered rather than hardcoded: the qemux86-64 image
presents its disk as /dev/sda, while the genericx86-64 image of the same
release is NVMe-backed and appears as /dev/nvme0n1.
"""
from __future__ import annotations

from .vm import VM, VMError

_DATA_PARTITION_INDEX = 6


class DiskLayoutError(VMError):
    """Raised when the AosCore data partition cannot be located."""


def data_partition(vm: VM) -> str:
    """Return the path of the AosCore data partition on this unit."""
    root_source = vm.exec_ok("findmnt -no SOURCE /")
    parent = vm.exec_ok(f"lsblk -no PKNAME {root_source}").splitlines()
    disk = next((line.strip() for line in parent if line.strip()), "")
    if not disk:
        raise DiskLayoutError(f"could not determine the disk holding {root_source}")
    separator = "p" if disk.startswith("nvme") else ""
    return f"/dev/{disk}{separator}{_DATA_PARTITION_INDEX}"


def partition_fstype(vm: VM, device: "str | None" = None) -> str:
    """Filesystem type as the kernel sees it - 'crypto_LUKS' when encrypted."""
    target = device or data_partition(vm)
    return vm.exec(f"lsblk -no FSTYPE {target} 2>/dev/null | head -1").text()


def is_luks(vm: VM, device: "str | None" = None) -> bool:
    """Authoritative LUKS check via cryptsetup."""
    target = device or data_partition(vm)
    return vm.exec(f"cryptsetup isLuks {target}").ok


def volume_group_present(vm: VM, name: str = "aosvg") -> bool:
    """Whether the AosCore volume group is currently assembled."""
    return name in vm.exec("vgs --noheadings -o vg_name 2>/dev/null").text()


def marker_occurrences(vm: VM, marker: str, device: "str | None" = None) -> int:
    """Count plaintext occurrences of the marker across the whole raw device.

    Streams the device through grep inside the guest so no multi-gigabyte
    transfer happens, and counts every match rather than stopping at the first.
    """
    target = device or data_partition(vm)
    command = f"dd if={target} bs=1M status=none | grep -a -c -- {marker!r} || true"
    text = vm.exec(command, timeout=3600).text()
    last = text.splitlines()[-1].strip() if text else "0"
    return int(last) if last.isdigit() else 0
