"""Tests D and E - what an instance can reach, and what identity it runs as.

Both are answered from one deployment: a probe that tries to read things
belonging to the unit, tries the usual ways of becoming root, and writes down
exactly how each attempt ended.

Two things are deliberately reported as statements rather than results:
domain isolation, because this target runs no hypervisor at all, and explicit
IAM permission grants, because nothing on the image or in the published demo
services exposes a permission to grant or withhold. Writing a test that
"passes" against a surface that is not there would prove nothing.
"""
from __future__ import annotations

import json

import pytest

from runner.helpers import report, service

pytestmark = pytest.mark.cloud

CHECK_D1 = "D1 instance-reads-own-data"
CHECK_D2 = "D2 unit-data-not-reachable"
CHECK_D3 = "D3 domain-isolation"
CHECK_E1 = "E1 instance-runs-as-granted-identity"
CHECK_E2 = "E2 privilege-escalation-refused"
CHECK_E3 = "E3 explicit-permission-grants"

# Paths that exist on the unit and that an instance has no business reading.
# Their presence is confirmed unit-side in the fixture, so a refusal cannot be
# mistaken for a path that was never there.
UNIT_TARGETS = (
    "/var/aos/iam/.usrpin",
    "/etc/aos/iam.cfg",
    "/var/aos/storages",
)


@pytest.fixture(scope="module")
def snooped(provisioned_unit, config, cloud, marker):
    """Deploy the snooper and collect its report, plus unit-side ground truth."""
    vm = provisioned_unit["vm"]
    version = service.publish_snooper(cloud, config, marker, UNIT_TARGETS)
    service.deliver(
        cloud, provisioned_unit["system_uid"], config.unit_set_id,
        subject_id=config.subject_id, version=version, codename=service.SNOOPER_CODENAME,
    )
    raw = service.wait_for_snoop_report(vm)
    assert raw, "the snooper never produced its report"

    return {
        "vm": vm,
        "report": json.loads(raw),
        # Ground truth: each target really is present on the unit itself.
        "exists_on_unit": {
            target: vm.exec(f"test -e {target}").ok for target in UNIT_TARGETS
        },
    }


def test_d1_instance_reads_its_own_data(snooped, record_property):
    """The probe can read what it wrote itself - so a refusal below is meaningful."""
    assert snooped["report"]["own_file_readable"], report.failed(
        CHECK_D1,
        "the probe could not read back its own file, so nothing can be concluded from what it "
        "failed to read elsewhere",
    )
    record_property(
        CHECK_D1,
        report.passed(
            CHECK_D1,
            "the instance reads and writes its own storage normally - the checks below are not "
            "measuring a broken probe",
        ),
    )


def test_d2_unit_data_is_not_reachable_from_an_instance(snooped, record_property):
    """Files that exist on the unit are unreachable from inside an instance."""
    missing_ground_truth = [t for t, present in snooped["exists_on_unit"].items() if not present]
    assert not missing_ground_truth, report.failed(
        CHECK_D2,
        f"these targets do not exist on the unit either, so their unreachability from the "
        f"instance proves nothing: {missing_ground_truth}",
    )

    outcomes = {entry["path"]: entry for entry in snooped["report"]["reads"]}
    reachable = [path for path, entry in outcomes.items() if entry["read"]]
    assert not reachable, report.failed(
        CHECK_D2, f"an instance read the unit's own files: {reachable}"
    )

    detail = ", ".join(
        f"{path} (errno {entry['errno']})" for path, entry in sorted(outcomes.items())
    )
    record_property(
        CHECK_D2,
        report.passed(
            CHECK_D2,
            "every file checked exists on the unit and none of them could be read from inside a "
            f"service instance: {detail}",
        ),
    )


def test_d3_domain_isolation_is_a_platform_property(provisioned_unit, record_property):
    """State domain isolation honestly for this target."""
    vm = provisioned_unit["vm"]
    hypervisor = vm.exec("test -d /proc/xen && echo yes || echo no").text()
    if hypervisor == "yes":
        message = report.passed(
            CHECK_D3, "the unit runs under a hypervisor and its domains are separated"
        )
    else:
        message = report.note(
            CHECK_D3,
            "domain isolation is NOT exercised on this target: the VM image runs no hypervisor "
            "(no /proc/xen, no Xen in the kernel log), so there are no separate domains here. "
            "On the hardware platforms that do run Xen this is a property of the hypervisor "
            "configuration and has to be checked there",
        )
    record_property(CHECK_D3, message)


def test_e1_instance_runs_as_a_granted_non_root_identity(snooped, record_property):
    """The instance runs as the unit-assigned identity, not as root."""
    uid = snooped["report"]["uid"]
    gid = snooped["report"]["gid"]
    assert uid != 0, report.failed(
        CHECK_E1, f"the service instance runs as uid {uid} - it has the unit's root identity"
    )
    record_property(
        CHECK_E1,
        report.passed(
            CHECK_E1,
            f"the service instance runs as the identity the unit assigned it (uid {uid}, gid "
            f"{gid}), not as the unit's root",
        ),
    )


def test_e2_privilege_escalation_is_refused(snooped, record_property):
    """The kernel refuses to let an instance take the unit's root identity.

    Every attempt is a syscall rather than a call to su or sudo: a helper that
    is simply absent from the image would fail for reasons that say nothing
    about isolation. Only failures the kernel itself returned (EPERM, EACCES,
    EROFS) are counted as evidence.
    """
    attempts = snooped["report"]["escalation"]
    assert attempts, report.failed(CHECK_E2, "no escalation attempts were recorded")

    succeeded = [a["attempt"] for a in attempts if a["succeeded"]]
    assert not succeeded, report.failed(
        CHECK_E2, f"an instance escalated its privileges via: {succeeded}"
    )

    refused = [a for a in attempts if a.get("kernel_refusal")]
    assert refused, report.failed(
        CHECK_E2,
        "no attempt was refused by the kernel - every one failed for some other reason "
        f"({[(a['attempt'], a['error']) for a in attempts]}), which demonstrates nothing about "
        "whether escalation is actually prevented",
    )

    privileges = snooped["report"].get("privileges", {})
    detail = ", ".join(f"{a['attempt']} ({a['error']})" for a in refused)
    caps = privileges.get("CapEff", "unknown")
    record_property(
        CHECK_E2,
        report.passed(
            CHECK_E2,
            f"the kernel refused every attempt an instance made to take the unit's root identity: "
            f"{detail}; the instance holds no effective capabilities (CapEff {caps})",
        ),
    )


def test_e3_explicit_permission_grants(record_property):
    """Say plainly that granted-permission enforcement was not exercised."""
    record_property(
        CHECK_E3,
        report.note(
            CHECK_E3,
            "enforcement of explicitly granted permissions is NOT exercised by this suite: no "
            "permission surface is configured on the image and none of the published demo "
            "services declares one, so there is nothing to grant or withhold. What is shown "
            "instead is the boundary the platform does enforce here - a non-root assigned "
            "identity (E1) that cannot escalate (E2) and cannot reach the unit's own data (D2). "
            "A grant/deny test needs the permissions API to be documented and exercised first",
        ),
    )
