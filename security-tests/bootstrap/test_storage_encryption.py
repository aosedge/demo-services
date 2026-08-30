"""Test A - data at rest on an AosCore unit is stored as ciphertext.

WHAT THIS PROVES, and what it deliberately does not
---------------------------------------------------
On this target the suite proves that AosCore keeps deployable-item data on an
encrypted volume: the volume is LUKS2, and a marker written by a running
service cannot be found in plaintext anywhere on the raw partition.

It does NOT prove that the encryption key is protected against extraction.
On a QEMU VM AosCore uses SoftHSM, whose token lives on the filesystem and
whose user PIN is stored in /var/aos/iam/.usrpin on an *unencrypted*
partition. Anyone with root on the VM, or with the disk image, can unlock the
volume. Key non-extractability is a property of the hardware platform
(OP-TEE-backed PKCS#11 on the target SoC) and is reported here as a stated
guarantee, never as a verified result. See README, "Key protection".
"""
from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from runner.helpers import network, provisioning, rawdisk, report, service

pytestmark = pytest.mark.bootstrap

CHECK_A0 = "A0 volume-absent-before-provisioning"
CHECK_A1 = "A1 volume-is-luks"
CHECK_A2 = "A2 probe-running"
CHECK_A3 = "A3 marker-readable-in-instance"
CHECK_A4 = "A4 marker-absent-from-raw-device"
CHECK_A6 = "A6 volume-destroyed-on-deprovision"
CHECK_A7 = "A7 key-protection"


@pytest.fixture(scope="module")
def unit(config, target, cloud, marker):
    """Bring the unit up, provision it and deliver the probe.

    Module-scoped: provisioning is slow and every check below reads the same
    unit state. Teardown always detaches, deprovisions and deletes so no
    dangling unit is left in the tenant.
    """
    config.require_cloud()
    vm = target.start()

    state: dict[str, object] = {"vm": vm, "pre_provision_fstype": None, "system_uid": None}
    cloud_host = urlsplit(config.cloud_api).hostname or ""
    state["network_note"] = network.ensure_name_resolution(vm, cloud_host)
    state["pre_provision_fstype"] = rawdisk.partition_fstype(vm)
    if provisioning.is_provisioned(vm):
        # The published image ships unprovisioned; a reused local copy may not
        # be. Reset it so A0 measures what it claims to measure.
        provisioning.local_deprovision(vm)
        state["pre_provision_fstype"] = rawdisk.partition_fstype(vm)

    system_uid = provisioning.provision(config)
    state["system_uid"] = system_uid

    version = service.publish_probe(cloud, config, marker)
    service.deliver(
        cloud, system_uid, config.unit_set_id,
        subject_id=config.subject_id, version=version,
    )
    state["probe_running"] = service.wait_until_running(vm)

    yield state

    cloud.detach_unit(system_uid, config.subject_id)
    record = cloud.find_unit(system_uid)
    provisioning.local_deprovision(vm)
    if record:
        cloud.deprovision_and_delete(str(record["id"]))
    target.stop()


def test_a0_no_encrypted_volume_before_provisioning(unit, record_property):
    """Before the unit is bound to a tenant there is no encrypted volume at all."""
    fstype = unit["pre_provision_fstype"]
    assert fstype != "crypto_LUKS", (
        report.failed(
            CHECK_A0,
            f"an encrypted volume already existed before provisioning: {fstype}",
        )
    )
    record_property(
        CHECK_A0,
        report.passed(
            CHECK_A0,
            "before provisioning the unit has no encrypted volume - it is created only when the "
            "unit is registered with the cloud",
        ),
    )


def test_a1_volume_is_luks(unit, record_property):
    """After provisioning the AosCore data partition is a LUKS2 volume."""
    vm = unit["vm"]
    fstype = rawdisk.partition_fstype(vm)
    assert fstype == "crypto_LUKS", report.failed(
        CHECK_A1,
        f"the AosCore data partition is {fstype or 'unformatted'}, not an encrypted volume",
    )
    assert rawdisk.is_luks(vm), report.failed(
        CHECK_A1, "cryptsetup does not recognise the partition as LUKS"
    )
    record_property(
        CHECK_A1,
        report.passed(CHECK_A1, "provisioning created a LUKS2-encrypted volume for AosCore data"),
    )


