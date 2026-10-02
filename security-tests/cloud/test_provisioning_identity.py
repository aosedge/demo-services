"""Test B - a unit is served only after registration, and only over mutual TLS.

No refusal is inferred from silence: the tenant is asked directly whether it
knows the unit, the identity service reports its own start condition, and every
mTLS refusal is paired with a successful call using the real certificate, so a
refusal cannot be mistaken for an unreachable endpoint.
"""
from __future__ import annotations

import pytest

from runner.helpers import mtls, provisioning, report

pytestmark = pytest.mark.cloud

CHECK_B1 = "B1 unregistered-unit-not-served"
CHECK_B2 = "B2 registered-unit-is-served"
CHECK_B3 = "B3 mtls-required"
CHECK_B4 = "B4 released-unit-not-served"


def test_b1_unregistered_unit_is_not_served(provisioned_unit, record_property):
    """Before registration the tenant does not know the unit, and its identity
    service is held back by its start condition."""
    system_uid = provisioned_unit["system_uid"]
    known_before = provisioned_unit["known_system_uids_before"]
    condition = provisioned_unit["iam_condition_before"]

    assert system_uid not in known_before, report.failed(
        CHECK_B1, f"the tenant already knew system id {system_uid} before it was registered"
    )
    assert condition == "no", report.failed(
        CHECK_B1,
        f"the identity service condition was {condition!r} on an unregistered unit; expected it "
        "to be held back",
    )
    record_property(
        CHECK_B1,
        report.passed(
            CHECK_B1,
            "before registration the cloud does not know this unit and its identity service is "
            "held back by its start condition - it cannot act on the tenant's behalf",
        ),
    )


def test_b2_registered_unit_is_served(provisioned_unit, record_property):
    """After registration the unit exists in the tenant, comes Online, and its
    identity service is allowed to run."""
    record = provisioned_unit["record_after_provisioning"]
    assert record, report.failed(
        CHECK_B2, "the unit was not present in the tenant after provisioning"
    )
    assert record.get("status") == "provisioned", report.failed(
        CHECK_B2, f"unit status after provisioning was {record.get('status')!r}"
    )
    assert provisioned_unit["online"], report.failed(
        CHECK_B2, "the unit never reported Online after provisioning"
    )
    assert provisioned_unit["iam_condition_after"] == "yes", report.failed(
        CHECK_B2, "the identity service remained held back after provisioning"
    )
    record_property(
        CHECK_B2,
        report.passed(
            CHECK_B2,
            "after registration with a valid OEM identity the unit is known to the tenant, "
            "reports Online, and its identity service is permitted to run",
        ),
    )


def test_b3_mutual_tls_is_required(config, cloud, ca_bundle, tmp_path, record_property):
    """The endpoint refuses callers without a trusted client certificate.

    Three calls to the same URL in the same run: with the real certificate,
    with none, and with a self-signed one the tenant has never seen. The first
    must succeed - otherwise a refusal would prove nothing about certificates.
    """
    url = cloud.base_url + "units/"

    with_cert = mtls.probe(url, ca_bundle, cloud.client_pem)
    assert with_cert.accepted, report.failed(
        CHECK_B3,
        f"the endpoint did not accept the valid certificate ({with_cert.status}"
        f"{'; ' + with_cert.error if with_cert.error else ''}), so nothing can be concluded "
        "about the refusals",
    )

    without_cert = mtls.probe(url, ca_bundle, None)
    untrusted = mtls.probe(url, ca_bundle, mtls.make_untrusted_identity(tmp_path))

    assert not without_cert.accepted, report.failed(
        CHECK_B3, "the endpoint served a caller presenting no client certificate"
    )
    assert not untrusted.accepted, report.failed(
        CHECK_B3, "the endpoint served a caller presenting an untrusted self-signed certificate"
    )
    probes = (
        ("no certificate", without_cert),
        ("an untrusted certificate", untrusted),
    )
    for label, result in probes:
        assert result.tls_refusal or result.status is not None, report.failed(
            CHECK_B3,
            f"the call with {label} failed without reaching the endpoint, so it shows nothing "
            f"about the certificate: {result.error}",
        )

    record_property(
        CHECK_B3,
        report.passed(
            CHECK_B3,
            "the same endpoint, in the same run, answers a caller holding a valid client "
            f"certificate ({with_cert.status}) and refuses one with no certificate "
            f"({mtls.describe(without_cert)}) and one holding a certificate it does not trust "
            f"({mtls.describe(untrusted)}) - an untrusted caller is not served, and neither "
            "refusal is a network error",
        ),
    )
    if not without_cert.tls_refusal:
        record_property(
            CHECK_B3 + "-layer",
            report.note(
                CHECK_B3,
                "the endpoint completes the TLS handshake with a caller presenting no client "
                f"certificate and refuses afterwards at the application layer "
                f"({mtls.describe(without_cert)}). Access is denied either way, but this is not "
                "a handshake-level refusal and should not be described as one",
            ),
        )


@pytest.mark.destructive
def test_b4_released_unit_is_not_served(provisioned_unit, cloud, record_property):
    """Releasing the unit removes it from the tenant and puts its identity
    service back behind its start condition.

    Runs last: it deprovisions the unit the other checks rely on.
    """
    vm = provisioned_unit["vm"]
    system_uid = provisioned_unit["system_uid"]

    record = cloud.find_unit(system_uid)
    assert record, report.failed(CHECK_B4, "the unit had already left the tenant before release")

    # Order matters: the cloud refuses to release a unit that is still online,
    # so the unit is stopped locally first and the release only then applied.
    provisioning.local_deprovision(vm)
    assert provisioning.wait_until_offline(cloud, system_uid), report.failed(
        CHECK_B4, "the unit never went offline after being deprovisioned locally"
    )
    cloud.deprovision_and_delete(str(record["id"]))

    assert cloud.find_unit(system_uid) is None, report.failed(
        CHECK_B4, "the tenant still knows the unit after it was released"
    )
    condition = provisioning.identity_service_condition(vm, retry_start=True)
    assert condition == "no", report.failed(
        CHECK_B4,
        f"the identity service condition was {condition!r} after release; expected it to be "
        "held back again",
    )
    record_property(
        CHECK_B4,
        report.passed(
            CHECK_B4,
            "after the unit is released the tenant no longer knows it and its identity service "
            "is held back again - it can no longer act on the tenant's behalf",
        ),
    )
