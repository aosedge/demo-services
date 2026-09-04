"""Probe the cloud endpoint with, without and with the wrong client certificate.

A mutual-TLS check is only meaningful if the failures it observes are caused by
the certificate rather than by the endpoint being unreachable. Every probe here
is therefore run against the same URL in the same run as a successful call with
the real certificate, and the failures are classified: a refusal at the TLS
layer is evidence, a connection error is not.
"""
from __future__ import annotations

import datetime
import pathlib
import ssl
import tempfile
from dataclasses import dataclass

import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

_TIMEOUT = 60
_FIRST_ERROR_STATUS = 400


@dataclass(frozen=True)
class ProbeResult:
    """What happened when the endpoint was called with a given identity."""

    status: int | None
    error: str
    tls_refusal: bool

    @property
    def accepted(self) -> bool:
        return self.status is not None and self.status < _FIRST_ERROR_STATUS


def _classify(error: Exception) -> ProbeResult:
    """Separate a certificate refusal from any other failure."""
    if isinstance(error, requests.exceptions.SSLError):
        return ProbeResult(None, f"{type(error).__name__}: {error}", True)
    if isinstance(error, ssl.SSLError):
        return ProbeResult(None, f"{type(error).__name__}: {error}", True)
    return ProbeResult(None, f"{type(error).__name__}: {error}", False)


def describe(result: ProbeResult) -> str:
    """One phrase saying how the endpoint answered, for the report line."""
    if result.status is not None:
        return f"HTTP {result.status}"
    if result.tls_refusal:
        return "refused during the TLS handshake"
    return f"failed before reaching the endpoint: {result.error}"


def probe(url: str, ca_bundle: pathlib.Path, client_pem: str | None) -> ProbeResult:
    """Call *url* with the given client identity, or with none at all."""
    try:
        kwargs = {"cert": client_pem} if client_pem else {}
        response = requests.get(
            url, verify=str(ca_bundle), timeout=_TIMEOUT, **kwargs
        )
        return ProbeResult(response.status_code, "", False)
    except Exception as error:
        return _classify(error)


def make_untrusted_identity(directory: pathlib.Path) -> str:
    """Create a self-signed certificate the cloud has never seen.

    Used to show that the endpoint refuses an identity it does not trust, as
    opposed to merely requiring that some certificate be present.
    """
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "aos-security-tests-untrusted")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 - lifetime is the caller's
        dir=directory, prefix="untrusted-", suffix=".pem", delete=False
    )
    handle.write(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    handle.write(certificate.public_bytes(serialization.Encoding.PEM))
    handle.close()
    path = pathlib.Path(handle.name)
    path.chmod(0o600)
    return str(path)
