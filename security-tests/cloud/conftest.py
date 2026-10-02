"""A provisioned unit, shared by the tests in this package.

Provisioning is the slowest thing the suite does, so the unit is brought up
once per module and every phase it passes through is recorded on the way, which
lets the identity tests assert on the "before" state without a second boot.
"""
from __future__ import annotations

from urllib.parse import urlsplit

import pytest

from runner.helpers import network, provisioning, rawdisk


@pytest.fixture(scope="module")
def provisioned_unit(config, target, cloud):
    """Boot a unit, register it, and tear it down completely afterwards.

    Yields a dict describing what was observed before and after provisioning.
    """
    config.require_cloud()
    vm = target.start()
    state: dict[str, object] = {"vm": vm}

    cloud_host = urlsplit(config.cloud_api).hostname or ""
    state["network_note"] = network.ensure_name_resolution(vm, cloud_host)

    if provisioning.is_provisioned(vm):
        provisioning.local_deprovision(vm)

    # Captured before registration so the identity tests can show that the unit
    # was genuinely unknown, rather than assuming it.
    state["known_system_uids_before"] = cloud.unit_system_uids()
    state["iam_condition_before"] = provisioning.identity_service_condition(vm)
    state["volume_before"] = rawdisk.partition_fstype(vm)

    system_uid = provisioning.provision(config)
    state["system_uid"] = system_uid
    state["record_after_provisioning"] = cloud.find_unit(system_uid)
    state["online"] = provisioning.wait_until_online(cloud, system_uid)
    state["iam_condition_after"] = provisioning.identity_service_condition(vm)

    yield state

    record = cloud.find_unit(system_uid)
    provisioning.local_deprovision(vm)
    if record:
        cloud.deprovision_and_delete(str(record["id"]))
    target.stop()
