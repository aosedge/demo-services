"""VM lifecycle and in-guest command execution.

The VM is started by runner/bringup.sh - a direct QEMU invocation with
user-mode networking. It needs no root, creates no bridge, installs no package
and leaves no trace on the host beyond the process itself.

Commands run over the serial console rather than SSH: the published image
starts no SSH server, and the console is available before provisioning too.
See runner/helpers/serial.py.
"""
from __future__ import annotations

import hashlib
import logging
import pathlib
import shutil
import subprocess
import time
from dataclasses import dataclass

from .serial import SerialConsole, SerialError

_LOG = logging.getLogger("security-tests.vm")
_BOOT_TIMEOUT_S = 900
_SHUTDOWN_TIMEOUT_S = 180


class VMError(RuntimeError):
    """Raised when the VM cannot be brought to a usable state."""


@dataclass
class ExecResult:
    """Outcome of one in-guest command."""

    exit_status: int
    stdout: str

    @property
    def ok(self) -> bool:
        return self.exit_status == 0

    def text(self) -> str:
        return self.stdout.strip()


def sha256_of(path: pathlib.Path) -> str:
    """Hash a file in chunks; the VM image is too large to slurp."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class VM:
    """A running AosCore VM, driven through its serial console."""

    def __init__(self, config, work_dir: pathlib.Path) -> None:
        self._cfg = config
        self._work = work_dir
        self._disk = work_dir / "vm-main.qcow2"
        self._serial = work_dir / "serial.sock"
        self._process: "subprocess.Popen[bytes] | None" = None
        self._console: "SerialConsole | None" = None

    # ---------------------------------------------------------------- lifecycle

    def prepare_disk(self) -> None:
        """Take a private copy of the pinned image so the original stays pristine."""
        if not self._cfg.vm_image.is_file():
            raise VMError(f"VM image not found: {self._cfg.vm_image}")
        self._work.mkdir(parents=True, exist_ok=True)
        if not self._disk.exists():
            _LOG.info("copying VM image to %s", self._disk)
            shutil.copy2(self._cfg.vm_image, self._disk)

    def verify_image_hash(self) -> "tuple[bool, str]":
        """Check the configured image against its expected SHA-256."""
        if not self._cfg.vm_image_sha256:
            return False, "no AOS_VM_IMAGE_SHA256 configured"
        actual = sha256_of(self._cfg.vm_image)
        return actual == self._cfg.vm_image_sha256, actual

    def start(self) -> None:
        """Launch QEMU and wait until a shell answers on the serial console."""
        self.prepare_disk()
        self._serial.unlink(missing_ok=True)
        script = pathlib.Path(__file__).resolve().parent.parent / "bringup.sh"
        env = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "AOS_VM_DISK": str(self._disk),
            "AOS_VM_SERIAL": str(self._serial),
            "AOS_VM_ACCEL": self._cfg.vm_accel,
            "AOS_VM_DISK_IF": self._cfg.vm_disk_if,
            "AOS_VM_CPUS": self._cfg.vm_cpus,
            "AOS_VM_MEM": self._cfg.vm_mem,
            "AOS_VM_SSH_PORT": str(self._cfg.ssh_port),
            "AOS_VM_PROV_PORT": str(self._cfg.prov_port),
            "AOS_VM_EXTRA_PORT": str(self._cfg.extra_port),
            "AOS_VM_BIOS": self._cfg.bios,
            "AOS_VM_CONSOLE": str(self._work / "vm-console.log"),
        }
        _LOG.info("starting VM via %s", script)
        self._process = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
            ["/usr/bin/env", "bash", str(script)],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._await_socket()
        self._console = SerialConsole(
            str(self._serial), self._cfg.ssh_user, self._cfg.ssh_password
        )
        try:
            self._console.open(timeout=_BOOT_TIMEOUT_S)
        except SerialError as error:
            raise VMError(
                f"{error}. Check {self._work / 'vm-console.log'}: a guest stuck in its initramfs "
                f"waiting for a device means AOS_VM_DISK_IF does not match the image "
                f"(qemux86-64 needs 'scsi', genericx86-64 needs 'nvme')."
            ) from error

    def _await_socket(self, timeout: int = 60) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._serial.exists():
                return
            if self._process is not None and self._process.poll() is not None:
                raise VMError("QEMU exited before creating its serial socket")
            time.sleep(1)
        raise VMError(f"QEMU did not create {self._serial} within {timeout}s")

    def stop(self) -> None:
        """Ask the guest to power off, then make sure QEMU is gone."""
        try:
            if self._console is not None:
                self._console.run("systemctl poweroff", timeout=30)
        except (SerialError, VMError):
            _LOG.warning("graceful poweroff failed; terminating QEMU")
        finally:
            if self._console is not None:
                self._console.close()
                self._console = None

        if self._process is None:
            return
        deadline = time.time() + _SHUTDOWN_TIMEOUT_S
        while time.time() < deadline and self._process.poll() is None:
            time.sleep(2)
        if self._process.poll() is None:
            self._process.terminate()
            try:
                self._process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self._process.kill()
        self._serial.unlink(missing_ok=True)

    # ---------------------------------------------------------------- exec

    def exec(self, command: str, timeout: int = 300) -> ExecResult:
        """Run one command in the guest."""
        if self._console is None:
            raise VMError("VM is not started")
        try:
            status, output = self._console.run(command, timeout=timeout)
        except SerialError as error:
            raise VMError(f"in-guest command failed: {error}") from error
        return ExecResult(status, output)

    def exec_ok(self, command: str, timeout: int = 300) -> str:
        """Run a command that must succeed and return its output."""
        result = self.exec(command, timeout=timeout)
        if not result.ok:
            raise VMError(f"command {command!r} exited {result.exit_status}: {result.stdout}")
        return result.text()
