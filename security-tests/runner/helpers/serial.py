"""Command execution over the unit's serial console.

SSH is not usable as the suite's transport: the published qemux86-64 image
starts no SSH server at all (`systemctl is-active sshd ssh dropbear` reports
`inactive inactive inactive` on a freshly booted unit). The serial console is
always present, needs no service inside the guest and works before the unit is
provisioned, so it is the only channel that covers every state the tests need.
This is also how the platform's own tooling reaches these VMs.
"""
from __future__ import annotations

import logging
import re
import secrets
import socket
import time

_LOG = logging.getLogger("security-tests.serial")
_ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")
_PROMPT = b"# "
_LOGIN_PROMPT = b"login:"
_PASSWORD_PROMPT = b"assword"
_RECV = 65536


class SerialError(RuntimeError):
    """Raised when the console cannot be driven to a usable shell."""


class SerialConsole:
    """A logged-in root shell reached through the QEMU serial socket."""

    def __init__(self, socket_path: str, user: str, password: str) -> None:
        self._path = socket_path
        self._user = user
        self._password = password
        self._sock: "socket.socket | None" = None
        self._buffer = b""

    # ---------------------------------------------------------------- plumbing

    def _connect(self) -> None:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(1.0)
        self._sock.connect(self._path)

    def _pump(self, seconds: float) -> bytes:
        """Drain the console for a while and return everything seen so far."""
        if self._sock is None:
            raise SerialError("console is not connected")
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                chunk = self._sock.recv(_RECV)
                if chunk:
                    self._buffer += chunk
            except socket.timeout:
                continue
        return _ANSI.sub(b"", self._buffer)

    def _wait_for(self, needles: "tuple[bytes, ...]", timeout: float) -> "bytes | None":
        if self._sock is None:
            raise SerialError("console is not connected")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                chunk = self._sock.recv(_RECV)
                if chunk:
                    self._buffer += chunk
            except socket.timeout:
                pass
            clean = _ANSI.sub(b"", self._buffer)
            for needle in needles:
                if needle in clean:
                    return needle
        return None

    def _send(self, text: str) -> None:
        if self._sock is None:
            raise SerialError("console is not connected")
        self._sock.sendall(text.encode())

    # ---------------------------------------------------------------- session

    def open(self, timeout: float = 900.0) -> None:
        """Connect and log in, waiting for the unit to finish booting."""
        self._connect()
        deadline = time.time() + timeout
        while time.time() < deadline:
            self._buffer = b""
            self._send("\n")
            seen = self._wait_for((_PROMPT, _LOGIN_PROMPT), 20)
            if seen == _PROMPT:
                _LOG.info("serial console already at a shell prompt")
                return
            if seen == _LOGIN_PROMPT and self._login():
                _LOG.info("logged in over the serial console")
                return
        raise SerialError(f"no shell on the serial console within {timeout}s")

    def _login(self) -> bool:
        """Answer the getty prompts. Paced deliberately: getty drops input
        typed before it is ready and then times the attempt out."""
        self._buffer = b""
        self._send(self._user + "\n")
        time.sleep(3)
        if self._wait_for((_PASSWORD_PROMPT,), 30) is None:
            return False
        time.sleep(2)
        self._send(self._password + "\n")
        time.sleep(5)
        return self._wait_for((_PROMPT,), 60) is not None

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    # ---------------------------------------------------------------- commands

    def run(self, command: str, timeout: float = 300.0) -> "tuple[int, str]":
        """Run one command; return its exit status and combined output.

        Output is delimited by per-call random markers. The echoed command line
        contains the markers too, so the payload is taken from the *last*
        opening marker onwards.
        """
        token = secrets.token_hex(6).upper()
        begin, end = f"__B{token}__", f"__E{token}__"
        self._buffer = b""
        self._send(f"echo {begin}; {command}; echo {end}$?\n")

        if self._wait_for((end.encode(),), timeout) is None:
            raise SerialError(f"command timed out after {timeout}s: {command}")
        # Let the trailing status digits arrive.
        text = self._pump(0.5).decode(errors="replace")

        start = text.rfind(begin)
        stop = text.find(end, start + len(begin))
        if start < 0 or stop < 0:
            raise SerialError(f"could not delimit output of: {command}")
        payload = text[start + len(begin):stop].strip("\r\n")
        status_text = text[stop + len(end):].strip().split()
        status = int(status_text[0]) if status_text and status_text[0].isdigit() else 0
        cleaned = "\n".join(line.rstrip("\r") for line in payload.splitlines())
        return status, cleaned
