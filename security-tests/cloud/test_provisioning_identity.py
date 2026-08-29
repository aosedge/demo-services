"""Test B - a unit is only served after valid provisioning. SPECIFICATION ONLY.

Status: specified, not implemented. Written up here so the scope and the exact
assertions can be reviewed before the code is committed; test A is the
reference implementation for the fixtures these will reuse.

B1  unprovisioned unit, cloud operations on its behalf      -> refused / unit unknown
B2  unprovisioned unit                                      -> provisioning ports 8089/8090 open
B3  provision with a valid OEM PKCS#12                      -> unit becomes provisioned and Online
B4  after provisioning                                      -> provisioning ports closed
    (AosCore drops the nft table aos-provfw once registered)
B5  Cloud API v11 request without a valid client certificate -> TLS refusal, mTLS is mandatory
B6  after deprovision + delete                              -> operations on its behalf refused
B6a locally provisioned but unknown to the cloud            -> orphaned identity is not served
    (observed in recon: a VM kept /var/aos/.provisionstate after its cloud
     record had been deleted)

All of B needs live cloud interaction; none of it can run offline.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.cloud, pytest.mark.skip(reason="Test B is specified, not yet implemented")]


def test_b1_unprovisioned_unit_is_not_served() -> None:
    """Cloud refuses to act for a unit it has never registered."""


def test_b2_provisioning_ports_open_before_registration() -> None:
    """The provisioning interface is reachable only before registration."""


def test_b3_provisioning_with_valid_oem_certificate() -> None:
    """A valid OEM identity registers the unit and brings it Online."""


def test_b4_provisioning_ports_closed_after_registration() -> None:
    """Once registered, the provisioning interface is closed."""


def test_b5_mtls_is_mandatory() -> None:
    """Cloud API rejects requests without a valid client certificate."""


def test_b6_deprovisioned_unit_is_refused() -> None:
    """A released unit loses access."""
