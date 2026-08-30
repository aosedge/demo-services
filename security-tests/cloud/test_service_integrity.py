"""Test C - only signed, correctly targeted services reach a unit.

Anti-rollback is deliberately out of scope. What we have observed is cloud-side
version ordering, which is not evidence that a unit refuses an older build, and
no unit-side requirement has been confirmed. Claiming it here would overstate
what the suite checks.

The tamper check is an A/B: two bundles are signed, unpacked and repacked the
same way, and only one of them has its payload altered in between. Without that
control a rejection would only show that the cloud disliked a rebuilt archive.
"""
from __future__ import annotations

import time

import pytest

from runner.helpers import bundle, report, service

pytestmark = pytest.mark.cloud

CHECK_C1 = "C1 signed-service-installed"
CHECK_C2 = "C2 tampered-payload-rejected"
CHECK_C3 = "C3 targeting-respected"

_SETTLE_S = 180


@pytest.fixture(scope="module")
def delivered(provisioned_unit, config, cloud, marker):
    """Publish the probe and deliver it to the provisioned unit."""
    vm = provisioned_unit["vm"]
    version = service.publish_probe(cloud, config, marker)
    service.deliver(
        cloud, provisioned_unit["system_uid"], config.unit_set_id, config.subject_id, version
    )
    return {"running": service.wait_until_running(vm), "vm": vm}


def test_c1_signed_service_is_installed(delivered, marker, record_property):
    """A correctly signed service is delivered and actually runs."""
    assert delivered["running"], report.failed(
        CHECK_C1, "the signed service was published but its instance never became active"
    )
    vm = delivered["vm"]
    path = service.wait_for_marker(vm, marker)
    assert path, report.failed(
        CHECK_C1,
        "the instance reported active but produced none of its own data, so it cannot be said "
        "to be running the payload that was published.\nStorage area:\n"
        f"{service.storage_layout(vm)}\nRecent instance output:\n"
        f"{service.instance_log_tail(vm, lines=25)}",
    )
    record_property(
        CHECK_C1,
        report.passed(
            CHECK_C1,
            f"a correctly signed service was accepted, installed and is running its published "
            f"payload (wrote {path})",
        ),
    )


def test_c2_tampered_payload_is_rejected(config, cloud, record_property):
    """A payload altered after signing is refused; an identically repacked one is not."""
    work = config.suite_root / ".run"
    control_version = service.next_version(cloud, service.TAMPER_CODENAME)
    tampered_version = f"{control_version}1"

    control = bundle.stage(
        config.suite_root / "probes" / "marker-writer", work, "bundle-control",
        version=control_version, marker="CONTROLMARKER",
        codename=service.TAMPER_CODENAME, sp_p12=config.sp_p12,
    )
    bundle.repack_unchanged(bundle.sign(control))
    bundle.upload(control, config.sp_p12)

    tampered = bundle.stage(
        config.suite_root / "probes" / "marker-writer", work, "bundle-tampered",
        version=tampered_version, marker="TAMPEREDMARKER",
        codename=service.TAMPER_CODENAME, sp_p12=config.sp_p12,
    )
    bundle.repack_tampered(bundle.sign(tampered))
    cli_output = bundle.upload(tampered, config.sp_p12)

    time.sleep(_SETTLE_S)
    service_id = cloud.find_service_id(service.TAMPER_CODENAME)
    assert service_id, report.failed(
        CHECK_C2, "the tamper-check service is not present in the tenant"
    )
    versions = cloud.service_versions(service_id)

    assert control_version in versions, report.failed(
        CHECK_C2,
        "the untampered control bundle was also refused, so the refusal cannot be attributed to "
        "the payload having been altered - repacking alone is enough to break it",
    )
    assert tampered_version not in versions, report.failed(
        CHECK_C2,
        f"the tampered bundle was accepted and published as {tampered_version} "
        f"(state {versions.get(tampered_version)!r})",
    )
    record_property(
        CHECK_C2,
        report.passed(
            CHECK_C2,
            "two bundles were signed and repacked identically; only the one whose payload was "
            "modified after signing was refused publication, so the integrity check acted on the "
            "change rather than on the repacking",
        ),
    )
    if "successfully uploaded" in cli_output.lower():
        record_property(
            CHECK_C2 + "-cli",
            report.note(
                CHECK_C2,
                "aos-signer reported the tampered bundle as successfully uploaded and exited 0; "
                "the refusal is only visible in the service's published versions. Do not treat "
                "the upload command's success as acceptance",
            ),
        )


def test_c3_service_not_delivered_to_untargeted_unit(
    provisioned_unit, config, cloud, record_property
):
    """A service offered through a subject the unit is not attached to does not reach it.

    Asserted positively: the unit is shown to be processing desired status in
    this window, and the service is shown to be absent from it. "Nothing
    happened" would not distinguish correct targeting from a stalled unit.
    """
    vm = provisioned_unit["vm"]
    other_subject = cloud.create_subject(f"sec-untargeted-{int(time.time())}")
    try:
        service_id = cloud.find_service_id(service.PROBE_CODENAME)
        assert service_id, report.failed(CHECK_C3, "the probe service is not present in the tenant")
        cloud.attach_service_to_subject(other_subject, service_id)

        # Attaching a service to a subject this unit is not part of generates no
        # traffic for it at all, which would leave nothing to judge. Cycling the
        # unit's own attachment forces fresh desired-status updates while the
        # untargeted service exists, so the absence below is observed rather
        # than assumed.
        cloud.detach_unit(provisioned_unit["system_uid"], config.subject_id)
        time.sleep(_SETTLE_S)
        cloud.attach_unit(
            provisioned_unit["system_uid"], config.unit_set_id, config.subject_id
        )
        time.sleep(_SETTLE_S)

        window = f"-{2 * _SETTLE_S + 60}s"
        processed = vm.exec(
            f"journalctl -u aos-cm --no-pager -o cat --since {window} 2>/dev/null "
            "| grep -c 'Process desired status'"
        ).text()
        offered = vm.exec(
            f"journalctl -u aos-cm --no-pager -o cat --since {window} 2>/dev/null "
            f"| grep -c 'id={other_subject}'"
        ).text()

        assert processed.isdigit() and int(processed) > 0, report.failed(
            CHECK_C3,
            "the unit did not process any desired status in this window, so its not receiving the "
            "service proves nothing",
        )
        assert offered.isdigit() and int(offered) == 0, report.failed(
            CHECK_C3,
            f"the unit was offered the subject {other_subject} it was never attached to",
        )
        record_property(
            CHECK_C3,
            report.passed(
                CHECK_C3,
                "while the unit was actively processing desired status, a service published "
                "through a subject it is not attached to was never offered to it - delivery "
                "follows the explicit attachment, not mere publication",
            ),
        )
    finally:
        cloud.delete_subject(other_subject)