def test_a2_probe_instance_running(unit, record_property):
    """The probe service was delivered and its instance reached the active state."""
    assert unit["probe_running"], report.failed(
        CHECK_A2, "the probe service instance never reached the active state"
    )
    record_property(
        CHECK_A2,
        report.passed(CHECK_A2, "the probe service was delivered and its instance is running"),
    )


def test_a3_marker_readable_inside_instance(unit, marker, record_property):
    """The marker the probe wrote is readable on the unit."""
    vm = unit["vm"]
    path = service.find_marker_path(vm, marker)
    unit["marker_path"] = path
    assert path, report.failed(
        CHECK_A3,
        "the marker written by the probe could not be read back. Storage area:\n"
        f"{service.storage_layout(vm)}\nRecent instance output:\n"
        f"{service.instance_log_tail(vm)}",
    )
    record_property(
        CHECK_A3,
        report.passed(
            CHECK_A3, f"the service reads its own data normally through the filesystem ({path})"
        ),
    )


def test_a4_marker_absent_from_raw_device(unit, marker, record_property):
    """The whole raw partition contains no plaintext copy of the marker.

    Guarded by A2/A3 on purpose: if the probe never wrote the marker, "not
    found on the device" is vacuously true and would report encryption that was
    never exercised. A check that cannot be performed is a failure, not a pass.
    """
    assert unit.get("marker_path"), report.failed(
        CHECK_A4,
        "could not be verified: the marker was never confirmed on the unit (A3), so finding "
        "nothing on the raw device would prove nothing about encryption",
    )
    occurrences = rawdisk.marker_occurrences(unit["vm"], marker)
    assert occurrences == 0, report.failed(
        CHECK_A4,
        f"the marker was found {occurrences} time(s) in plaintext on the raw device - "
        "data at rest is NOT encrypted",
    )
    record_property(
        CHECK_A4,
        report.passed(
            CHECK_A4,
            "the data written by the service cannot be found in plaintext anywhere on the raw "
            "device - it is stored as ciphertext",
        ),
    )


def test_a7_key_protection_is_a_hardware_guarantee(unit, target, record_property):
    """State the key-protection property honestly for this target.

    Passing this check means the suite reported the guarantee correctly, not
    that key extraction was attempted and failed.
    """
    if target.supports_hardware_tee():
        message = report.passed(
            CHECK_A7,
            "the encryption key is held in a hardware TEE (OP-TEE-backed PKCS#11) on this target",
        )
    else:
        message = report.note(
            CHECK_A7,
            "key protection is NOT verified on this target: the VM has no secure element, so "
            "AosCore uses SoftHSM with its token on the filesystem and the PIN in "
            "/var/aos/iam/.usrpin on an unencrypted partition. Non-extractability of the key is a "
            "hardware guarantee (OP-TEE) that must be verified on the target SoC, not here",
        )
    record_property(CHECK_A7, message)


@pytest.mark.destructive
def test_a6_volume_destroyed_on_deprovision(unit, marker, record_property):
    """Deprovisioning destroys the encrypted volume.

    Runs last: it removes the volume the earlier checks inspect.
    """
    vm = unit["vm"]
    provisioning.local_deprovision(vm)
    assert not rawdisk.volume_group_present(vm), report.failed(
        CHECK_A6, "the AosCore volume group still exists after deprovisioning"
    )
    assert not service.read_marker_in_instance(vm, marker), report.failed(
        CHECK_A6, "data written before deprovisioning is still readable on the unit"
    )
    record_property(
        CHECK_A6,
        report.passed(
            CHECK_A6,
            "deprovisioning destroyed the volume group and its contents are no longer readable",
        ),
    )
    if rawdisk.partition_fstype(vm) == "crypto_LUKS":
        record_property(
            CHECK_A6 + "-header",
            report.note(
                CHECK_A6,
                "the partition still carries a LUKS header after deprovisioning: the volume group "
                "and its data are gone, but the container itself is not wiped. Not a finding by "
                "itself - stated so nobody reads the leftover header as leftover data",
            ),
        )
