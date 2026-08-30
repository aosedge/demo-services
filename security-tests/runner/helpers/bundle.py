"""Build signed, repacked and deliberately tampered deployment bundles.

`aos-signer sign` produces an outer archive holding the payload archive, the
rendered config and a detached signature over them. Splitting sign from upload
lets the suite alter the payload *after* signing and offer the result to the
cloud, which is what an integrity check has to do to mean anything.

Every tampered upload is paired with a control that is unpacked and repacked
identically but left unmodified. Without that control a rejection would only
show that the cloud disliked something about a rebuilt archive; with it, the
single difference between the accepted and the rejected upload is the change to
the payload.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import tarfile

_SIGN_TIMEOUT_S = 1800

PAYLOAD_MEMBER = "./src_any/marker_writer.py"


class BundleError(RuntimeError):
    """Raised when a bundle cannot be built or offered to the cloud."""


def _run(args: list[str], cwd: pathlib.Path) -> str:
    completed = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=_SIGN_TIMEOUT_S, check=False
    )
    output = completed.stdout + completed.stderr
    if completed.returncode != 0:
        raise BundleError(f"{args[0]} failed ({completed.returncode}):\n{output}")
    return output


def stage(source: pathlib.Path, work_dir: pathlib.Path, name: str, *,
          version: str, marker: str, codename: str, sp_p12: pathlib.Path) -> pathlib.Path:
    """Render a probe folder ready for signing."""
    staged = work_dir / name
    if staged.exists():
        shutil.rmtree(staged)
    shutil.copytree(source, staged)
    template = (staged / "config.yaml.in").read_text(encoding="utf-8")
    rendered = (
        template.replace("@VERSION@", version)
        .replace("@MARKER@", marker)
        .replace("@CODENAME@", codename)
    )
    (staged / "config.yaml").write_text(rendered, encoding="utf-8")
    (staged / "config.yaml.in").unlink()
    if not sp_p12.is_file():
        raise BundleError(f"service-provider certificate not found: {sp_p12}")
    shutil.copy2(sp_p12, staged / "aos-user-sp.p12")
    return staged


def sign(folder: pathlib.Path) -> pathlib.Path:
    """Sign the staged folder and return the produced bundle."""
    _run(["aos-signer", "sign"], folder)
    bundle = folder / "batch.tar.gz"
    if not bundle.is_file():
        raise BundleError(f"aos-signer produced no bundle in {folder}")
    return bundle


def _repack(bundle: pathlib.Path, mutate_payload: bool) -> None:
    """Unpack the signed bundle and rebuild it, optionally altering the payload.

    The detached signature is carried over untouched, so a mutated payload no
    longer matches what was signed.
    """
    work = bundle.parent / "unpacked"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    with tarfile.open(bundle, "r:gz") as archive:
        archive.extractall(work)  # noqa: S202 - archive we produced ourselves

    if mutate_payload:
        inner = work / "inner"
        inner.mkdir()
        payload = work / "batch.tar.gz"
        with tarfile.open(payload, "r:gz") as archive:
            archive.extractall(inner)  # noqa: S202 - archive we produced ourselves
        target = inner / "src_any" / "marker_writer.py"
        target.write_text(
            target.read_text(encoding="utf-8") + "\n# modified after signing\n",
            encoding="utf-8",
        )
        with tarfile.open(payload, "w:gz") as archive:
            archive.add(inner, arcname=".")
        shutil.rmtree(inner)

    with tarfile.open(bundle, "w:gz") as archive:
        archive.add(work, arcname=".")
    shutil.rmtree(work)


def repack_unchanged(bundle: pathlib.Path) -> None:
    """Rebuild the bundle byte-for-byte equivalent in content. The control."""
    _repack(bundle, mutate_payload=False)


def repack_tampered(bundle: pathlib.Path) -> None:
    """Rebuild the bundle with the payload altered after signing."""
    _repack(bundle, mutate_payload=True)


def upload(folder: pathlib.Path, sp_p12: pathlib.Path) -> str:
    """Offer the bundle in *folder* to the cloud and return the CLI output.

    A non-zero exit is an error, but a zero exit is not proof of acceptance:
    the cloud validates the signature after the transfer, so the caller must
    confirm the outcome by looking at the service's versions.
    """
    return _run(["aos-signer", "upload", "-p", str(sp_p12)], folder)
