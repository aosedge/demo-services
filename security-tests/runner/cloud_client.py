"""Minimal AosCloud REST client (API v11) over mutual TLS.

Deliberately small and dependency-light: the suite must be auditable by the
customer running it. Credentials are read from the configured PKCS#12 bundle at
runtime and never written into the repository.
"""
from __future__ import annotations

import pathlib
import tempfile
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from cryptography.hazmat.primitives.serialization.pkcs12 import load_pkcs12

_TIMEOUT = 60


class CloudError(RuntimeError):
    """Raised when the cloud rejects a request the suite depends on."""


class CloudClient:
    """mTLS client for the handful of endpoints the suite needs."""

    def __init__(self, base_url: str, oem_p12: pathlib.Path, ca_bundle: pathlib.Path) -> None:
        self._base = base_url if base_url.endswith("/") else base_url + "/"
        self._ca = str(ca_bundle)
        self._pem = self._materialise_pem(oem_p12)

    @staticmethod
    def _materialise_pem(p12_path: pathlib.Path) -> str:
        """Convert PKCS#12 to a temporary PEM chain readable by requests."""
        bundle = load_pkcs12(p12_path.read_bytes(), None)
        if bundle.key is None or bundle.cert is None:
            raise CloudError(f"{p12_path} does not contain a key and certificate pair")
        parts = [
            bundle.key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()),
            bundle.cert.certificate.public_bytes(Encoding.PEM),
        ]
        parts += [extra.certificate.public_bytes(Encoding.PEM) for extra in bundle.additional_certs]
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 - lifetime is the client's
            prefix="aos-oem-", suffix=".pem", delete=False
        )
        handle.write(b"".join(parts))
        handle.close()
        pathlib.Path(handle.name).chmod(0o600)
        return handle.name

    def request(self, method: str, path: str, body: "Any | None" = None) -> requests.Response:
        """Issue one request; callers decide what an acceptable status is."""
        split = urlsplit(self._base + path.lstrip("/"))
        url = urlunsplit((split.scheme, split.netloc, split.path.rstrip("/") + "/", split.query, ""))
        kwargs: "dict[str, Any]" = {"json": body} if body is not None else {}
        return requests.request(
            method, url, cert=self._pem, verify=self._ca, timeout=_TIMEOUT, **kwargs
        )

    def get_json(self, path: str) -> "dict[str, Any]":
        response = self.request("GET", path)
        if response.status_code != 200:
            raise CloudError(f"GET {path} returned {response.status_code}")
        return response.json()

    # ---------------------------------------------------------------- units

    def find_unit(self, system_uid: str) -> "dict[str, Any] | None":
        """Return the cloud record for a system id, or None when unknown."""
        for item in self.get_json("units").get("items", []):
            if item.get("system_uid") == system_uid:
                return item
        return None

    def attach_unit(self, system_uid: str, unit_set_id: str, subject_id: str) -> None:
        """Attach a unit to the validation unit-set and to the test subject.

        Both bindings are sub-resource POSTs keyed by *system id*. A PATCH on
        units/{id} answers 200 and silently does not apply - do not use it.
        """
        for path in (f"unit-sets/{unit_set_id}/units", f"subjects/{subject_id}/units"):
            response = self.request("POST", path, {"system_uids": [system_uid]})
            if response.status_code not in (200, 201):
                raise CloudError(f"POST {path} returned {response.status_code}: {response.text}")

    def detach_unit(self, system_uid: str, subject_id: str) -> None:
        """Detach the unit from the test subject (idempotent)."""
        self.request("DELETE", f"subjects/{subject_id}/units/{system_uid}")

    # ---------------------------------------------------------------- services

    def find_service_id(self, codename: str) -> "str | None":
        """Cloud id of a published service, by codename."""
        for item in self.get_json("services").get("items", []):
            if item.get("codename") == codename:
                return str(item["id"])
        return None

    def attach_service_to_subject(self, subject_id: str, service_id: str) -> None:
        """Make the subject offer this service. Idempotent."""
        response = self.request(
            "POST", f"subjects/{subject_id}/services", {"service_ids": [service_id]}
        )
        if response.status_code not in (200, 201, 409):
            raise CloudError(
                f"attaching service {service_id} to subject {subject_id} returned "
                f"{response.status_code}: {response.text}"
            )

    def deprovision_and_delete(self, unit_uid: str) -> None:
        """Release the cloud-side unit record. The unit must be offline first."""
        self.request("DELETE", f"units/{unit_uid}/deprovision")
        self.request("DELETE", f"units/{unit_uid}")
