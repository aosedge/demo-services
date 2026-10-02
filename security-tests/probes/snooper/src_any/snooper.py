"""Probe payload: try to reach things outside this instance and record what happened.

Writes a JSON report into its own storage. Every attempt records the exact
errno, so the test can tell a refusal apart from a path that simply was not
there - "not found" would prove nothing on its own, which is why the test also
confirms from the unit side that each target does exist.
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
import pathlib
import stat
import time

STORAGE = pathlib.Path("/storage")
TARGETS_ENV = "AOS_SEC_TARGETS"
MARKER_ENV = "AOS_SEC_MARKER"


def _attempt_read(path: str) -> dict[str, object]:
    """Try to read a path and describe the outcome precisely."""
    try:
        with open(path, "rb") as handle:
            handle.read(64)
    except OSError as error:
        return {"path": path, "read": False, "errno": error.errno,
                "error": type(error).__name__}
    return {"path": path, "read": True, "errno": None, "error": ""}


def _attempt_list(path: str) -> dict[str, object]:
    """Try to list a directory and describe the outcome precisely."""
    try:
        entries = os.listdir(path)
    except OSError as error:
        return {"path": path, "listed": False, "errno": error.errno,
                "error": type(error).__name__}
    return {"path": path, "listed": True, "errno": None, "entries": len(entries)}


MS_REMOUNT = 32
_KERNEL_REFUSAL = (errno.EPERM, errno.EACCES, errno.EROFS)


def _remount_root_writable() -> None:
    """Ask the kernel directly to remount / read-write."""
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    if libc.mount(b"none", b"/", None, MS_REMOUNT, None) != 0:
        raise OSError(ctypes.get_errno(), "mount(MS_REMOUNT) refused")


def _privileges() -> dict[str, str]:
    """Read what the kernel says this process is actually allowed to do."""
    wanted = ("CapEff", "CapPrm", "CapBnd", "NoNewPrivs", "Seccomp")
    found = {}
    try:
        for line in pathlib.Path("/proc/self/status").read_text().splitlines():
            key, _, value = line.partition(":")
            if key in wanted:
                found[key] = value.strip()
    except OSError as error:
        found["error"] = str(error)
    return found


def _attempt_escalation() -> list[dict[str, object]]:
    """Try to take the unit's root identity.

    These go straight to the kernel rather than through su or sudo: a missing
    helper binary would fail for reasons that say nothing about isolation,
    whereas a syscall always fails because something refused it, and its errno
    names who. Attempts that fail merely because nothing was there are marked
    as such and are not treated as evidence by the test.
    """
    results: list[dict[str, object]] = []

    def record(name: str, action) -> None:
        entry: dict[str, object] = {"attempt": name}
        try:
            action()
        except OSError as error:
            code = error.errno
            entry.update(
                succeeded=False,
                errno=code,
                error=errno.errorcode.get(code, str(code)) if code is not None else str(error),
                kernel_refusal=code in _KERNEL_REFUSAL,
            )
        except Exception as error:  # any failure to escalate is still a failure
            entry.update(succeeded=False, errno=None,
                         error=f"{type(error).__name__}: {error}"[:120],
                         kernel_refusal=False)
        else:
            entry.update(succeeded=True, errno=None, error="", kernel_refusal=False)
        results.append(entry)

    # Ordered so that nothing here can change what the later attempts run as.
    record("chroot-/", lambda: os.chroot("/"))
    record("mknod-char-device",
           lambda: os.mknod(str(STORAGE / "esc.dev"),
                            0o600 | stat.S_IFCHR, os.makedev(1, 3)))
    record("write-into-unit-/etc",
           lambda: open("/etc/aos-escalation-probe", "wb").close())
    record("remount-root-rw", _remount_root_writable)
    record("setgid-0", lambda: os.setgid(0))
    record("setuid-0", lambda: os.setuid(0))
    return results


def main() -> None:
    marker = os.environ.get(MARKER_ENV, "")
    targets = [t for t in os.environ.get(TARGETS_ENV, "").split(":") if t]
    instance = os.environ.get("AOS_INSTANCE_ID", "instance")

    STORAGE.mkdir(parents=True, exist_ok=True)
    own = STORAGE / f"{instance}.own"
    own.write_text(marker, encoding="utf-8")

    report = {
        "marker": marker,
        "instance": instance,
        "uid": os.getuid(),
        "gid": os.getgid(),
        "own_file_readable": own.read_text(encoding="utf-8") == marker,
        "reads": [_attempt_read(t) for t in targets],
        "lists": [_attempt_list(t) for t in targets],
        "escalation": _attempt_escalation(),
        "privileges": _privileges(),
    }
    (STORAGE / f"{instance}.snoop").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    os.sync()
    print("snooper report written", flush=True)

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
