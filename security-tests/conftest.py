"""Top-level fixtures for the security test suite."""
from __future__ import annotations

import pathlib
import secrets
import sys

import pytest

_SUITE_ROOT = pathlib.Path(__file__).resolve().parent
if str(_SUITE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SUITE_ROOT))

from runner import config as config_module  # noqa: E402
from runner.cloud_client import CloudClient  # noqa: E402
from runner.targets.aws import AWSTarget  # noqa: E402
from runner.targets.local_ubuntu import LocalUbuntuTarget  # noqa: E402

_TARGETS = {"local-ubuntu": LocalUbuntuTarget, "aws": AWSTarget}


@pytest.fixture(scope="session")
def config():
    """Suite configuration, loaded once."""
    return config_module.load(_SUITE_ROOT)


@pytest.fixture(scope="session")
def target(config):
    """The selected execution target."""
    try:
        factory = _TARGETS[config.target]
    except KeyError as error:
        raise config_module.ConfigError(
            f"unknown AOS_TARGET {config.target!r}; expected one of {sorted(_TARGETS)}"
        ) from error
    return factory(config)


@pytest.fixture(scope="session")
def ca_bundle(config):
    """Root CAs shipped with aos-keys, needed to verify the cloud endpoint."""
    import aos_keys.files  # noqa: PLC0415 - optional dependency, resolved lazily

    directory = pathlib.Path(aos_keys.files.__path__[0])
    bundle = config.suite_root / ".run" / "aos-ca.pem"
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_bytes(
        b"".join((directory / name).read_bytes()
                 for name in ("aos_root_ca_1.crt", "aos_root_ca_2.crt"))
    )
    return bundle


@pytest.fixture(scope="session")
def cloud(config, ca_bundle):
    """Authenticated AosCloud client."""
    config.require_cloud()
    return CloudClient(config.cloud_api, config.oem_p12, ca_bundle)


@pytest.fixture(scope="session")
def marker():
    """A fresh random marker per run, so a stale one cannot make a test pass."""
    return "AOSSECMARK" + secrets.token_hex(16).upper()
