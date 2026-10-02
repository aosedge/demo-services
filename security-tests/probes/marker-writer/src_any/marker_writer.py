"""Probe payload: write a known marker into instance storage and stay alive.

The suite then checks that the marker is readable through the filesystem but
absent in plaintext from the underlying block device.
"""
from __future__ import annotations

import os
import pathlib
import time

STORAGE = pathlib.Path("/storage")
MARKER_ENV = "AOS_SEC_MARKER"
REPEATS = 64


def main() -> None:
    marker = os.environ.get(MARKER_ENV, "")
    if not marker:
        raise SystemExit(f"{MARKER_ENV} is not set; nothing to write")

    STORAGE.mkdir(parents=True, exist_ok=True)
    instance = os.environ.get("AOS_INSTANCE_ID", "instance")
    target = STORAGE / f"{instance}.marker"

    # Repeat the marker so the search across the raw device is not defeated by
    # a single unlucky block boundary.
    target.write_text("\n".join([marker] * REPEATS) + "\n", encoding="utf-8")
    os.sync()
    print(f"marker written to {target}", flush=True)

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
