"""AWS EC2 target - phase 2 placeholder.

Deliberately unimplemented: it is listed so the target selection and the
configuration surface are settled now, and so that choosing it fails loudly
instead of silently running against the wrong platform.
"""
from __future__ import annotations

from ..helpers.vm import VM


class AWSTarget:
    """AosCore VM on an AWS EC2 instance."""

    name = "aws"

    def __init__(self, config) -> None:
        self._cfg = config

    def start(self) -> VM:
        raise NotImplementedError(
            "The aws target is planned for phase 2. Set AOS_TARGET=local-ubuntu."
        )

    def stop(self) -> None:
        """Nothing is allocated until start() is implemented."""

    def supports_hardware_tee(self) -> bool:
        """EC2 exposes no OP-TEE either; key protection stays a stated guarantee."""
        return False
