"""Configuration loading. Every value comes from config.env - nothing is baked in."""
from __future__ import annotations

import os
import pathlib
import time
from dataclasses import dataclass

_DEFAULTS: dict[str, str] = {
    "AOS_TARGET": "local-ubuntu",
    "AOS_VM_ACCEL": "kvm",
    "AOS_VM_DISK_IF": "scsi",
    "AOS_VM_CPUS": "4",
    "AOS_VM_MEM": "4G",
    "AOS_VM_SSH_PORT": "12222",
    "AOS_VM_PROV_PORT": "18089",
    "AOS_VM_EXTRA_PORT": "18090",
    "AOS_VM_SSH_USER": "root",
    "AOS_VM_SSH_PASSWORD": "Password1",
    "AOS_VM_BIOS": "/usr/share/ovmf/OVMF.fd",
    "AOS_PROBE_VERSION": "auto",
}


class ConfigError(RuntimeError):
    """Raised when the suite is not configured well enough to run."""


def _read_env_file(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Config:
    """Everything the runner needs, resolved once."""

    target: str
    cloud_api: str
    oem_p12: pathlib.Path
    sp_p12: pathlib.Path
    unit_set_id: str
    subject_id: str
    vm_image: pathlib.Path
    vm_image_sha256: str
    vm_accel: str
    vm_disk_if: str
    vm_cpus: str
    vm_mem: str
    ssh_port: int
    prov_port: int
    extra_port: int
    ssh_user: str
    ssh_password: str
    bios: str
    probe_version: str
    suite_root: pathlib.Path

    def require_cloud(self) -> None:
        """Fail early and clearly when cloud credentials are absent."""
        missing = [
            name
            for name, value in (
                ("AOS_CLOUD_API", self.cloud_api),
                ("AOS_OEM_P12", str(self.oem_p12)),
                ("AOS_UNIT_SET_ID", self.unit_set_id),
                ("AOS_SUBJECT_ID", self.subject_id),
            )
            if not value or value.startswith("<")
        ]
        if missing:
            raise ConfigError(
                "AosCloud settings are incomplete: "
                + ", ".join(missing)
                + ". There is no offline mode - see the README section"
                + " 'Why the cloud is always needed'."
            )
        if not self.oem_p12.is_file():
            raise ConfigError(f"OEM certificate not found: {self.oem_p12}")


def _resolve_probe_version(value: str) -> str:
    """Give every run its own service version.

    Re-uploading an existing version does not replace the bundle already held
    by the cloud, so a fixed version would silently keep deploying the first
    payload ever published - with the first run's marker inside it. The
    pre-release label is kept and only its number grows, because the cloud
    compares pre-release identifiers alphanumerically and would otherwise go on
    offering the older version.
    """
    if value and value != "auto":
        return value
    return f"1.0.0-rc.{int(time.time())}"


def load(suite_root: pathlib.Path | None = None) -> Config:
    """Load config.env, overlaid by real environment variables."""
    root = suite_root or pathlib.Path(__file__).resolve().parent.parent
    values = dict(_DEFAULTS)
    values.update(_read_env_file(root / "config.env"))
    for key in [
        *values,
        "AOS_CLOUD_API",
        "AOS_OEM_P12",
        "AOS_SP_P12",
        "AOS_UNIT_SET_ID",
        "AOS_SUBJECT_ID",
        "AOS_VM_IMAGE",
        "AOS_VM_IMAGE_SHA256",
    ]:
        if os.environ.get(key):
            values[key] = os.environ[key]

    return Config(
        target=values.get("AOS_TARGET", "local-ubuntu"),
        cloud_api=values.get("AOS_CLOUD_API", ""),
        oem_p12=pathlib.Path(values.get("AOS_OEM_P12", "")),
        sp_p12=pathlib.Path(values.get("AOS_SP_P12", "")),
        unit_set_id=values.get("AOS_UNIT_SET_ID", ""),
        subject_id=values.get("AOS_SUBJECT_ID", ""),
        vm_image=pathlib.Path(values.get("AOS_VM_IMAGE", "")),
        vm_image_sha256=values.get("AOS_VM_IMAGE_SHA256", ""),
        vm_accel=values["AOS_VM_ACCEL"],
        vm_disk_if=values["AOS_VM_DISK_IF"],
        vm_cpus=values["AOS_VM_CPUS"],
        vm_mem=values["AOS_VM_MEM"],
        ssh_port=int(values["AOS_VM_SSH_PORT"]),
        prov_port=int(values["AOS_VM_PROV_PORT"]),
        extra_port=int(values["AOS_VM_EXTRA_PORT"]),
        ssh_user=values["AOS_VM_SSH_USER"],
        ssh_password=values["AOS_VM_SSH_PASSWORD"],
        bios=values["AOS_VM_BIOS"],
        probe_version=_resolve_probe_version(values["AOS_PROBE_VERSION"]),
        suite_root=root,
    )
