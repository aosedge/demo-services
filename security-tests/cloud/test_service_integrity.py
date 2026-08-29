"""Test C - only signed, validated services are installed. SPECIFICATION ONLY.

Status: specified, not implemented, and deliberately narrower than the first
draft.

C1  correctly signed service, attached to unit-set + subject -> delivered, instance
    active, manifest digest present
C2  payload modified after signing                           -> rejected at verification,
    instance does not start
C3  unsigned payload                                         -> rejected
C5  unit outside the validation unit-set                     -> not delivered; assert
    positively that the desired status arrives *without* the service, because
    "nothing happened" is a poor pass criterion for a reader

Removed from v1
---------------
C4 (downgrade / anti-rollback) is NOT part of this suite. What we observed is
cloud-side semver ordering - switching a pre-release label can make the cloud
keep offering the older version. That is not evidence that the *unit* refuses a
rollback, and no unit-side anti-rollback requirement has been confirmed.
Claiming it here would be an overstatement; it returns only once the
requirement is established.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.cloud, pytest.mark.skip(reason="Test C is specified, not yet implemented")]


def test_c1_signed_service_is_installed() -> None:
    """A correctly signed service is delivered and runs."""


def test_c2_tampered_payload_is_rejected() -> None:
    """A payload altered after signing fails verification."""


def test_c3_unsigned_payload_is_rejected() -> None:
    """An unsigned payload is not accepted."""


def test_c5_service_not_delivered_outside_validation_unit_set() -> None:
    """Without the unit-set binding the desired status carries no service."""
