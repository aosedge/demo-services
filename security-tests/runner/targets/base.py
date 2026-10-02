"""Target abstraction so the same tests can run against different platforms."""
from __future__ import annotations

from typing import Protocol

from ..helpers.vm import VM


class Target(Protocol):
    """What every target must provide to the tests."""

    name: str

    def start(self) -> VM:
        """Bring the unit up and return a handle for in-unit commands."""

    def stop(self) -> None:
        """Release everything this target allocated."""

    def supports_hardware_tee(self) -> bool:
        """Whether key material is held in a hardware TEE on this target.

        False on emulated targets. The suite uses this to decide whether the
        key-protection property can be *proven* or only *stated*.
        """
