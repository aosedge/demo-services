"""Local QEMU target: an AosCore VM on the machine running the suite.

Chosen over the shipped aos_vm.sh because that script presents the disk as
virtio-scsi while the v6.1.1 image's initramfs expects NVMe, and because it
requires root, creates a fixed 10.0.0.1/24 bridge and hardcodes KVM.
"""
from __future__ import annotations

import pathlib

from ..helpers.vm import VM


class LocalUbuntuTarget:
    """AosCore VM under QEMU with user-mode networking."""

    name = "local-ubuntu"

    def __init__(self, config) -> None:
        self._cfg = config
        self._vm = VM(config, pathlib.Path(config.suite_root) / ".run")

    def start(self) -> VM:
        self._vm.start()
        return self._vm

    def stop(self) -> None:
        self._vm.stop()

    def supports_hardware_tee(self) -> bool:
        """QEMU has no secure element: AosCore falls back to SoftHSM here."""
        return False
