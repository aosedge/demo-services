# Copyright (c) 2018-2026 EPAM Systems Inc.
# SPDX-License-Identifier: Apache-2.0
"""Tests of verify_batch.py against a tiny in-memory AosCloud. No network: requests.Session is replaced.

    pip install pytest requests && python -m pytest -q
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import verify_batch  # noqa: E402  pylint: disable=wrong-import-position

SERVICE, UNIT, BATCH, NEW, OLD = 'service-1', 'unit-1', 'batch-1', '4.6.0', '4.5.0'


class Answer:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = '' if body is None else str(body)

    def json(self):
        if self._body is None:
            raise ValueError('no JSON in the answer')
        return self._body


class Cloud:
    """The answers of the calls the script makes. ``unit``: none | pending | activating | installed | failed."""

    def __init__(self):
        self.batch = {'id': BATCH, 'state': 'Waiting_validation', 'architectures': ['amd64'],
                      'update_items': [{'identity_id': SERVICE, 'version': NEW}]}
        self.others = []  # batches listed before ours, newest first
        self.unit, self.patches, self.gets = 'installed', [], []
        self.down = self.lose_patch = False
        self.fail_get = None  # an exception or an HTTP status for every GET
        self.patch_answer = None  # (status, body) instead of the batch
        self.verification_set = {'amd64': [UNIT]}  # per architecture: what statistics lists
        self.service_units = [UNIT]  # what services/{id}/units/ lists
        self.states = {}  # per Unit, other than UNIT: pending | installed | failed
        self.offline = set()  # the Units that are offline
        self.offline_reads = 0  # UNIT reads as offline this many times, then online

    def entry(self):
        if self.unit == 'none':
            return []
        versions = {'installed_service_version': {'version': OLD}, 'pending_service_version': {'version': NEW},
                    'pending_service_version_status': 'pending'}
        instances = [{'version': OLD, 'run_state': 'active'}]
        if self.unit in ('activating', 'installed', 'failed'):
            versions = {'installed_service_version': {'version': NEW}, 'pending_service_version': None,
                        'pending_service_version_status': 'installed'}
            run_state = {'activating': 'activating', 'installed': 'active', 'failed': 'failed'}[self.unit]
            instances = [{'version': NEW, 'run_state': run_state}]
        return [{'service_versions': versions, 'instances': instances}]

    def batches(self):
        return self.others + ([self.batch] if self.batch else [])

    def session(self):
        cloud = self

        class Session:
            cert = verify = None

            def get(self, url, params=None, timeout=None):
                path = url.split('/api/v11/', 1)[1]
                cloud.gets.append((path, dict(params or {})))
                if cloud.down:
                    raise requests.ConnectionError('AosCloud is not reachable')
                if isinstance(cloud.fail_get, Exception):
                    raise cloud.fail_get
                if cloud.fail_get:
                    return Answer(cloud.fail_get, {'detail': 'error'})
                if path == 'verification-batch/':
                    offset, limit = int(params.get('offset', 0)), int(params.get('limit', 50))
                    every = cloud.batches()
                    return Answer(200, {'total': len(every), 'offset': offset, 'items': every[offset:offset + limit]})
                for batch in cloud.batches():
                    if path == f"verification-batch/{batch['id']}/":
                        return Answer(200, batch)
                    if path == f"verification-batch/{batch['id']}/statistics/":
                        return Answer(200, {'batch_id': batch['id'], 'sub_batches': [
                            {'architecture': arch, 'verification_set': {'count': len(ids), 'unit_ids': ids},
                             'installed': {'count': 0, 'unit_ids': []}}
                            for arch, ids in cloud.verification_set.items()]})
                if path == f'services/{SERVICE}/units/':
                    offset, limit = int(params.get('offset', 0)), int(params.get('limit', 50))
                    every = [{'id': unit} for unit in cloud.service_units]
                    return Answer(200, {'total': len(every), 'offset': offset, 'items': every[offset:offset + limit]})
                unit_read = re.fullmatch(r'units/([^/]+)/', path)
                if unit_read:
                    late = unit_read[1] == UNIT and cloud.offline_reads > 0
                    cloud.offline_reads -= 1 if late else 0
                    down = late or unit_read[1] in cloud.offline
                    return Answer(200, {'id': unit_read[1], 'online_status': 'Offline' if down else 'Online'})
                if path == f'units/{UNIT}/subjects-services/{SERVICE}/':
                    return Answer(200, {'total': 1, 'offset': 0, 'items': cloud.entry()})
                for unit, state in cloud.states.items():
                    if path == f'units/{unit}/subjects-services/{SERVICE}/':
                        saved, cloud.unit = cloud.unit, state
                        entry = cloud.entry()
                        cloud.unit = saved
                        return Answer(200, {'total': 1, 'offset': 0, 'items': entry})
                return Answer(404, {'detail': 'Not found.'})

            def patch(self, url, json=None, timeout=None):  # pylint: disable=redefined-outer-name
                cloud.patches.append(json)
                if cloud.lose_patch:
                    raise requests.ReadTimeout('read timed out')
                if cloud.patch_answer:
                    return Answer(*cloud.patch_answer)
                cloud.batch['state'] = 'Valid' if all(e['is_approved'] for e in json) else 'Invalid'
                return Answer(200, cloud.batch)

        return Session()


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    for name in ('user.pem', 'root-ca.pem'):
        (tmp_path / name).write_text('test\n')
    monkeypatch.setenv('AOS_API_URL', 'https://api.example/api/v11')
    monkeypatch.setenv('AOS_CERT', str(tmp_path / 'user.pem'))
    monkeypatch.setenv('AOS_CA', str(tmp_path / 'root-ca.pem'))
    monkeypatch.delenv('AOS_KEY', raising=False)
    the_cloud = Cloud()
    monkeypatch.setattr(verify_batch.requests, 'Session', the_cloud.session)
    return the_cloud


def wait(timeout=0.3, units=(UNIT,), batch_id=None, units_file=None):
    return verify_batch.wait(SimpleNamespace(service_id=SERVICE, version=NEW, unit=list(units), batch_id=batch_id,
                                             units_file=units_file, timeout=timeout, interval=0.02))


def decide(approve=True, arch=None):
    return verify_batch.decide(SimpleNamespace(batch_id=BATCH, approve=approve, arch=arch))


def main(*argv):
    return verify_batch.main(list(argv))


WAIT_ARGS = ('wait', '--service-id', SERVICE, '--version', NEW, '--unit', UNIT, '--timeout', '0.3',
             '--interval', '0.02')


# wait

def test_wait_ready_prints_the_batch_id(cloud, capsys):
    assert wait() == 0
    assert capsys.readouterr().out.strip() == BATCH


def test_wait_on_a_decided_batch_exits_2(cloud, capsys):
    cloud.batch['state'] = 'Valid'
    assert wait() == 2 and capsys.readouterr().out == ''


def test_timeout_when_the_unit_never_gets_the_version(cloud, capsys):
    cloud.unit = 'pending'
    assert wait() == 2 and capsys.readouterr().out == '' and cloud.patches == []


def test_timeout_when_there_is_no_batch(cloud, capsys):
    cloud.batch = None
    assert wait() == 2 and capsys.readouterr().out == ''


def test_failed_version_exits_1_with_the_batch_id(cloud, capsys):
    cloud.unit = 'failed'
    assert wait() == 1 and capsys.readouterr().out.strip() == BATCH


def test_an_instance_still_activating_is_not_ready(cloud, capsys):
    cloud.unit = 'activating'
    assert wait() == 2 and capsys.readouterr().out == ''


def test_a_failed_instance_of_another_version_does_not_count(cloud):
    cloud.unit = 'pending'
    cloud.entry = lambda: [{'service_versions': {'installed_service_version': {'version': NEW}},
                            'instances': [{'version': NEW, 'run_state': 'active'},
                                          {'run_state': 'failed'}, {'version': OLD, 'run_state': 'failed'}]}]
    assert wait() == 0


def test_wait_prints_unit_states_only_when_they_change(cloud, capsys):
    cloud.unit = 'pending'
    wait(timeout=0.3)
    assert len([line for line in capsys.readouterr().err.splitlines() if line.startswith('Verification batch ')]) == 1


def test_the_batch_is_found_on_a_later_page(cloud):
    cloud.others = [{'id': f'other-{n}', 'state': 'Valid', 'architectures': ['amd64'],
                     'update_items': [{'identity_id': 'another-service', 'version': NEW}]} for n in range(120)]
    assert wait() == 0
    offsets = [params['offset'] for path, params in cloud.gets if path == 'verification-batch/']
    assert offsets[:3] == [0, 50, 100]


def test_a_waiting_batch_is_preferred_and_paging_stops(cloud, capsys):
    decided = {'id': 'batch-old', 'state': 'Invalid', 'architectures': ['amd64'],
               'update_items': [{'identity_id': SERVICE, 'version': NEW}]}
    cloud.others = [decided] + [{'id': f'other-{n}', 'state': 'Valid', 'update_items': []} for n in range(60)]
    cloud.batch, waiting = None, dict(cloud.batch)
    cloud.others.insert(10, waiting)
    assert wait() == 0 and capsys.readouterr().out.strip() == BATCH
    assert [p['offset'] for path, p in cloud.gets if path == 'verification-batch/'] == [0]


def test_without_units_wait_takes_them_from_the_verification_set(cloud, capsys, tmp_path):
    found = tmp_path / 'units'
    assert wait(units=('', ' '), units_file=str(found)) == 0
    assert capsys.readouterr().out.strip() == BATCH and found.read_text() == UNIT


def test_given_units_replace_the_service_list_but_are_checked_against_the_set(cloud, tmp_path):
    found = tmp_path / 'units'
    assert wait(units_file=str(found)) == 0 and found.read_text() == UNIT
    assert [path for path, _ in cloud.gets if 'statistics' in path]
    assert not [path for path, _ in cloud.gets if path.startswith('services/')]


def test_a_given_unit_outside_the_set_decides_nothing_even_if_it_runs_the_version(cloud, capsys):
    cloud.verification_set = {'amd64': ['unit-2']}  # UNIT runs the version, but it is not in the Set
    assert wait() == 2
    out = capsys.readouterr()
    assert out.out == '' and 'is not in the Verification Set' in out.err


def test_a_given_unit_cannot_stand_in_for_an_untested_architecture(cloud, capsys):
    cloud.verification_set = {'amd64': [UNIT], 'arm64': ['unit-2']}
    assert wait() == 2
    out = capsys.readouterr()
    assert out.out == '' and 'no Verification Set Unit for arm64' in out.err


def test_an_offline_unit_that_still_shows_active_decides_nothing(cloud, capsys):
    cloud.offline = {UNIT}  # cloud.unit is 'installed': the state AosCloud kept from before
    assert wait() == 3
    out = capsys.readouterr()
    assert out.out == '' and cloud.patches == [] and 'is offline' in out.err and 'does not count' in out.err
    assert 'decide nothing: Unit' in out.err and not [path for path, _ in cloud.gets if 'subjects-services' in path]


def test_an_offline_unit_that_shows_failed_is_not_rejected(cloud, capsys):
    cloud.offline, cloud.unit = {UNIT}, 'failed'
    assert wait() == 3 and capsys.readouterr().out == '' and cloud.patches == []


def test_a_unit_that_comes_online_is_ready(cloud, capsys):
    cloud.offline_reads = 2
    assert wait(timeout=1) == 0
    out = capsys.readouterr()
    assert out.out.strip() == BATCH and 'is offline' in out.err


def test_a_unit_without_an_online_status_does_not_count(cloud, monkeypatch):
    real = verify_batch.get
    monkeypatch.setattr(verify_batch, 'get', lambda path, **kw: {} if path == f'units/{UNIT}/' else real(path, **kw))
    assert wait() == 3


def test_of_two_units_one_offline_is_not_ready(cloud, capsys):
    cloud.verification_set = {'amd64': [UNIT, 'unit-2']}
    cloud.states, cloud.offline = {'unit-2': 'installed'}, {'unit-2'}
    assert wait(units=(UNIT, 'unit-2')) == 3 and capsys.readouterr().out == ''


def test_a_failed_online_unit_rejects_even_if_another_is_offline(cloud, capsys):
    cloud.verification_set = {'amd64': [UNIT, 'unit-2']}
    cloud.unit, cloud.states, cloud.offline = 'failed', {'unit-2': 'installed'}, {'unit-2'}
    assert wait(units=(UNIT, 'unit-2')) == 1 and capsys.readouterr().out.strip() == BATCH


def test_status_on_an_offline_unit_is_an_error_not_a_fail(cloud, capsys):
    cloud.offline = {UNIT}
    assert main('status', '--service-id', SERVICE, '--version', NEW, '--batch-id', BATCH, '--unit', UNIT) == 2
    out = capsys.readouterr()
    assert out.out == '' and 'is offline' in out.err


def test_a_locked_batch_is_not_waited_for(cloud, capsys):
    cloud.batch['is_locked'] = True
    assert wait() == 2 and capsys.readouterr().out == ''


def masks(err):
    return [line.split('::')[2] for line in err.splitlines() if line.startswith('::add-mask::')]


def test_in_github_actions_the_ids_are_masked_before_they_are_printed(cloud, capsys, monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    assert main('discover', '--service-id', SERVICE) == 0
    out = capsys.readouterr()
    assert {SERVICE, BATCH, UNIT} <= set(masks(out.err)) and BATCH in out.out
    assert main(*WAIT_ARGS) == 0
    out = capsys.readouterr()
    assert {SERVICE, BATCH, UNIT} <= set(masks(out.err)) and out.out.strip() == BATCH
    assert out.err.index(f'::add-mask::{BATCH}') < out.err.index(f'batch {verify_batch.short(BATCH)}: Unit')


def test_outside_github_actions_nothing_is_masked(cloud, capsys, monkeypatch):
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
    assert main('discover', '--service-id', SERVICE) == 0
    assert masks(capsys.readouterr().err) == []


def test_a_value_that_is_not_an_id_never_reaches_a_workflow_command(capsys, monkeypatch):
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    verify_batch.hide('x\n::set-output name=a::b', '', None, 'good-id')
    assert capsys.readouterr().err == '::add-mask::good-id\n'


def test_only_decide_can_write():
    source = (ROOT / 'verify_batch.py').read_text(encoding='utf-8')
    assert source.count('session.patch(') == 1 and source.index('session.patch(') > source.index('def decide(')
    assert not re.search(r'session\.(post|put|delete)\(', source)


def test_without_units_and_an_empty_set_wait_decides_nothing(cloud, capsys):
    cloud.verification_set = {'amd64': []}
    assert wait(units=()) == 2
    out = capsys.readouterr()
    assert out.out == '' and cloud.patches == [] and 'no Verification Set Unit for amd64' in out.err


def test_without_units_a_placeholder_unit_outside_the_set_is_not_waited_for(cloud):
    cloud.service_units = ['outside-the-set', UNIT]
    assert wait(units=()) == 0
    assert not [path for path, _ in cloud.gets if path.startswith('units/outside-the-set')]


def test_two_units_one_failed_ends_in_a_reject_code(cloud, capsys):
    cloud.verification_set = {'amd64': [UNIT, 'unit-2']}
    cloud.service_units = [UNIT, 'unit-2']
    cloud.states = {'unit-2': 'failed'}
    assert wait(units=()) == 1 and capsys.readouterr().out.strip() == BATCH and cloud.patches == []


def test_two_units_one_still_pending_is_not_ready(cloud, capsys):
    cloud.verification_set = {'amd64': [UNIT, 'unit-2']}
    cloud.service_units = [UNIT, 'unit-2']
    cloud.states = {'unit-2': 'pending'}
    assert wait(units=()) == 2 and capsys.readouterr().out == '' and cloud.patches == []


def test_wait_stops_when_the_version_is_in_another_batch(cloud, capsys):
    assert wait(batch_id='batch-9') == 2
    out = capsys.readouterr()
    assert out.out == '' and f"not in {verify_batch.short('batch-9')}" in out.err


@pytest.mark.parametrize('bad', ['../../x', 'a b', 'unit;id'])
def test_a_unit_that_is_not_an_id_stops(cloud, capsys, bad):
    assert main('wait', '--service-id', SERVICE, '--version', NEW, '--unit', bad) == 2
    assert cloud.gets == [] and 'not an id' in capsys.readouterr().err


@pytest.mark.parametrize('status', [400, 401, 403, 404])
def test_http_4xx_stops_at_once(cloud, capsys, status):
    cloud.fail_get = status
    assert main(*WAIT_ARGS[:-4], '--timeout', '30', '--interval', '0.02') == 2
    assert len(cloud.gets) == 1 and f'HTTP {status}' in capsys.readouterr().err


def test_a_tls_error_stops_at_once(cloud, capsys):
    cloud.fail_get = requests.exceptions.SSLError('tlsv1 alert unknown ca')
    assert main(*WAIT_ARGS[:-4], '--timeout', '30', '--interval', '0.02') == 2
    assert len(cloud.gets) == 1 and 'TLS error' in capsys.readouterr().err


@pytest.mark.parametrize('url', ['', 'api.example/api/v11', 'http://api.example/api/v11'])
def test_a_bad_api_url_stops_at_once(cloud, capsys, monkeypatch, url):
    monkeypatch.setenv('AOS_API_URL', url)
    assert main(*WAIT_ARGS) == 2 and cloud.gets == [] and 'AOS_API_URL' in capsys.readouterr().err


@pytest.mark.parametrize('error', [requests.ConnectionError('refused'), requests.Timeout('slow'), 503])
def test_outage_and_5xx_are_retried_until_the_timeout_and_exit_3(cloud, capsys, error):
    cloud.fail_get = error
    assert wait(timeout=0.2) == 3
    assert len(cloud.gets) > 1 and 'retrying' in capsys.readouterr().err


def test_a_missing_aos_cert_exits_2_with_nothing_on_stdout(cloud, capsys, monkeypatch):
    monkeypatch.delenv('AOS_CERT')
    assert main(*WAIT_ARGS) == 2
    out = capsys.readouterr()
    assert out.out == '' and 'AOS_CERT is not set' in out.err


def test_a_missing_cert_file_exits_2_with_nothing_on_stdout(cloud, capsys, monkeypatch, tmp_path):
    monkeypatch.setenv('AOS_CERT', str(tmp_path / 'missing.pem'))
    assert main(*WAIT_ARGS) == 2
    out = capsys.readouterr()
    assert out.out == '' and 'no file' in out.err


def test_any_unexpected_exception_exits_2(cloud, capsys, monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError('something else')
    monkeypatch.setattr(verify_batch, 'find_batch', broken)
    assert main(*WAIT_ARGS) == 2
    out = capsys.readouterr()
    assert out.out == '' and 'RuntimeError' in out.err


def test_the_script_itself_exits_2_without_aos_cert(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith('AOS_')}
    env['AOS_API_URL'] = 'https://api.example/api/v11'
    run = subprocess.run([sys.executable, str(ROOT / 'verify_batch.py'), *WAIT_ARGS], env=env, cwd=tmp_path,
                         capture_output=True, text=True, timeout=60, check=False)
    assert run.returncode == 2 and run.stdout == '' and 'AOS_CERT' in run.stderr


# decide

def test_approve_makes_the_batch_valid(cloud):
    assert decide(approve=True) == 0 and cloud.batch['state'] == 'Valid'


def test_approve_sends_exactly_one_patch_for_all_architectures(cloud, capsys):
    cloud.batch['architectures'] = ['amd64', 'arm64']
    decide(approve=True)
    assert cloud.patches == [[{'architecture': 'amd64', 'is_approved': True},
                              {'architecture': 'arm64', 'is_approved': True}]]
    assert '(amd64, arm64): approve' in capsys.readouterr().err


def test_arch_decides_only_the_named_architectures(cloud):
    cloud.batch['architectures'] = ['amd64', 'arm64']
    assert decide(approve=True, arch=['arm64']) == 0
    assert cloud.patches == [[{'architecture': 'arm64', 'is_approved': True}]]


def test_an_unknown_arch_is_refused(cloud):
    assert decide(arch=['riscv64']) == 2 and cloud.patches == []


def test_reject_makes_the_batch_invalid(cloud):
    assert decide(approve=False) == 0 and cloud.batch['state'] == 'Invalid' and len(cloud.patches) == 1


@pytest.mark.parametrize('state', ['Valid', 'Invalid', 'Partially_validated'])
def test_decided_batch_is_refused_and_nothing_is_sent(cloud, state):
    cloud.batch['state'] = state
    assert decide() == 2 and cloud.patches == [] and cloud.batch['state'] == state


def test_batch_without_architectures_is_refused(cloud):
    cloud.batch['architectures'] = []
    assert decide() == 2 and cloud.patches == []


def test_cloud_down_before_decide_sends_nothing(cloud):
    cloud.down = True
    assert decide() == 2 and cloud.patches == []


def test_lost_answer_exits_3_without_a_retry(cloud):
    cloud.lose_patch = True
    assert decide() == 3 and len(cloud.patches) == 1


@pytest.mark.parametrize('status', [500, 502, 504])
def test_5xx_after_the_patch_exits_3(cloud, status):
    cloud.patch_answer = (status, {'detail': 'error'})
    assert decide() == 3 and len(cloud.patches) == 1


def test_4xx_after_the_patch_exits_2(cloud):
    cloud.patch_answer = (405, {'detail': 'Method not allowed'})
    assert decide() == 2 and len(cloud.patches) == 1


@pytest.mark.parametrize('body', [None, 'not json', []])
def test_a_success_without_a_json_body_still_exits_0(cloud, capsys, body):
    cloud.patch_answer = (204 if body is None else 200, body)
    assert decide() == 0 and capsys.readouterr().out.strip() == f'Verification batch {verify_batch.short(BATCH)} (amd64): approved, sent'


# discover: step 4 (Start B), reads only

def waiting(version, batch_id=None, state='Waiting_validation', service=SERVICE):
    return {'id': batch_id or 'batch-' + version.replace('.', '_'), 'state': state, 'architectures': ['amd64'],
            'update_items': [{'identity_id': service, 'version': version}]}


def json_line(capsys):
    out = capsys.readouterr()
    lines = out.out.splitlines()
    assert len(lines) == 1, out
    return json.loads(lines[0]), out.err


@pytest.fixture
def github_output(monkeypatch, tmp_path):
    path = tmp_path / 'github_output'
    path.write_text('')
    monkeypatch.setenv('GITHUB_OUTPUT', str(path))
    return path


def discover(*extra):
    return main('discover', '--service-id', SERVICE, *extra)


def test_discover_takes_the_highest_version_and_reports_the_stale_ones(cloud, capsys, github_output):
    cloud.batch = None
    cloud.others = [waiting('4.9.0'), waiting('4.10.0'), waiting('4.2.0')]  # 4.10.0 is above 4.9.0: not a text compare
    assert discover() == 0
    found, err = json_line(capsys)
    assert found['version'] == '4.10.0' and found['units'] == [UNIT]
    assert 'version 4.9.0' in err and 'version 4.2.0' in err and 'stale' in err and cloud.patches == []
    assert github_output.read_text() == f"batch_id={found['batch_id']}\nversion=4.10.0\nunits={UNIT}\n"


@pytest.mark.parametrize('state', ['Valid', 'Invalid', 'Partially_validated'])
def test_discover_takes_no_older_batch_when_the_highest_version_is_decided(cloud, capsys, github_output, state):
    cloud.batch = None
    cloud.others = [waiting('5.0.0', state=state), waiting('4.9.0')]
    assert discover() == 0
    out = capsys.readouterr()
    assert out.out == '' and 'no waiting Verification batch' in out.err and 'stale' in out.err and github_output.read_text() == ''
    assert not [path for path, _ in cloud.gets if 'statistics' in path]


def test_discover_with_nothing_waiting_prints_nothing_and_exits_0(cloud, capsys):
    cloud.batch['state'] = 'Valid'
    assert discover() == 0
    out = capsys.readouterr()
    assert out.out == '' and 'no waiting Verification batch' in out.err


def test_discover_ignores_batches_of_other_services(cloud, capsys):
    cloud.batch = None
    cloud.others = [waiting('9.0.0', service='another-service')]
    assert discover() == 0
    out = capsys.readouterr()
    assert out.out == '' and 'no waiting Verification batch' in out.err


def test_discover_finds_a_batch_on_a_later_page(cloud, capsys):
    cloud.others = [waiting('1.0.0', f'other-{n}', service='another-service') for n in range(120)]
    assert discover() == 0
    assert json_line(capsys)[0]['batch_id'] == BATCH


def test_a_prerelease_is_below_its_release():
    keys = ['1.0.0-alpha', '1.0.0-alpha.1', '1.0.0-alpha.beta', '1.0.0-beta.2', '1.0.0-beta.11', '1.0.0-rc.1', '1.0.0']
    assert sorted(keys, key=verify_batch.semver) == keys and verify_batch.semver('latest') is None


def test_discover_leaves_out_a_version_that_is_not_semver(cloud, capsys):
    cloud.others = [waiting('nightly')]
    assert discover() == 0
    found, err = json_line(capsys)
    assert found['version'] == NEW and 'nightly' in err


def test_discover_reads_the_batch_when_the_list_cuts_its_items(cloud, capsys, monkeypatch):
    listed = [{'identity_id': f'layer-{n}', 'version': '1.0.0'} for n in range(3)]
    full = dict(cloud.batch, update_items=listed + cloud.batch['update_items'])
    cloud.batch = full
    answer = cloud.session

    def cut_session():
        session = answer()
        real = session.get

        def get(url, params=None, timeout=None):
            result = real(url, params=params, timeout=timeout)
            if url.endswith('/verification-batch/'):
                result = Answer(200, {'total': 1, 'offset': 0, 'items': [dict(full, update_items=listed)]})
            return result
        session.get = get
        return session
    monkeypatch.setattr(verify_batch.requests, 'Session', cut_session)
    assert discover() == 0
    assert json_line(capsys)[0]['version'] == NEW


@pytest.mark.parametrize('in_set, with_service', [([], [UNIT]), ([UNIT], []), ([UNIT], ['outside-the-set'])])
def test_no_unit_in_both_lists_exits_2_and_starts_nothing(cloud, capsys, github_output, in_set, with_service):
    cloud.verification_set = {'amd64': in_set}
    cloud.service_units = with_service
    assert discover() == 2
    out = capsys.readouterr()
    assert out.out == '' and 'no Verification Set Unit' in out.err and github_output.read_text() == ''


def test_units_outside_the_set_are_ignored_like_the_placeholder_rows_of_6_2(cloud, capsys):
    cloud.service_units = ['announced-only', UNIT, 'announced-too']
    assert discover() == 0
    assert json_line(capsys)[0]['units'] == [UNIT]


def test_a_unit_of_the_set_without_the_service_is_left_out_and_named(cloud, capsys):
    cloud.verification_set = {'amd64': [UNIT, 'unit-without-service']}
    assert discover() == 0
    found, err = json_line(capsys)
    assert found['units'] == [UNIT] and 'Service is not linked' in err


def test_service_units_are_read_page_by_page(cloud, capsys):
    late = 'unit-late'
    cloud.service_units = [f'unit-x{n}' for n in range(70)] + [late]
    cloud.verification_set = {'amd64': [late]}
    assert discover() == 0
    assert json_line(capsys)[0]['units'] == [late]


def test_units_of_several_architectures_are_joined_once(cloud, capsys):
    cloud.verification_set = {'amd64': [UNIT, 'unit-2'], 'arm64': ['unit-2', 'unit-3']}
    cloud.service_units = [UNIT, 'unit-2', 'unit-3']
    assert discover() == 0
    assert json_line(capsys)[0]['units'] == [UNIT, 'unit-2', 'unit-3']


def test_an_architecture_without_a_test_unit_exits_2(cloud, capsys):
    cloud.verification_set = {'amd64': [UNIT], 'arm64': []}
    assert discover() == 2
    out = capsys.readouterr()
    assert out.out == '' and 'no Verification Set Unit for arm64' in out.err


def test_dry_run_prints_and_writes_nothing_and_never_patches(cloud, capsys, github_output):
    cloud.others = [waiting('4.1.0')]
    assert discover('--dry-run') == 0
    found, err = json_line(capsys)
    assert found['version'] == NEW and 'stale' in err and f'version {NEW}, Waiting_validation' in err
    assert github_output.read_text() == '' and cloud.patches == []


def test_dry_run_with_no_test_unit_says_so_and_exits_0(cloud, capsys, github_output):
    cloud.verification_set = {'amd64': []}
    assert discover('--dry-run') == 0
    found, err = json_line(capsys)
    assert found['units'] == [] and 'no Verification Set Unit' in err and github_output.read_text() == ''


def test_discover_takes_the_service_id_from_the_environment(cloud, capsys, monkeypatch):
    monkeypatch.setenv('SERVICE_ID', SERVICE)
    assert main('discover', '--dry-run') == 0
    assert json_line(capsys)[0]['batch_id'] == BATCH


def test_discover_without_a_service_id_exits_2(cloud, capsys, monkeypatch):
    monkeypatch.delenv('SERVICE_ID', raising=False)
    assert main('discover') == 2 and 'no Service id' in capsys.readouterr().err and cloud.gets == []


def test_the_override_replaces_the_service_list(cloud, capsys):
    cloud.verification_set = {'amd64': [UNIT, 'unit-chosen']}
    assert discover('--unit', 'unit-chosen') == 0
    assert json_line(capsys)[0]['units'] == ['unit-chosen']
    assert not [path for path, _ in cloud.gets if path.startswith('services/')]


def test_an_override_outside_the_set_starts_nothing(cloud, capsys, github_output):
    assert discover('--unit', 'unit-outside') == 2
    out = capsys.readouterr()
    assert out.out == '' and 'not in the Verification Set' in out.err and github_output.read_text() == ''


def test_an_override_that_misses_an_architecture_starts_nothing(cloud, capsys):
    cloud.verification_set = {'amd64': [UNIT], 'arm64': ['unit-2']}
    assert discover('--unit', UNIT) == 2
    out = capsys.readouterr()
    assert out.out == '' and 'no Verification Set Unit for arm64' in out.err


def test_a_locked_batch_is_not_chosen(cloud, capsys):
    cloud.batch['is_locked'] = True
    assert discover() == 0
    out = capsys.readouterr()
    assert out.out == '' and 'is locked' in out.err


def test_with_two_builds_of_one_version_the_newest_decides(cloud, capsys):
    cloud.batch = None
    cloud.others = [waiting('1.0.0+b', state='Valid'), waiting('1.0.0+a')]
    assert discover() == 0
    assert capsys.readouterr().out == ''


def test_a_unit_id_from_the_server_cannot_add_an_output_line(cloud, capsys, github_output):
    cloud.verification_set = {'amd64': ['unit-1\nversion=9.9.9']}
    assert discover() == 2 and capsys.readouterr().out == '' and github_output.read_text() == ''


def test_a_trailing_newline_is_not_a_version_and_an_inner_one_is_not_an_id():
    assert verify_batch.semver('1.0.0\n') is None
    with pytest.raises(verify_batch.Stop):
        verify_batch.checked_ids(['unit\nb'])


@pytest.mark.parametrize('error', [403, 503, requests.ConnectionError('refused')])
def test_discover_on_an_error_exits_2_with_nothing_on_stdout(cloud, capsys, github_output, error):
    cloud.fail_get = error
    assert discover() == 2
    out = capsys.readouterr()
    assert out.out == '' and 'start nothing' in out.err and github_output.read_text() == ''


def test_an_odd_batch_id_goes_nowhere(cloud, capsys, github_output):
    cloud.batch['id'] = 'x\nversion=9.9.9'
    assert discover() == 2 and capsys.readouterr().out == '' and github_output.read_text() == ''


# the steps of the workflows, run with bash and stand-ins for python and gh

WORKFLOW = ROOT / '.github' / 'workflows' / 'verify-batch.yml'
WATCH = ROOT / '.github' / 'workflows' / 'watch-batches.yml'
# The partner zip leaves out the demo files (.gitattributes, export-ignore): their tests run in the repository only.
needs_demo = pytest.mark.skipif(not (ROOT / 'TRY_IT.md').exists(), reason='the demo files are not in the partner zip')


def step(name: str, workflow: Path = WORKFLOW) -> str:
    """The run block of the workflow step whose name starts with ``name``."""
    lines = workflow.read_text(encoding='utf-8').splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip().startswith(f'- name: {name}'))
    run = next(i for i in range(start, len(lines)) if lines[i].strip() == 'run: |')
    body = []
    for line in lines[run + 1:]:
        if line.strip() and not line.startswith(' ' * 10):
            break
        body.append(line[10:])
    return '\n'.join(body) + '\n'


def run_wait_step(tmp_path, code, stdout, units='unit-1', user_pem='PEM', batch_input='', service=SERVICE, override='',
                  actor='a-person'):
    fake = tmp_path / 'bin'
    fake.mkdir(exist_ok=True)
    (fake / 'python').write_text(f"""#!/usr/bin/env bash
test -s "$AOS_CERT" || exit 9
cp "$AOS_CERT" "{tmp_path}/seen_pem"
printf '%s\\n' "$@" > "{tmp_path}/args"
prev=
for a in "$@"; do
  if [ "$prev" = --units-file ]; then printf 'unit-from-the-set' > "$a"; fi
  prev=$a
done
printf "%s" "{stdout}"
exit {code}
""")
    (fake / 'python').chmod(0o755)
    (tmp_path / 'aos').mkdir(exist_ok=True)
    output = tmp_path / 'output'
    output.write_text('')
    env = {'PATH': f"{fake}:{os.environ['PATH']}", 'RUNNER_TEMP': str(tmp_path), 'GITHUB_OUTPUT': str(output),
           'USER_PEM': user_pem, 'INPUT_UNITS': units, 'OVERRIDE_UNITS': override, 'WAIT_TIMEOUT': '1',
           'SERVICE_ID': service, 'VERSION': NEW, 'BATCH_INPUT': batch_input, 'ACTOR': actor}
    run = subprocess.run(['bash', '-e', '-c', step('5. Wait')], env=env, capture_output=True, text=True, timeout=60,
                         check=False)
    return run, output.read_text(), (tmp_path / 'aos' / 'user.pem').exists()


needs_bash = pytest.mark.skipif(shutil.which('bash') is None or os.name == 'nt', reason='needs bash')


@needs_bash
@pytest.mark.parametrize('code, stdout, passes', [(0, BATCH, True), (1, BATCH, True), (1, '', False),
                                                   (2, '', False), (3, '', False)])
def test_the_wait_step_never_ends_green_without_a_decision(tmp_path, code, stdout, passes):
    run, output, pem_left = run_wait_step(tmp_path, code, stdout)
    assert (run.returncode == 0) is passes and f'code={code}' in output and not pem_left


@needs_bash
def test_verify_batch_with_units_passes_them_on(tmp_path):
    run, output, _ = run_wait_step(tmp_path, 0, BATCH, units='unit-1 unit-2', batch_input='batch-1')
    args = (tmp_path / 'args').read_text().splitlines()
    assert run.returncode == 0 and args.count('--unit') == 2 and 'unit-2' in args
    assert args[args.index('--batch-id') + 1] == 'batch-1' and f'batch_id={BATCH}' in output


@needs_bash
def test_verify_batch_without_units_lets_wait_find_them(tmp_path):
    run, output, _ = run_wait_step(tmp_path, 0, BATCH, units='  ')
    args = (tmp_path / 'args').read_text().splitlines()
    assert run.returncode == 0 and '--unit' not in args and '--batch-id' not in args
    assert 'units=unit-from-the-set' in output


@needs_bash
@pytest.mark.parametrize('units, override, batch, actor, found_by, units_from', [
    ('u', '', 'b', 'github-actions[bot]', 'watch', 'the Verification Set'),
    ('u', 'u', 'b', 'github-actions[bot]', 'watch', 'AOS_TEST_UNITS'),
    ('u', '', 'b', 'a-person', 'version', 'the run input'),
    ('u', 'v', '', 'a-person', 'version', 'the run input'),
    ('', 'v', '', 'a-person', 'version', 'AOS_TEST_UNITS'),
    ('', '', '', 'a-person', 'version', 'the Verification Set'),
])
def test_the_report_is_told_who_found_the_batch_and_the_units(tmp_path, units, override, batch, actor, found_by,
                                                              units_from):
    run, output, _ = run_wait_step(tmp_path, 0, BATCH, units=units, override=override, batch_input=batch, actor=actor)
    assert run.returncode == 0 and f'found_by={found_by}\n' in output and f'units_from={units_from}\n' in output
    assert f'service_id={SERVICE}\n' in output


@needs_bash
def test_the_override_is_used_when_the_input_is_empty(tmp_path):
    run_wait_step(tmp_path, 0, BATCH, units='', override='unit-chosen')
    assert 'unit-chosen' in (tmp_path / 'args').read_text().splitlines()


@needs_bash
def test_a_star_in_the_units_is_not_expanded(tmp_path):
    (tmp_path / 'somefile').write_text('')
    run_wait_step(tmp_path, 0, BATCH, units='*')
    assert '*' in (tmp_path / 'args').read_text().splitlines()


# the watch

FAKE_GH = r"""#!/usr/bin/env bash
echo "gh $*" >> "$GH_CALLS"
if [ "$1 $2" = "run list" ]; then
  [ "${GH_FAIL:-}" = list ] && exit 1
  cat "$GH_RUNS"
elif [ "$1 $2" = "workflow run" ]; then
  [ "${GH_FAIL:-}" = dispatch ] && exit 1
  version= batch=
  for a in "$@"; do case "$a" in version=*) version=${a#version=} ;; batch_id=*) batch=${a#batch_id=} ;; esac; done
  jq --arg t "verify-batch ${batch:-manual} $version" '. + [{displayTitle: $t, status: "queued"}]' "$GH_RUNS" > "$GH_RUNS.new"
  mv "$GH_RUNS.new" "$GH_RUNS"
fi
"""


def run_start_step(tmp_path, runs=(), batch='batch-aaaa', version=NEW, fail=''):
    fake = tmp_path / 'bin'
    fake.mkdir(exist_ok=True)
    (fake / 'gh').write_text(FAKE_GH)
    (fake / 'gh').chmod(0o755)
    runs_file, calls = tmp_path / 'runs.json', tmp_path / 'calls'
    if not runs_file.exists():
        runs_file.write_text(json.dumps(list(runs)))
    env = {'PATH': f"{fake}:{os.environ['PATH']}", 'GH_RUNS': str(runs_file), 'GH_CALLS': str(calls), 'GH_FAIL': fail,
           'BATCH_ID': batch, 'VERSION': version, 'UNITS': 'unit-1 unit-2', 'GITHUB_REF_NAME': 'the-branch'}
    run = subprocess.run(['bash', '-e', '-c', step('Start one', WATCH)], env=env, capture_output=True, text=True,
                         timeout=60, check=False)
    return run, calls.read_text().splitlines()


def dispatches(calls):
    return [call for call in calls if call.startswith('gh workflow run')]


needs_jq = pytest.mark.skipif(shutil.which('jq') is None or shutil.which('bash') is None, reason='needs bash and jq')


@needs_jq
def test_the_watch_starts_one_run_with_the_version_the_batch_and_the_units(tmp_path):
    run, calls = run_start_step(tmp_path)
    assert run.returncode == 0, run.stderr
    assert dispatches(calls) == [f'gh workflow run verify-batch.yml --ref the-branch -f version={NEW} -f batch_id=batch-aaaa '
                                 f'-f units=unit-1 unit-2']
    assert '--limit 200' in calls[0]


@needs_jq
def test_two_discovers_on_one_batch_start_one_run(tmp_path):
    assert len(dispatches(run_start_step(tmp_path)[1])) == 1
    run, calls = run_start_step(tmp_path)
    assert 'already has a run' in run.stdout and len(dispatches(calls)) == 1


@needs_jq
@pytest.mark.parametrize('status', ['queued', 'in_progress', 'completed'])
def test_the_watch_skips_an_existing_run_in_any_state(tmp_path, status):
    run, calls = run_start_step(tmp_path, [{'displayTitle': f'verify-batch batch-aaaa {NEW}', 'status': status}])
    assert run.returncode == 0 and status in run.stdout and dispatches(calls) == []


@needs_jq
def test_the_watch_skips_a_run_started_by_hand_for_the_version(tmp_path):
    _, calls = run_start_step(tmp_path, [{'displayTitle': f'verify-batch manual {NEW}', 'status': 'completed'}])
    assert dispatches(calls) == []


@needs_jq
@pytest.mark.parametrize('title', ['verify-batch batch-bbbb 4.5.0', 'verify-batch batch-aaaa-2 4.5.0', 'verify-batch manual 4.5.0',
                                   f'verify-batch batch-bbbb {NEW}', 'some other title'])
def test_the_watch_does_not_skip_for_another_batch(tmp_path, title):
    _, calls = run_start_step(tmp_path, [{'displayTitle': title, 'status': 'completed'}])
    assert len(dispatches(calls)) == 1


@needs_jq
def test_when_the_run_list_fails_the_watch_starts_nothing(tmp_path):
    run, calls = run_start_step(tmp_path, fail='list')
    assert run.returncode != 0 and dispatches(calls) == []


def workflow_step(name: str, workflow: Path) -> str:
    """The whole text of a step: its name, env and run block."""
    text = workflow.read_text(encoding='utf-8')
    start = text.index(f'- name: {name}')
    match = re.compile(r'^ {6}(- |# )', re.M).search(text, start + 1)
    return text[start:match.start()] if match else text[start:]


@needs_jq
def test_the_first_step_masks_the_inputs_without_an_env(tmp_path):
    lines = WORKFLOW.read_text(encoding='utf-8')
    assert lines.index('- name: Hide the ids') < lines.index('actions/checkout')
    assert 'env:' not in workflow_step('Hide the ids', WORKFLOW)
    event = tmp_path / 'event.json'
    event.write_text(json.dumps({'inputs': {'version': NEW, 'batch_id': 'b-1', 'units': 'u-1 u-2', 'service_id': ''}}))
    run = subprocess.run(['bash', '-e', '-c', step('Hide the ids')], env={**os.environ, 'GITHUB_EVENT_PATH': str(event)},
                         capture_output=True, text=True, timeout=60, check=False)
    assert run.returncode == 0, run.stderr
    assert run.stdout.split() == ['::add-mask::b-1', '::add-mask::u-1', '::add-mask::u-2']


def test_your_tests_step_has_no_secret_and_only_the_verdict_decides():
    yours = workflow_step('6b.', WORKFLOW)
    assert 'secrets.' not in yours and 'USER_PEM' not in yours and 'RUN_PART: yours' in yours
    assert 'SERVICE_ID: ${{ steps.wait.outputs.service_id }}' in yours
    assert 'secrets.AOS_USER_PEM' in workflow_step('6a.', WORKFLOW)
    text = WORKFLOW.read_text(encoding='utf-8')
    assert "steps.tests.outputs.code == '0'" in workflow_step('7a.', WORKFLOW)
    assert "steps.tests.outputs.code == '1'" in text and 'persist-credentials: false' in text


def test_only_the_discover_step_of_the_watch_has_the_key():
    text = WATCH.read_text(encoding='utf-8')
    # twice: the trim step only checks that it is there, step 4 writes it
    assert text.count('secrets.AOS_USER_PEM') == 2 and 'secrets.AOS_USER_PEM' in workflow_step('4. Ask', WATCH)
    assert '$USER_PEM' not in workflow_step('Check and trim', WATCH).split('if [ -z "$USER_PEM" ]', 1)[1]
    assert 'GH_TOKEN' not in workflow_step('4. Ask', WATCH) and 'persist-credentials: false' in text
    assert 'secrets.' not in workflow_step('Start one', WATCH)


def test_workflow_expressions_never_go_straight_into_a_shell():
    for workflow in (WORKFLOW, WATCH, ROOT / '.github' / 'workflows' / 'demo-upload.yml'):
        if not workflow.exists():
            continue
        for line in workflow.read_text(encoding='utf-8').splitlines():
            if '${{' in line:
                assert re.match(r'^\s*(- )?[A-Za-z_-]+: |^\s*if: ', line) and 'run:' not in line, line


@needs_bash
def test_the_discover_step_deletes_the_key_and_calls_discover(tmp_path):
    fake = tmp_path / 'bin'
    fake.mkdir()
    (fake / 'python').write_text('#!/usr/bin/env bash\ntest -s "$AOS_CERT" && test -s "$AOS_CA" || exit 9\n'
                                 'echo "$*" > "$CALLED"\nexit "${FAKE_CODE:-0}"\n')
    (fake / 'python').chmod(0o755)
    (tmp_path / 'aos').mkdir()
    (tmp_path / 'aos' / 'aos-root-ca.crt').write_text('CA')
    env = {'PATH': f"{fake}:{os.environ['PATH']}", 'RUNNER_TEMP': str(tmp_path), 'USER_PEM': 'PEM',
           'AOS_CA': str(tmp_path / 'aos' / 'aos-root-ca.crt'), 'TEST_UNITS': 'unit-chosen', 'CALLED': str(tmp_path / 'called'),
           'SERVICE_ID': 'service-chosen'}
    run = subprocess.run(['bash', '-e', '-c', step('4. Ask', WATCH)], env=env, capture_output=True, text=True,
                         timeout=60, check=False)
    assert run.returncode == 0, run.stderr
    assert (tmp_path / 'called').read_text().strip() == 'verify_batch.py discover --unit unit-chosen'
    assert not (tmp_path / 'aos').exists()
    (tmp_path / 'aos').mkdir()
    (tmp_path / 'aos' / 'aos-root-ca.crt').write_text('CA')
    run = subprocess.run(['bash', '-e', '-c', step('4. Ask', WATCH)], env={**env, 'FAKE_CODE': '2'}, capture_output=True,
                         text=True, timeout=60, check=False)
    assert run.returncode == 2 and not (tmp_path / 'aos').exists()


def test_both_starts_end_in_one_concurrency_group():
    text = WORKFLOW.read_text(encoding='utf-8')
    assert "group: verify-batch-${{ inputs.service_id || 'service' }}-${{ inputs.version }}" in text
    assert 'vars.AOS_SERVICE_ID' not in text + WATCH.read_text(encoding='utf-8')
    assert 'cancel-in-progress: false' in text and "run-name: verify-batch ${{ inputs.batch_id || 'manual' }} " in text
    caller = (ROOT / 'examples' / 'release-caller.yml').read_text(encoding='utf-8')
    assert 'service_id' not in caller and 'secrets: inherit' in caller
    assert 'uses: ./.github/workflows/verify-batch.yml' in caller and 'version:' in caller


def test_the_watch_asks_every_5_minutes_and_can_write_actions():
    text = WATCH.read_text(encoding='utf-8')
    assert re.findall(r'^\s+- cron: (.*)$', text, re.M) == ['"*/5 * * * *"'] and 'workflow_dispatch:' in text
    assert 'actions: write' in text and 'contents: read' in text


def test_the_docs_show_the_watch_as_it_is():
    setup = (ROOT / 'docs' / 'setup.md').read_text(encoding='utf-8')
    assert '[`.github/workflows/watch-batches.yml`](../.github/workflows/watch-batches.yml)' in setup
    for name in ('README.md', 'docs/setup.md', 'docs/how-it-works.md'):
        text = (ROOT / name).read_text(encoding='utf-8')
        assert '```yaml\n# Ask AosCloud on a schedule' not in text and '<details>' not in text


# check and status: read only

def test_check_prints_the_connection_the_latest_batch_and_the_unit(cloud, capsys):
    assert main('check', '--service-id', SERVICE, '--unit', UNIT) == 0
    out = capsys.readouterr().out
    assert 'connection OK' in out and f'version {NEW}, state Waiting_validation' in out
    assert f'installed {NEW}; instances: {NEW} active' in out and SERVICE not in out and UNIT not in out
    assert cloud.patches == []


def test_check_with_a_wrong_unit_id_exits_2_and_says_why(cloud, capsys):
    assert main('check', '--service-id', SERVICE, '--unit', 'unit-9') == 2
    assert 'HTTP 404: not found; check the id (the Unit id, not its System UID)' in capsys.readouterr().err


@pytest.mark.parametrize('status', [401, 403])
def test_check_with_the_wrong_certificate_role_exits_2_and_says_why(cloud, capsys, status):
    cloud.fail_get = status
    assert main('check', '--service-id', SERVICE) == 2
    assert 'use the SP user certificate of this AosCloud' in capsys.readouterr().err


def test_check_on_a_tls_error_exits_2(cloud, capsys):
    cloud.fail_get = requests.exceptions.SSLError('unknown ca')
    assert main('check', '--service-id', SERVICE) == 2
    assert 'TLS error' in capsys.readouterr().err


def status_lines(capsys):
    return [line.split('\t') for line in capsys.readouterr().out.splitlines()]


def test_status_reports_a1_b1_c2_as_pass(cloud, capsys):
    assert main('status', '--service-id', SERVICE, '--version', NEW, '--batch-id', BATCH, '--unit', UNIT) == 0
    assert [(code, result) for code, _, result, _ in status_lines(capsys)] == [('A1', 'PASS'), ('B1', 'PASS'),
                                                                              ('C2', 'PASS')]


def test_status_reports_a_failed_instance_as_c2_fail(cloud, capsys):
    cloud.unit = 'failed'
    assert main('status', '--service-id', SERVICE, '--version', NEW, '--batch-id', BATCH, '--unit', UNIT) == 1
    rows = {code: (result, detail) for code, _, result, detail in status_lines(capsys)}
    assert rows['B1'][0] == 'FAIL' and rows['C2'][0] == 'FAIL' and UNIT not in rows['C2'][1]


def test_status_reports_a_decided_batch_as_a1_fail(cloud, capsys):
    cloud.batch['state'] = 'Valid'
    assert main('status', '--service-id', SERVICE, '--version', NEW, '--batch-id', BATCH, '--unit', UNIT) == 1
    rows = {code: (result, detail) for code, _, result, detail in status_lines(capsys)}
    assert rows['A1'] == ('FAIL', 'Verification batch …ch-1 is Valid, not Waiting_validation')


# tests/run_tests.sh: the built-in checks, the observation, your tests and the report, with a stand-in for verify_batch.py

FAKE_PYTHON = r"""#!/usr/bin/env bash
case "$1" in
  */verify_batch.py)
    echo "$*" >> "$CALLS"
    if [ "$2" = status ]; then
      printf 'A1\texactly this Service and version\tPASS\tbatch holds it\n'
      printf 'B1\ton every listed Unit\tPASS\tactive on 1 of 1 Units\n'
      if [ "$FAKE_STATUS" = 0 ]; then printf 'C2\tno crash on start\tPASS\tno failed instance\n'
      else printf 'C2\tno crash on start\tFAIL\tfailed instance\n'; fi
      exit "$FAKE_STATUS"
    fi
    [ -n "$FAKE_WAIT_SAYS" ] && echo "$FAKE_WAIT_SAYS" >&2
    exit "$FAKE_WAIT" ;;
  *) exec "$REAL_PYTHON" "$@" ;;
esac
"""


def run_tests_sh(tmp_path, observe='60', status=0, wait_code=0, yours=None, batch='batch-1234567890', part='all',
                 extra=None, broken_report=False):
    tests = tmp_path / 'repo' / 'tests'
    tests.mkdir(parents=True, exist_ok=True)
    for name in ('run_tests.sh', 'report.py'):
        shutil.copy(ROOT / 'tests' / name, tests / name)
    if broken_report:
        (tests / 'report.py').write_text('import sys\nsys.exit("report.py: error")\n')
    if yours is not None:
        (tests / 'your_tests.sh').write_text(yours)
    fake = tmp_path / 'python'
    fake.write_text(FAKE_PYTHON)
    fake.chmod(0o755)
    calls = tmp_path / 'calls'
    env = {'PATH': os.environ['PATH'], 'PYTHON': str(fake), 'REAL_PYTHON': sys.executable, 'CALLS': str(calls),
           'FAKE_STATUS': str(status), 'FAKE_WAIT': str(wait_code), 'WATCH_INTERVAL': '0', 'WATCH_SECONDS': observe,
           'UNIT_ID': 'unit-abcdef123456', 'SERVICE_ID': 'service-abcdef123456', 'VERSION': NEW, 'BATCH': batch,
           'AOS_CERT': '/secret/user.pem', 'REPORT_DIR': str(tmp_path / 'report'), 'RUN_PART': part, **(extra or {})}
    run = subprocess.run(['bash', str(tests / 'run_tests.sh')], env=env, cwd=tmp_path, capture_output=True,
                         text=True, timeout=60, check=False)
    report = tmp_path / 'report' / 'report.html'
    return (run, calls.read_text().splitlines() if calls.exists() else [],
            report.read_text() if report.exists() else '')


@needs_bash
def test_our_checks_and_the_watch_run_by_default_and_write_the_report(tmp_path):
    run, calls, report = run_tests_sh(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert 'PASS A1' in run.stdout and 'PASS C3/C4' in run.stdout and 'all checks passed: approve' in run.stdout
    assert any(' status ' in c for c in calls) and sum(' wait ' in c for c in calls) == 1
    assert '--unit unit-abcdef123456 --timeout 60' in calls[-1]
    assert 'PASS → approve' in report and 'Scenarios covered' in report and 'Needs AosCloud 6.2' in report
    assert 'service-abcdef123456' not in report and 'unit-abcdef123456' not in report and '…3456' in report
    junit = (tmp_path / 'report' / 'junit.xml').read_text()
    assert 'tests="5"' in junit and 'failures="0"' in junit and 'skipped="1"' in junit


@needs_bash
@pytest.mark.parametrize('observe', ['60s', '1 min', ' 60', '-1'])
def test_a_watch_time_that_is_not_a_number_decides_nothing(tmp_path, observe):
    run, calls, report = run_tests_sh(tmp_path, observe=observe, extra={'WATCH_SECONDS': observe, 'AOS_WATCH_SECONDS': observe})
    assert run.returncode == 2 and 'must be a whole number of seconds' in run.stdout and not calls and not report


@needs_bash
def test_a_report_that_cannot_be_written_decides_nothing_and_never_rejects(tmp_path):
    run, _, _ = run_tests_sh(tmp_path, broken_report=True)
    assert run.returncode == 2 and 'the report could not be written: decide nothing' in run.stdout
    assert 'reject' not in run.stdout


@needs_bash
def test_the_watch_catches_a_version_that_stops_running(tmp_path):
    run, calls, report = run_tests_sh(tmp_path, observe='180', wait_code=1)
    assert run.returncode == 1 and 'FAIL C3/C4' in run.stdout and 'round 1 of 3' in run.stdout
    assert sum(' wait ' in c for c in calls) == 1 and 'FAIL → reject' in report


@needs_bash
def test_the_watch_can_be_turned_off(tmp_path):
    run, calls, _ = run_tests_sh(tmp_path, observe='0')
    assert run.returncode == 0 and 'SKIP C3/C4' in run.stdout and not any(' wait ' in c for c in calls)


@needs_bash
def test_a_failed_instance_found_again_fails_the_run(tmp_path):
    run, _, report = run_tests_sh(tmp_path, status=1)
    assert run.returncode == 1 and 'FAIL C2' in run.stdout and 'FAIL → reject' in report


@needs_bash
def test_an_outage_in_the_built_in_checks_decides_nothing(tmp_path):
    run, _, report = run_tests_sh(tmp_path, status=2)
    assert run.returncode == 3 and 'ERROR A1' in run.stdout and 'decide nothing' in run.stdout
    assert 'no decision' in report and 'FAIL → reject' not in report
    junit = (tmp_path / 'report' / 'junit.xml').read_text()
    assert 'errors="1"' in junit and '<error message=' in junit


@needs_bash
def test_an_outage_in_the_watch_decides_nothing(tmp_path):
    run, _, report = run_tests_sh(tmp_path, wait_code=3)
    assert run.returncode == 3 and 'ERROR C3/C4' in run.stdout and 'no decision' in report


@needs_bash
def test_a_failed_test_wins_over_an_outage(tmp_path):
    run, _, report = run_tests_sh(tmp_path, wait_code=3, yours='echo "FAIL b: wrong answer"\nexit 1\n')
    assert run.returncode == 1 and 'FAIL → reject' in report


@needs_bash
def test_by_parts_your_tests_get_the_built_in_results_and_no_key(tmp_path):
    yours = '#!/usr/bin/env bash\necho "PASS key ${USER_PEM:-no-key}"\n'
    run, _, _ = run_tests_sh(tmp_path, part='builtin', yours=yours, extra={'USER_PEM': 'THE-KEY'})
    assert run.returncode == 0 and 'report:' not in run.stdout and 'PASS A1' in run.stdout and 'PASS key' not in run.stdout
    run, _, report = run_tests_sh(tmp_path, part='yours', yours=yours)
    assert run.returncode == 0 and 'PASS key: no-key' in run.stdout and 'PASS A1' not in run.stdout
    assert 'THE-KEY' not in report and 'PASS → approve' in report and 'exactly this Service' in report


@needs_bash
def test_by_parts_a_failed_built_in_check_still_rejects(tmp_path):
    run_tests_sh(tmp_path, part='builtin', status=1)
    run, _, report = run_tests_sh(tmp_path, part='yours')
    assert run.returncode == 1 and 'FAIL → reject' in report


@needs_bash
def test_by_parts_an_outage_in_the_built_in_checks_decides_nothing(tmp_path):
    assert run_tests_sh(tmp_path, part='builtin', status=2)[0].returncode == 0
    run, _, report = run_tests_sh(tmp_path, part='yours')
    assert run.returncode == 3 and 'no decision' in report


@needs_bash
def test_by_parts_without_built_in_results_nothing_is_approved(tmp_path):
    run, _, report = run_tests_sh(tmp_path, part='yours')
    assert run.returncode == 3 and 'did not run' in run.stdout and 'no decision' in report


@needs_bash
def test_your_tests_are_run_without_the_certificate_and_reported(tmp_path):
    yours = '#!/usr/bin/env bash\necho "PASS health ${AOS_CERT:-no-cert}"\necho "PASS suite"\n'
    run, _, report = run_tests_sh(tmp_path, yours=yours)
    assert run.returncode == 0 and 'PASS health: no-cert' in run.stdout and 'partner tests: 2 of 2 passed' in report


@needs_bash
def test_a_failing_test_of_yours_rejects(tmp_path):
    run, _, report = run_tests_sh(tmp_path, yours='echo "PASS a"\necho "FAIL b: wrong answer"\nexit 1\n')
    assert run.returncode == 1 and 'FAIL b: wrong answer' in run.stdout and 'FAIL → reject' in report


@needs_bash
def test_your_tests_that_print_nothing_fail(tmp_path):
    run, _, _ = run_tests_sh(tmp_path, yours='exit 0\n')
    assert run.returncode == 1 and 'printed no PASS or FAIL line' in run.stdout


@needs_bash
def test_no_batch_stops_with_exit_2(tmp_path):
    run, calls, _ = run_tests_sh(tmp_path, batch='')
    assert run.returncode == 2 and 'BATCH is empty' in run.stdout and calls == []


def test_the_example_of_your_tests_has_no_test_that_always_passes():
    text = (ROOT / 'tests' / 'your_tests.sh.example').read_text()
    assert 'check health_endpoint curl' in text and 'pytest' in text and ' true' not in text


def test_the_report_verdict_is_white_on_its_colour():
    sys.path.insert(0, str(ROOT / 'tests'))
    import report  # noqa: E402  pylint: disable=import-outside-toplevel
    args = SimpleNamespace(service_id='service-abcdef', version=NEW, batch_id='batch-abcdef', units='unit-abcdef',
                           observe=60, found_by='watch', units_from='the Verification Set')
    page = report.render(args, [{'part': 'builtin', 'code': 'A1', 'name': 'a', 'result': 'PASS', 'detail': ''}], 'now')
    assert '.r.pass{' in page and '\n.pass{' not in page and '<div class="verdict pass">PASS → approve</div>' in page
    assert 'service-abcdef' not in page and '…cdef' in page
    assert f'found by the watch in GitHub: Verification batch …cdef, Service version {NEW}, Units from the Verification Set' in page


# What a person reads: never a whole id.
A_UUID = '4cd5d65e-0000-4000-8000-00000000bcb1'


def test_a_connection_error_with_an_id_in_its_url_is_shortened(capsys):
    verify_batch.tell(f'HTTPSConnectionPool: Max retries exceeded with url: /api/v11/verification-batch/{A_UUID}/')
    err = capsys.readouterr().err
    assert A_UUID not in err and 'verification-batch/…bcb1/' in err


def test_lines_for_a_command_hold_whole_ids_but_a_terminal_shows_them_short(capsys, monkeypatch):
    verify_batch.machine(A_UUID)
    assert capsys.readouterr().out.strip() == A_UUID
    monkeypatch.setattr(sys.stdout, 'isatty', lambda: True)
    verify_batch.machine(A_UUID)
    assert capsys.readouterr().out.strip() == '…bcb1'


def test_wait_says_the_state_of_each_unit_in_words(cloud, capsys):
    assert wait() == 0
    err = capsys.readouterr().err
    assert f'batch {verify_batch.short(BATCH)}: Unit {verify_batch.short(UNIT)} installed' in err and '{' not in err


def test_decide_names_the_state_as_the_ui_does(cloud, capsys):
    assert decide(approve=True) == 0
    assert capsys.readouterr().out.strip() == f'Verification batch {verify_batch.short(BATCH)} (amd64): approved, now Verified (Valid)'
    cloud.batch['state'] = 'Waiting_validation'
    assert decide(approve=False) == 0
    assert capsys.readouterr().out.strip() == f'Verification batch {verify_batch.short(BATCH)} (amd64): rejected, now Invalid'


def report_args(tmp_path, *more):
    results = tmp_path / 'results.tsv'
    if not results.exists():
        results.write_text('builtin\tA1\tthe batch\tPASS\tok\nbuiltin\tB1\ton every\tPASS\tok\n'
                           'builtin\tC2\tno crash\tPASS\tok\nbuiltin\tC3/C4\tstill running\tPASS\tok\n'
                           'yours\t\tpartner tests\tSKIP\tnone\n')
    return ['--out', str(tmp_path / 'r'), '--batch-id', 'batch-abcdef', *more]


def write_report(tmp_path, found_by='run', observe='60'):
    sys.path.insert(0, str(ROOT / 'tests'))
    import report  # noqa: E402  pylint: disable=import-outside-toplevel
    args = report_args(tmp_path, '--results', str(tmp_path / 'results.tsv'), '--service-id', 'service-abcdef',
                       '--version', NEW, '--units', 'unit-abcdef', '--observe', observe, '--found-by', found_by,
                       '--units-from', 'the Verification Set')
    assert report.main(args) == 0
    return report


def test_the_report_explains_itself(tmp_path):
    write_report(tmp_path)
    page = (tmp_path / 'r/report.html').read_text(encoding='utf-8')
    for text in ('<b>A</b> = upload and Verification batch', '<b>B</b> = delivery to the Verification Set Units', '<b>C</b> = the Service runs',
                 '<b>D</b> = next to other Services', '<b>E</b> = configuration and recovery',
                 '= checked, good', '= checked, bad, leads to reject', '= not run',
                 '4 passed, 0 skipped, 0 failed; partner tests: none added (tests/your_tests.sh)',
                 'no restarts in 60 s; memory is not measured', 'Found automatically (discover)'):
        assert text in page, text
    assert 'by wait' not in page and 'memory' not in page.split('Scenarios covered')[0]
    assert 'verify_batch.py decide' not in page and './aos-verify decide approve|reject' in page
    assert page.count('Did not happen in this run') == 3  # A3, B2, E2


def test_the_status_column_has_only_the_four_values(tmp_path):
    write_report(tmp_path)
    page = (tmp_path / 'r/report.html').read_text(encoding='utf-8').split('Scenarios covered')[1]
    statuses = set(re.findall(r'<span class="s \w+">([^<]+)</span>', page))
    assert statuses == {'Checked now', 'Covered by the waiting logic', 'Not run', 'Needs AosCloud 6.2', 'Next Service version'}


@pytest.mark.parametrize('found_by, says, status', [
    ('watch', 'found by the watch in GitHub', 'Checked now'),
    ('run', 'found by ./aos-verify on this computer', 'Checked now'),
    ('version', 'started with the Service version, so nothing had to find the Verification batch',
     'Not needed \\(Start A\\)')])
def test_the_report_says_who_found_the_batch(tmp_path, found_by, says, status):
    write_report(tmp_path, found_by=found_by)
    page = (tmp_path / 'r/report.html').read_text(encoding='utf-8')
    assert says in page and re.search(f'<td>Found automatically \\(discover\\)</td><td><span[^>]*>{status}</span>', page)
    if found_by == 'version':
        assert '<span class="s skip">Not needed (Start A)</span>' in page


@pytest.mark.parametrize('decision, more, says', [
    ('approved', ['--state', 'Valid'], 'batch now Verified'),
    ('rejected', ['--state', 'Invalid'], 'batch now Invalid'),
    ('not-sent', ['--why', 'you answered no'], 'Decision: not sent (you answered no).'),
    ('report-only', [], 'Decision: report-only, nothing sent.'),
    ('unknown', [], 'do not send it again')])
def test_the_decision_is_written_into_the_report(tmp_path, decision, more, says):
    report = write_report(tmp_path)
    before = (tmp_path / 'r/report.html').read_text(encoding='utf-8')
    assert 'Decision: not sent yet' in before
    assert report.main(report_args(tmp_path, '--decision', decision, *more)) == 0
    page = (tmp_path / 'r/report.html').read_text(encoding='utf-8')
    assert says in page and 'not sent yet' not in page
    assert '<div class="verdict pass">PASS → ' in page and 'batch …cdef' in page and '…cdef' in page


def test_a_decision_leaves_the_report_of_another_batch_alone(tmp_path):
    report = write_report(tmp_path)
    before = (tmp_path / 'r/report.html').read_text(encoding='utf-8')
    args = ['--out', str(tmp_path / 'r'), '--batch-id', 'batch-other', '--decision', 'rejected', '--state', 'Invalid']
    assert report.main(args) == 0 and (tmp_path / 'r/report.html').read_text(encoding='utf-8') == before
    assert report.main(['--out', str(tmp_path / 'none'), '--batch-id', 'b', '--decision', 'rejected']) == 0


def test_decide_writes_its_outcome_into_the_report(cloud, tmp_path):
    (tmp_path / 'results.tsv').write_text('builtin\tA1\tthe batch\tPASS\tok\n')
    sys.path.insert(0, str(ROOT / 'tests'))
    import report  # noqa: E402  pylint: disable=import-outside-toplevel
    report.main(['--out', str(tmp_path), '--results', str(tmp_path / 'results.tsv'), '--service-id', SERVICE,
                 '--version', NEW, '--batch-id', BATCH, '--units', UNIT])
    args = SimpleNamespace(batch_id=BATCH, approve=True, arch=None, report_dir=str(tmp_path))
    assert verify_batch.decide(args) == 0
    assert 'Decision: approved,' in (tmp_path / 'report.html').read_text(encoding='utf-8')
    assert verify_batch.decide(args) == 2  # already decided: refused, and the report says so
    assert 'Decision: not sent (the Verification batch is already Valid)' in (tmp_path / 'report.html').read_text(encoding='utf-8')


def test_times_are_in_utc_with_the_local_time_in_brackets(monkeypatch):
    sys.path.insert(0, str(ROOT / 'tests'))
    import report  # noqa: E402  pylint: disable=import-outside-toplevel
    now = report.datetime(2026, 11, 5, 10, 15, tzinfo=report.timezone.utc)
    if not hasattr(time, 'tzset'):
        pytest.skip('no tzset on this system')
    for zone, says in (('Europe/Kyiv', '2026-11-05 10:15 UTC (12:15 local)'), ('UTC', '2026-11-05 10:15 UTC'),
                       ('Asia/Tokyo', '2026-11-05 10:15 UTC (19:15 local)'),
                       ('Pacific/Kiritimati', '2026-11-05 10:15 UTC (2026-11-06 00:15 local)')):
        monkeypatch.setenv('TZ', zone)
        time.tzset()
        assert report.stamp(now) == says
    monkeypatch.undo()
    time.tzset()


@needs_bash
def test_a_version_that_failed_gets_a_report_and_no_watch_and_not_your_tests(tmp_path):
    run, calls, report = run_tests_sh(tmp_path, status=1, yours='echo "PASS never"\n', extra={'VERSION_FAILED': 'yes'})
    assert run.returncode == 1 and 'never' not in run.stdout and not any(' wait ' in c for c in calls)
    assert 'FAIL → reject' in report and 'not run: the Service version already failed' in report


@needs_bash
def test_what_wait_saw_fail_stays_a_fail_even_if_aoscloud_reads_well_later(tmp_path):
    run, _, report = run_tests_sh(tmp_path, status=0, extra={'VERSION_FAILED': 'yes'})
    assert run.returncode == 1 and 'FAIL → reject' in report and 'wait saw the Service version fail' in report


def test_the_job_decides_by_itself_unless_told_otherwise():
    text = (ROOT / '.github/workflows/verify-batch.yml').read_text(encoding='utf-8')
    assert "AOS_DECISION: ${{ vars.AOS_DECISION || 'auto' }}" in text
    approve, reject = text.split('- name: 7a. Approve')[1], text.split('- name: 7b. Reject')[1]
    assert "env.AOS_DECISION == 'auto'" in approve.split('run: |')[0]
    assert "env.AOS_DECISION == 'auto'" in reject.split('run: |')[0]
    assert text.count('verify_batch.py decide') == 2 and text.count('--report-dir report') == 2
    assert "env.AOS_DECISION != 'auto'" in text.split('- name: 7. No decision')[1].split('run: |')[0]
    assert text.index('- name: Keep the report') > text.index('- name: 7. No decision')


def scenario_row(page, code):
    """The cells of one row of Scenarios covered, without the tags inside them."""
    rows = page.split('Scenarios covered')[1].split('<tr>')[2:]
    row = next(r for r in rows if r.startswith(f'<td>{code}</td>'))
    return [re.sub('<[^>]+>', '', cell) for cell in row.split('</td>')[:5]]


def test_a2_is_checked_and_e2_waits_for_the_next_version(tmp_path):
    write_report(tmp_path)
    page = (tmp_path / 'r/report.html').read_text(encoding='utf-8')
    assert scenario_row(page, 'A2')[2] == 'Checked now' and scenario_row(page, 'E2')[2] == 'Next Service version'


@pytest.mark.parametrize('code, change', [
    ('4', '—'), ('A1', '—'), ('A2', '—'), ('A3', '—'), ('A4', '—'), ('B1', 'AOS_WAIT_TIMEOUT'), ('B2', 'AOS_WAIT_TIMEOUT'),
    ('C1', 'your_tests.sh'), ('C2', '—'), ('C3', 'AOS_WATCH_SECONDS'), ('C4', 'AOS_WATCH_SECONDS, your_tests.sh'),
    ('D1', '—'), ('D2', '—'), ('E1', 'your_tests.sh'), ('E2', '—')])
def test_the_report_says_what_you_can_change(tmp_path, code, change):
    write_report(tmp_path)
    page = (tmp_path / 'r/report.html').read_text(encoding='utf-8')
    assert 'You can change it' in page and scenario_row(page, code)[4] == change


def test_each_status_has_its_colour(tmp_path):
    write_report(tmp_path)
    page = (tmp_path / 'r/report.html').read_text(encoding='utf-8')
    for name, kind in (('Checked now', 'pass'), ('Covered by the waiting logic', 'skip'), ('Needs AosCloud 6.2', 'skip'),
                       ('Not run', 'amber'), ('Next Service version', 'skip')):
        assert f'<span class="s {kind}">{name}</span>' in page
    assert '.s.pass{background:#DDF3E6' in page and '.s.skip{background:#ECEFF3' in page and '.s.amber{background:#FDF0D5' in page


@needs_bash
def test_an_observation_that_is_off_is_not_called_the_watch(tmp_path):
    run, calls, report = run_tests_sh(tmp_path, extra={'WATCH_SECONDS': '', 'AOS_WATCH_SECONDS': '0'})
    assert run.returncode == 0 and not any(' wait ' in c for c in calls)
    says = '60-second observation was off, AOS_WATCH_SECONDS=0'
    for code in ('C3', 'C4'):
        row = scenario_row(report, code)
        assert row[2] == 'Not run' and row[3] == says and 'watch' not in row[3].replace('AOS_WATCH_SECONDS', '')
    assert says in report.split('Scenarios covered')[0]


@needs_bash
def test_an_observation_skipped_for_a_failed_version_says_that_not_off(tmp_path):
    _, _, report = run_tests_sh(tmp_path, status=1, extra={'VERSION_FAILED': 'yes'})
    assert scenario_row(report, 'C3')[3] == 'not run: the Service version already failed on a Verification Set Unit'
    assert 'AOS_WATCH_SECONDS=0' not in report


@needs_bash
def test_the_60_second_look_is_called_observing_everywhere(tmp_path):
    run, _, report = run_tests_sh(tmp_path)
    assert 'observing for 60 s' in run.stdout and 'watch' not in run.stdout.lower()
    # Started with the version here, so the job that finds a batch had no part in it. Only the name of the setting stays.
    assert 'watch' not in report.replace('AOS_WATCH_SECONDS', '').lower()


# Impossible to miss: the decision mode, your tests, and why the watch asks every 5 minutes.
BOX = ('Defaults: the job decides by itself (AOS_DECISION=auto). For a decision by a person, set '
       'AOS_DECISION=report-only: the job only checks and writes the report, and a person approves or '
       'rejects in the SP UI. Or ask (laptop only).',
       'Built-in checks run on every Service version (what they check). Add your '
       'own tests in tests/your_tests.sh (start from tests/your_tests.sh.example): they run after the '
       'built-in checks. If any check or test fails, the verdict is reject; in auto mode the job sends it, '
       'in report-only a person decides.')
WHY = ('AosCloud does not notify a pipeline when a new Service version waits (no webhook), so the watch asks'
       ' every 5 minutes. 5 minutes is the shortest interval GitHub allows. GitHub may start runs late or '
       'skip them; for a guaranteed start use Start A. Each run takes about 20 s and only reads. To change '
       'it, edit one line in watch-batches.yml: cron: "*/5 * * * *" → "*/10 * * * *" (every 10 min), "*/30 *'
       ' * * *", "0 * * * *" (hourly). GitHub cannot read this from a variable. Faster than 5 min, or exact '
       'timing: start verify-batch right after your upload job (Start A), or use an external '
       'scheduler. A webhook from AosCloud would make it instant (feature request).')


def plain(text: str) -> str:
    """The words a person reads, from Markdown or HTML."""
    return html.unescape(re.sub(r'<[^>]+>', '', text.replace('`', '').replace('**', '')))


def start_here_text() -> str | None:
    """START_HERE.html is built into the zip only: the zip has the file, the source repo has the builder."""
    if (ROOT / 'tools' / 'build_start_here.py').exists():
        sys.path.insert(0, str(ROOT / 'tools'))
        import build_start_here  # pylint: disable=import-outside-toplevel
        return build_start_here.build()
    if (ROOT / 'START_HERE.html').exists():
        return (ROOT / 'START_HERE.html').read_text(encoding='utf-8')
    return None


def start_here_page(page_id: str) -> str:
    page = start_here_text()
    if page is None:
        return ''
    section = page[page.index(f'<section class="page" id="{page_id}">'):]
    return section[:section.index('</section>')]


def test_the_defaults_are_in_decision_modes_and_start_here():
    page = (ROOT / 'docs' / 'how-it-works.md').read_text(encoding='utf-8')
    box = page[page.index('## Decision modes'):page.index('One setting, `AOS_DECISION`')]
    box = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', box)  # a link reads as its words
    for line in BOX:
        assert line in plain(box), line
        if start_here_text() is not None:
            assert line in plain(start_here_page('how-it-works')), line
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert 'Defaults:' not in readme and '[!IMPORTANT]' not in readme
    assert '](docs/how-it-works.md#decision-modes)' in readme


def test_why_every_5_minutes_says_the_same_everywhere():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert 'Why every 5 minutes' not in readme
    assert WHY in plain((ROOT / 'docs/faq.md').read_text(encoding='utf-8'))
    assert WHY in plain((ROOT / 'docs/how-it-works.md').read_text(encoding='utf-8'))
    text = WATCH.read_text(encoding='utf-8')
    above = text[text.index('schedule:'):text.index('- cron:')]
    assert WHY in ' '.join(line.strip().lstrip('# ') for line in above.splitlines()[1:])


@pytest.mark.parametrize('name', ['README.md', 'START_HERE.html', 'docs/faq.md', 'docs/run-locally.md', 'docs/setup.md',
                                  'docs/how-it-works.md', 'docs/img/sequence.svg'])
def test_every_5_minutes_is_the_default_in_the_docs(name):
    source = start_here_text() if name == 'START_HERE.html' else (ROOT / name).read_text(encoding='utf-8')
    if source is None:
        pytest.skip('START_HERE.html is built into the zip only')
    text = re.sub(r'\s*\n\s*#?\s*', ' ', plain(source))
    # the only 10 left is the example of how to change it
    text = text.replace('"*/10 * * * *" (every 10 min)', '')
    assert not re.search(r'\*/10|(every|why|about) 10 min', text, re.I)


def summary_of(workflow: Path, step: str, tmp_path, **env) -> list[str]:
    """Run one step that writes the job summary; its lines."""
    text = workflow.read_text(encoding='utf-8')
    block = text.split(f'- name: {step}\n')[1].split('\n\n')[0]
    script = textwrap.dedent(block.split('run: ', 1)[1].removeprefix('|\n'))
    out = tmp_path / 'summary'
    subprocess.run(['bash', '-c', script], check=True, env={**os.environ, 'GITHUB_STEP_SUMMARY': str(out), **env})
    return out.read_text(encoding='utf-8').splitlines()


def test_the_decision_mode_values_are_next_to_it_and_only_the_watch_says_why():
    text = WORKFLOW.read_text(encoding='utf-8')
    above = text[:text.index('AOS_DECISION: ${{')].rsplit('VERSION: ${{', 1)[1]
    for value in ('auto', 'report-only', 'ask'):
        assert re.search(rf'#\s+{value}\s+\S', above), value
    assert 'Why every 5 minutes' not in text and 'Say the decision mode' not in text
    shown = workflow_step('Show the report on the run page', WORKFLOW)
    assert text.count('GITHUB_STEP_SUMMARY') == shown.count('GITHUB_STEP_SUMMARY') == 2
    assert text.index('- name: Show the report') > text.index('- name: Keep the report') > text.index('- name: 7. No decision')
    assert "if: always()" in shown


def show_summary(tmp_path, code='0', wait_log=None, **env):
    """Run the last step of verify-batch in tmp_path: what the run page shows."""
    out, temp = tmp_path / 'summary', tmp_path / 'temp'
    (temp / 'aos').mkdir(parents=True, exist_ok=True)
    if wait_log is not None:
        (temp / 'aos' / 'wait.log').write_text(wait_log)
    subprocess.run(['bash', '-e', '-c', step('Show the report on the run page')], check=True, cwd=tmp_path,
                   env={**os.environ, 'GITHUB_STEP_SUMMARY': str(out), 'RUNNER_TEMP': str(temp), 'CODE': code,
                        'VERSION': NEW, 'AOS_DECISION': 'auto', 'BATCH_ID': env.get('batch', '')})
    return out.read_text(encoding='utf-8')


def test_the_run_page_shows_the_whole_report_after_the_decision(tmp_path):
    report = write_report(tmp_path)
    assert report.main(report_args(tmp_path, '--decision', 'approved', '--state', 'Valid')) == 0
    (tmp_path / 'report').mkdir()
    (tmp_path / 'report' / 'report.md').write_text((tmp_path / 'r' / 'report.md').read_text(encoding='utf-8'))
    shown = show_summary(tmp_path)
    assert shown.startswith('### Verification report\n\n# ✅ PASS → approved, now Verified\n')
    for row in ('| Service | …cdef |', f'| Service version | {NEW} |', '| Verification batch | …cdef |',
                '| Verification Set Units | …cdef |', '| Report time | '):
        assert row in shown, row
    assert shown.index('### Built-in checks') < shown.index('### Partner tests') < shown.index('### Scenarios covered')
    assert '| A1 | the batch | **PASS** | ok |' in shown and '| C3 | Late failure | Checked now |' in shown
    assert shown.rstrip().splitlines()[-1] == 'Full report.html and junit.xml: Artifacts at the bottom of this page'


@pytest.mark.parametrize('decision, state, line', [
    ('approved', 'Valid', 'PASS → approved, now Verified'), ('rejected', 'Invalid', 'FAIL → rejected, now Invalid'),
    ('report-only', None, 'PASS → approve; report-only: not sent'), ('not-sent', None, 'PASS → approve; not sent'), (None, None, 'PASS → approve')])
def test_the_summary_and_report_html_say_the_same(tmp_path, decision, state, line):
    if decision == 'rejected':
        (tmp_path / 'results.tsv').write_text('builtin\tA1\tthe batch\tPASS\tok\nbuiltin\tC2\tno crash\tFAIL\tcrashed\n')
    report = write_report(tmp_path)
    if decision:
        assert report.main(report_args(tmp_path, '--decision', decision, *(['--state', state] if state else []))) == 0
    page = (tmp_path / 'r/report.html').read_text(encoding='utf-8')
    markdown = (tmp_path / 'r/report.md').read_text(encoding='utf-8')
    assert f'">{line}</div>' in page and markdown.splitlines()[2].startswith('# ') and line in markdown.splitlines()[2]
    for row in report.read_results(tmp_path / 'results.tsv'):
        assert row['name'] in page and row['name'] in markdown
    assert html.unescape(re.search(r'<p class="summary">(.*?)</p>', page)[1]) in markdown
    assert html.unescape(re.search(r'<p class="decision">(.*?)</p>', page)[1]) in markdown
    for code, _ in report.SCENARIOS:
        assert f'| {code} |' in markdown


def test_a_pipe_or_a_tag_in_a_result_does_not_break_the_summary(tmp_path):
    (tmp_path / 'results.tsv').write_text('builtin\tA1\tthe batch\tPASS\tok\nyours\t\thealth\tFAIL\tgot <b>|500|</b>\n')
    write_report(tmp_path)
    markdown = (tmp_path / 'r/report.md').read_text(encoding='utf-8')
    assert '| health | **FAIL** | got &lt;b&gt;\\|500\\|&lt;/b&gt; |' in markdown


@pytest.mark.parametrize('code, head', [('2', 'No decision: timeout, or nothing to verify'),
                                        ('3', 'No decision: timeout, or nothing to verify'),
                                        ('', 'No decision: the job stopped before the checks')])
def test_with_no_report_the_run_page_says_no_decision_and_why(tmp_path, code, head):
    shown = show_summary(tmp_path, code=code, wait_log='waiting\ntimed out after 1800 s: Unit …318d still installs 4.6.0\n',
                         batch='batch-abcdef')
    assert f'\n# {head}\n' in shown and f'| Service version | {NEW} |' in shown and '| Verification batch | …cdef |' in shown
    assert 'timed out after 1800 s: Unit …318d still installs 4.6.0' in shown and 'batch-abcdef' not in shown
    assert 'Nothing was sent to AosCloud.' in shown


# Values pasted into GitHub: a new line or a space at the end (walkthrough 06.11: sed: unterminated 's' command)
PASTED_CA = '\n  -----BEGIN CERTIFICATE-----\r\nAAAA\r\n-----END CERTIFICATE-----  \n\n'
CLEAN_CA = '-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n'


def run_trim_step(tmp_path, workflow, url='https://api.example:10000/api/v11 ', service='service-abcdef\n', pem='KEY', ca=PASTED_CA,
                  version='4.6.0', decision='auto'):
    genv, temp = tmp_path / 'github_env', tmp_path / 'temp'
    genv.write_text(''), temp.mkdir(exist_ok=True)
    done = subprocess.run(['bash', '-e', '-c', step('Check and trim the secrets', workflow)], capture_output=True, text=True,
                          env={'PATH': os.environ['PATH'], 'GITHUB_ENV': str(genv), 'RUNNER_TEMP': str(temp), 'PASTED_URL': url,
                               'PASTED_SERVICE_ID': service, 'USER_PEM': pem, 'ROOT_CA': ca, 'VERSION': version,
                               'AOS_DECISION': decision}, check=False)
    return done, genv.read_text()


@pytest.mark.parametrize('version, decision, error', [
    ('', 'auto', 'The input version is empty'), ('null', 'auto', 'The input version is empty'),
    ('4.6.0', 'Auto', "AOS_DECISION must be auto, ask or report-only, not 'Auto'")])
def test_an_empty_version_or_a_mistyped_decision_mode_stops_before_any_call(tmp_path, version, decision, error):
    done, _ = run_trim_step(tmp_path, WORKFLOW, version=version, decision=decision)
    assert done.returncode == 1 and error in done.stdout


def test_the_job_queue_holds_for_a_called_run_too():
    text = WORKFLOW.read_text(encoding='utf-8')
    job = text[text.index('\n  verify:\n'):text.index('    steps:')]
    assert "group: verify-batch-job-${{ inputs.service_id || 'service' }}-${{ inputs.version }}" in job
    assert 'cancel-in-progress: false' in job


ALL_THREE = [WORKFLOW, WATCH, pytest.param(ROOT / '.github' / 'workflows' / 'demo-upload.yml', marks=needs_demo)]


@pytest.mark.parametrize('workflow', ALL_THREE, ids=['verify-batch', 'watch-batches', 'demo-upload'])
def test_a_service_id_with_a_new_line_and_a_url_with_a_space_work(tmp_path, workflow):
    done, genv = run_trim_step(tmp_path, workflow)
    assert done.returncode == 0, done.stdout + done.stderr
    ca = tmp_path / 'temp' / 'aos' / 'aos-root-ca.crt'
    assert genv.splitlines() == ['AOS_API_URL=https://api.example:10000/api/v11', 'SERVICE_ID=service-abcdef', f'AOS_CA={ca}']
    assert ca.read_text() == CLEAN_CA and '::add-mask::service-abcdef\n' in done.stdout and 'KEY' not in genv
    text = workflow.read_text(encoding='utf-8')
    assert text.index('Check and trim the secrets') < text.index('Install requests')
    # every later step takes the trimmed values: no step reads these two secrets again
    assert text.count('secrets.AOS_SERVICE_ID }}') == 1 and text.count('secrets.AOS_ROOT_CA_PEM }}') == 1


@pytest.mark.parametrize('workflow', ALL_THREE, ids=['verify-batch', 'watch-batches', 'demo-upload'])
def test_an_empty_or_blank_secret_stops_the_job_and_names_it(tmp_path, workflow):
    done, genv = run_trim_step(tmp_path, workflow, service=' \n', pem='')
    assert done.returncode == 1 and genv == ''
    assert 'AOS_USER_PEM AOS_SERVICE_ID.' in done.stdout and '::error::' in done.stdout


@pytest.mark.parametrize('workflow', ALL_THREE, ids=['verify-batch', 'watch-batches', 'demo-upload'])
def test_the_key_is_trimmed_wherever_it_is_written(workflow):
    text = workflow.read_text(encoding='utf-8')
    writes = re.findall(r'.*printf .%s\\n. "\$USER_PEM".*', text)
    assert writes and all("| sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e '/^$/d' >" in w for w in writes), writes


@needs_bash
def test_the_wait_step_writes_the_key_without_the_pasted_ends(tmp_path):
    run, _, _ = run_wait_step(tmp_path, 0, BATCH, user_pem=PASTED_CA)
    assert run.returncode == 0 and (tmp_path / 'seen_pem').read_text() == CLEAN_CA


GNU_SED = subprocess.run(['sed', '--version'], capture_output=True, check=False).returncode == 0


@needs_demo
@pytest.mark.skipif(not GNU_SED, reason='the build step uses GNU sed -i, as on the GitHub runner')
def test_the_demo_build_takes_the_trimmed_service_id(tmp_path):
    demo = ROOT / '.github' / 'workflows' / 'demo-upload.yml'
    _, genv = run_trim_step(tmp_path, demo)
    service = dict(line.split('=', 1) for line in genv.splitlines())['SERVICE_ID']
    bin_dir, temp = tmp_path / 'bin', tmp_path / 'temp'
    bin_dir.mkdir()
    for tool in ('aos-signer', 'openssl'):
        (bin_dir / tool).write_text('#!/bin/bash\nexit 0\n'), (bin_dir / tool).chmod(0o755)
    env = {'PATH': f"{bin_dir}:{os.environ['PATH']}", 'RUNNER_TEMP': str(temp), 'USER_PEM': 'pem', 'VERSION': '4.11.2'}
    done = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step('b. Build', demo)], capture_output=True, text=True,
                          cwd=ROOT, env={**env, 'SERVICE_ID': service}, check=False)
    assert done.returncode == 0, done.stderr
    assert 'id: service-abcdef\n' in (temp / 'aos' / 'bundle' / 'config.yaml').read_text()
    # what happened on 06.11, before the trim
    shutil.rmtree(temp / 'aos' / 'bundle')
    done = subprocess.run(['bash', '-e', '-c', step('b. Build', demo)], capture_output=True, text=True, cwd=ROOT,
                          env={**env, 'SERVICE_ID': 'service-abcdef\n'}, check=False)
    assert done.returncode != 0 and 'unterminated' in done.stderr


def test_the_watch_says_why_in_its_summary(tmp_path):
    assert plain(summary_of(WATCH, 'Say why it asks on a schedule', tmp_path)[0]) == f'Why every 5 minutes? {WHY}'
    text = WATCH.read_text(encoding='utf-8')
    # after the checkout: the root copy of this file (demo-services) runs in a folder that exists only then
    assert text.index('actions/checkout') < text.index('Say why it asks on a schedule')


def test_the_report_header_says_the_decision_mode_and_keeps_it_after_the_decision(tmp_path):
    sys.path.insert(0, str(ROOT / 'tests'))
    import report  # noqa: E402  pylint: disable=import-outside-toplevel
    args = report_args(tmp_path, '--results', str(tmp_path / 'results.tsv'), '--service-id', 'service-abcdef',
                       '--version', NEW, '--decision-mode', 'auto')
    assert report.main(args) == 0
    fact = '<div><span>Decision mode</span>auto</div>'
    assert fact in (tmp_path / 'r/report.html').read_text(encoding='utf-8')
    assert report.main(report_args(tmp_path, '--decision', 'approved', '--state', 'Valid')) == 0
    page = (tmp_path / 'r/report.html').read_text(encoding='utf-8')
    assert fact in page and 'Decision: approved' in page
    assert page.index('<span>Service version</span>') < page.index(fact) < page.index('<span>Verification Set Units</span>')


def test_a_report_without_a_mode_does_not_invent_one(tmp_path):
    write_report(tmp_path)
    assert 'Decision mode' not in (tmp_path / 'r/report.html').read_text(encoding='utf-8')


AMBER = '<p class="todo"><b>No partner tests yet: add tests/your_tests.sh</b>'


@pytest.mark.parametrize('line, amber', [
    ('yours\t\tpartner tests\tSKIP\tno tests/your_tests.sh (start from tests/your_tests.sh.example)\n', True),
    ('yours\t\tpartner tests\tSKIP\tnot run: the Service version already failed on a Verification Set Unit\n', False),
    ('yours\t\thealth\tPASS\tok\n', False),
    ('yours\t\thealth\tFAIL\tbroken\n', False)])
def test_your_tests_section_is_amber_only_when_you_have_none(tmp_path, line, amber):
    (tmp_path / 'results.tsv').write_text('builtin\tA1\tthe batch\tPASS\tok\n' + line)
    write_report(tmp_path)
    page = (tmp_path / 'r/report.html').read_text(encoding='utf-8')
    section = page[page.index('<h2>Partner tests</h2>'):page.index('<h2>Scenarios covered</h2>')]
    assert (AMBER in section) is amber
    assert ('they run after the built-in checks. If any check or test fails, the verdict is reject; in auto mode the job '
            'sends it, in report-only a person decides.' in section) is amber
    assert 'after ours' not in section
    assert ('<table>' in section) is not amber


def test_the_old_workflow_names_are_gone():
    old = re.compile('verify[-_]' + 'release|watch-' + 'verification|demo-' + 'release')
    files = [p for p in ROOT.rglob('*') if p.is_file() and '.git' not in p.parts and p.suffix not in ('.png', '.jpg', '.mp4')
             and '__pycache__' not in p.parts and '.pytest_cache' not in p.parts and '.venv' not in p.parts and 'report' not in p.parts]
    assert [str(p) for p in files if old.search(p.name) or old.search(p.read_text(encoding='utf-8', errors='ignore'))] == []


# Terms: a reader never has to guess which version or which batch.
def report_page(tmp_path, *more, results='builtin\tA1\ta\tPASS\tok\nbuiltin\tB1\tb\tPASS\tok\nbuiltin\tC2\tc\tPASS\tok\n'
                                         'builtin\tC3/C4\td\tSKIP\toff\nyours\t\tpartner tests\tSKIP\tnone\n'):
    sys.path.insert(0, str(ROOT / 'tests'))
    import report  # noqa: E402  pylint: disable=import-outside-toplevel
    (tmp_path / 'results.tsv').write_text(results)
    assert report.main(report_args(tmp_path, '--results', str(tmp_path / 'results.tsv'), '--service-id', 'service-abcdef',
                                   '--version', NEW, '--units', 'unit-abcdef', '--observe', '60', *more)) == 0
    return (tmp_path / 'r/report.html').read_text(encoding='utf-8')


def test_the_report_header_labels(tmp_path):
    page = report_page(tmp_path, '--service-name', 'verification-demo', '--decision-mode', 'auto')
    labels = re.findall(r'<div><span>([^<]+)</span>', page)
    assert labels == ['Service', 'Service version', 'Verification batch', 'Decision mode', 'Verification Set Units', 'Report time']
    assert '<div><span>Service</span>verification-demo (…cdef)</div>' in page


def test_the_report_without_a_service_name_shows_the_short_id(tmp_path):
    assert '<div><span>Service</span>…cdef</div>' in report_page(tmp_path)


def test_the_report_has_no_bare_version_or_batch(tmp_path):
    page = report_page(tmp_path, '--service-name', 'verification-demo', '--decision-mode', 'auto')
    labels = re.findall(r'<(?:span|th|h1|h2|b)[^>]*>([^<]+)<', page)
    assert not [text for text in labels if re.fullmatch(r'\s*(version|batch)s?\s*', text, re.I)]
    words = html.unescape(re.sub(r'<[^>]+>', ' ', page[page.index('<body>'):]))
    assert not re.findall(r'.{0,30}(?<!Service )\b[Vv]ersions?\b.{0,10}', words)
    assert not re.findall(r'.{0,30}(?<!Verification )\b[Bb]atch(?:es)?\b.{0,10}', words)
    assert 'your tests' not in words.lower() and 'test Unit' not in words


def test_the_junit_names_have_no_bare_version(tmp_path):
    report_page(tmp_path)
    junit = (tmp_path / 'r/junit.xml').read_text(encoding='utf-8')
    assert f'name="verification of Service version {NEW}"' in junit and 'classname="your tests"' not in junit


def test_the_summary_counts_what_was_skipped_and_failed(tmp_path):
    page = report_page(tmp_path)
    assert '3 passed, 1 skipped (C3/C4), 0 failed; partner tests: none added (tests/your_tests.sh)' in page
    page = report_page(tmp_path, results='builtin\tA1\ta\tPASS\tok\nbuiltin\tB1\tb\tFAIL\tno\nyours\t\th\tPASS\tok\n')
    assert '1 passed, 0 skipped, 1 failed; partner tests: 1 of 1 passed' in page


def test_a1_and_b1_are_named_as_agreed(cloud, capsys):
    verify_batch.main(['status', '--service-id', SERVICE, '--version', NEW, '--batch-id', 'batch-1', '--unit', UNIT])
    out = capsys.readouterr().out
    assert 'A1\tthe Verification batch is for this Service and this Service version\t' in out
    assert f'B1\tService version {NEW} is installed and running on every Verification Set Unit\t' in out


def test_service_name_reads_the_codename(monkeypatch, capsys):
    monkeypatch.setattr(verify_batch, 'pages', lambda path: [{'id': 'other', 'codename': 'no'}, {'id': SERVICE, 'codename': 'demo'}])
    assert main('service-name', '--service-id', SERVICE) == 0 and capsys.readouterr().out == 'demo\n'


@needs_bash
def test_by_parts_the_service_name_reaches_the_step_without_the_key(tmp_path):
    run_tests_sh(tmp_path, part='builtin', extra={'SERVICE_NAME': 'demo-x'})
    run, _, report = run_tests_sh(tmp_path, part='yours')
    assert run.returncode == 0 and '<div><span>Service</span>demo-x (…3456)</div>' in report


def test_aos_verify_recognises_the_message_of_verify_batch():
    phrase = 'no Verification Set Unit'
    assert phrase in (ROOT / 'verify_batch.py').read_text(encoding='utf-8')
    assert f"*'{phrase}'*)" in (ROOT / 'aos-verify').read_text(encoding='utf-8')


# demo-upload.yml and the pause switch of the watch
DEMO = ROOT / '.github' / 'workflows' / 'demo-upload.yml'
SERVICE_ID_FOR_NEXT = 'service-next'


def batch_of(version, service=SERVICE_ID_FOR_NEXT):
    return {'id': f'batch-{version}', 'state': 'Valid', 'update_items': [{'identity_id': service, 'version': version}]}


@pytest.mark.parametrize('versions, expected', [
    (['4.9.0', '4.11.0', '4.10.0'], '4.11.1'),
    (['4.11.1', '4.11.0'], '4.11.2'),
    (['5.0.0-rc1', '4.11.0'], '5.0.1'),
    (['not-semver', '1.2.3'], '1.2.4'),
    ([], '1.0.0'),
])
def test_next_version_is_the_highest_plus_one_patch(versions, expected, monkeypatch, capsys):
    batches = [batch_of(v) for v in versions] + [batch_of('99.0.0', 'another-service')]
    monkeypatch.setattr(verify_batch, 'pages', lambda path, **params: iter(batches))
    monkeypatch.setattr(verify_batch, 'get', lambda *a, **k: pytest.fail('next-version must only list'))
    assert verify_batch.main(['next-version', '--service-id', SERVICE_ID_FOR_NEXT]) == 0
    assert capsys.readouterr().out.strip() == expected


def test_ids_and_versions_with_a_new_line_or_a_space_are_trimmed(monkeypatch, capsys):
    monkeypatch.setattr(verify_batch, 'pages', lambda path, **params: iter([batch_of('4.11.1')]))
    assert verify_batch.main(['next-version', '--service-id', f' {SERVICE_ID_FOR_NEXT}\n']) == 0
    assert capsys.readouterr().out.strip() == '4.11.2'
    monkeypatch.setenv('SERVICE_ID', f'{SERVICE_ID_FOR_NEXT}\n')
    seen = {}
    monkeypatch.setattr(verify_batch, 'discover', lambda args: seen.update(vars(args)) or 0)
    assert verify_batch.main(['discover', '--unit', 'unit-1 ']) == 0
    assert seen['service_id'] == SERVICE_ID_FOR_NEXT and seen['unit'] == ['unit-1']


def test_next_version_stops_on_an_error_with_code_2(monkeypatch, capsys):
    def refused(path, **params):
        raise verify_batch.Stop('HTTP 403')
    monkeypatch.setattr(verify_batch, 'pages', refused)
    assert verify_batch.main(['next-version', '--service-id', SERVICE_ID_FOR_NEXT]) == 2
    captured = capsys.readouterr()
    assert captured.out == '' and 'nothing was changed' in captured.err


def job_of(text: str, name: str) -> str:
    return re.search(rf'^  {name}:\n(.*?)(?=^  \w+:\n|\Z)', text, re.M | re.S)[1]


@pytest.mark.parametrize('value, event, fork, asked, says', [
    ('off', 'schedule', 'false', None, 'the variable AOS_WATCH is off'),
    ('on', 'schedule', 'false', None, None),
    ('', 'schedule', 'false', None, None),
    ('off', 'workflow_dispatch', 'false', None, None),
    ('', 'schedule', 'true', None, 'scheduled watch is off in forks'),
    ('', 'workflow_dispatch', 'true', None, None),
    ('', 'schedule', '', 'true', 'scheduled watch is off in forks'),
    ('', 'schedule', '', 'false', None),
    ('', 'schedule', '', 'fails', 'scheduled watch is off in forks'),
])
def test_the_switch_stops_a_scheduled_watch_when_paused_or_in_a_fork(value, event, fork, asked, says, tmp_path):
    output, summary, fake = tmp_path / 'output', tmp_path / 'summary', tmp_path / 'bin'
    output.touch(), summary.touch(), fake.mkdir()
    (fake / 'gh').write_text('#!/bin/bash\necho "$*" >> "$CALLS"\n[ "$ASKED" = fails ] && exit 1\necho "$ASKED"\n')
    (fake / 'gh').chmod(0o755)
    done = subprocess.run(['bash', '-e', '-c', step('Is the watch paused', WATCH)], capture_output=True, text=True, check=True,
                          env={'PATH': f"{fake}:{os.environ['PATH']}", 'AOS_WATCH': value, 'EVENT': event, 'FORK': fork,
                               'ASKED': str(asked), 'CALLS': str(tmp_path / 'calls'), 'GITHUB_REPOSITORY': 'someone/demo',
                               'GITHUB_OUTPUT': str(output), 'GITHUB_STEP_SUMMARY': str(summary)})
    paused = says is not None
    assert ('paused=yes' in output.read_text()) == paused and ('watch paused' in done.stdout) == paused
    if says:
        assert f'watch paused: {says}' in done.stdout and says in summary.read_text()
    asked_github = (tmp_path / 'calls').exists()
    assert asked_github == (event == 'schedule' and fork == '')
    if asked_github:
        assert (tmp_path / 'calls').read_text().strip() == 'api repos/someone/demo --jq .fork'


def test_the_paused_watch_has_no_secret_and_skips_the_job_that_has():
    text = WATCH.read_text(encoding='utf-8')
    assert 'secrets.' not in job_of(text, 'switch') and 'environment:' not in job_of(text, 'switch')
    assert "needs: switch\n    if: needs.switch.outputs.paused != 'yes'" in text


@needs_demo
def test_demo_upload_is_manual_and_says_demo_only():
    text = DEMO.read_text(encoding='utf-8')
    assert re.search(r'^on:\n  workflow_dispatch:\n\n', text, re.M) and 'schedule' not in text and 'pull_request' not in text
    assert 'name: demo-upload (demo only)' in text and text.startswith('#') and 'DEMO ONLY' in text.splitlines()[0]
    assert 'aos-signer==2.0.2' in text and 'gh workflow run' not in text and 'actions: write' not in text
    assert 'batch_id' not in workflow_step('d. Say', DEMO) and 'secrets.' not in workflow_step('d. Say', DEMO)
    assert 'actions/runs' not in workflow_step('d. Say', DEMO)
    assert 'verify-batch runs next in this run (Start A); its report follows below.' in workflow_step('d. Say', DEMO)
    assert ' approve' not in text.lower() and '--reject' not in text


@needs_demo
def test_demo_upload_starts_verify_batch_exactly_as_readme_step_3():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    snippet = readme[readme.index('```yaml\n', readme.index('3. **Start it after your upload')) + 8:]
    snippet = textwrap.dedent(snippet[:snippet.index('```')])
    text = DEMO.read_text(encoding='utf-8')
    job = textwrap.dedent(text[text.index('\n  verify:\n') + 1:])
    # only a name added, so that the graph reads with the diagram numbers
    assert job.replace('  name: "4 Start A: verify-batch"\n', '') == snippet.lstrip()
    assert 'outputs:\n      version: ${{ steps.next.outputs.version }}' in text


def test_the_job_names_read_with_the_diagram_numbers():
    names = re.findall(r'^    name: (.+)$', WORKFLOW.read_text(encoding='utf-8'), re.M)
    assert names == ['5–7 Wait, test, decide']
    if DEMO.exists():
        assert re.findall(r'^    name: (.+)$', DEMO.read_text(encoding='utf-8'), re.M) == [
            '1–3 Upload a new demo Service version', '"4 Start A: verify-batch"']


def run_signer_step(name, signer_output, signer_code, tmp_path):
    """A signer step of demo-upload.yml, with stand-ins for aos-signer and openssl."""
    bin_dir, temp = tmp_path / 'bin', tmp_path / 'temp'
    bin_dir.mkdir(), (temp / 'aos').mkdir(parents=True)
    for tool, body in (('aos-signer', f'cat <<\'OUT\'\n{signer_output}\nOUT\nexit {signer_code}\n'), ('openssl', 'exit 0\n')):
        (bin_dir / tool).write_text('#!/bin/bash\n' + body), (bin_dir / tool).chmod(0o755)
    (temp / 'aos' / 'bundle').mkdir()
    return subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', step(name, DEMO)], capture_output=True, text=True, cwd=ROOT,
                          env={'PATH': f"{bin_dir}:{os.environ['PATH']}", 'RUNNER_TEMP': str(temp), 'USER_PEM': 'pem',
                               'VERSION': '4.11.1', 'SERVICE_ID': 'a-service'})


SIGNER_OUT = ('\x1b[36mUpload info: user=someone+demo@example.com, domain=aoscloud.io\x1b[0m\n'
              'Uploading...   DONE\nowner someone@example.com of 11111111-2222-3333-4444-555555555555')


@needs_demo
def test_the_upload_step_hides_the_user_and_the_ids_and_deletes_the_key(tmp_path):
    done = run_signer_step('c. Upload', SIGNER_OUT, 0, tmp_path)
    assert done.returncode == 0, done.stderr
    assert 'example.com' not in done.stdout + done.stderr and '11111111-2222' not in done.stdout + done.stderr
    assert 'domain=aoscloud.io' in done.stdout and 'uploaded Service version 4.11.1' in done.stdout
    assert not list((tmp_path / 'temp' / 'aos').glob('user.*'))


@needs_demo
def test_a_refused_upload_stops_the_job_and_says_why(tmp_path):
    done = run_signer_step('c. Upload', 'User does not have "deployment_bundles_create" permission.', 1, tmp_path)
    assert done.returncode == 1 and '::error::' in done.stdout and 'deployment_bundles_create' in done.stdout
    assert 'uploaded Service version' not in done.stdout


@needs_demo
def test_the_bundle_template_holds_no_secret_and_the_build_fills_every_token():
    config = (ROOT / 'demo-service' / 'config.yaml').read_text(encoding='utf-8')
    assert verify_batch.AN_UUID.search(config) is None and 'tlsKey' not in config and '@' not in config
    assert 'id: __AOS_SERVICE_ID__' in config and 'version: __DEMO_VERSION__' in config
    build = workflow_step('b. Build', DEMO)
    app = (ROOT / 'demo-service' / 'verification-demo' / 'src_any' / 'app.py').read_text(encoding='utf-8')
    for token in re.findall(r"^BAKED_\w+ = '(__\w+__)'", app, re.M):
        assert token in build
    assert '__AOS_SERVICE_ID__' in build


@needs_demo
def test_try_it_has_the_five_steps_and_the_readme_links_it():
    page = (ROOT / 'TRY_IT.md').read_text(encoding='utf-8')
    for name in ('AOS_USER_PEM', 'AOS_ROOT_CA_PEM', 'AOS_SERVICE_ID', 'AOS_API_URL', 'I understand my workflows', 'demo-upload',
                 'verify-batch', 'If something goes wrong'):
        assert name in page
    assert verify_batch.AN_UUID.search(page) is None and '@' not in page
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert readme.index('## Add it to your repository') < readme.index('## Try it in your browser') < readme.index('TRY_IT.md')
    assert readme.index('TRY_IT.md') < readme.index('## Read more')
    assert readme.index('![Automatic verification') < readme.index('## How the job knows')
    assert '**Two pipelines on one Service: pause one.**' in (ROOT / 'docs' / 'faq.md').read_text(encoding='utf-8')


@needs_bash
@pytest.mark.parametrize('says, result, code', [
    ('stopped, decide nothing: GET units/…: HTTP 403: this certificate may not read it', 'ERROR C3/C4', 3),
    ('stopped, decide nothing: TLS error: check AOS_CERT', 'ERROR C3/C4', 3),
    ('batch …7890 is no longer Waiting_validation: nothing to decide', 'ERROR C3/C4', 3),
    ('timed out after 60 s: decide nothing', 'FAIL C3/C4', 1),
])
def test_a_4xx_or_tls_error_in_the_observation_is_no_fail_and_no_reject(says, result, code, tmp_path):
    run, _, report = run_tests_sh(tmp_path, observe='60', wait_code=2, extra={'FAKE_WAIT_SAYS': says})
    assert run.returncode == code and result in run.stdout, run.stdout
    if code == 3:
        assert 'reject' not in run.stdout.lower().split('verdict')[-1] and 'FAIL → reject' not in report
        assert 'FAIL C3/C4' not in run.stdout


# The README is one screen in: title, intro, diagram; then the 3 steps and links. Everything else is in docs/.
README_ORDER = ['# Automatic verification of a new Service version', 'Nothing reaches the fleet before the decision.',
                'runs in any CI with Python 3.10 that reaches AosCloud', '![Automatic verification', '](docs/img/flow.svg)',
                '[Open the diagram at full size](https://raw.githubusercontent.com/aosedge/demo-services/main/'
                'verification-example/docs/img/flow.svg)',
                'Tested on AosCloud 6.1.97, 05–06.11.2026: a Service version uploaded from GitHub was Verified about '
                '1.5 min later, with no step in between.',
                '## How the job knows it is time', 'The calls: six GETs and one PATCH', '## Add it to your repository',
                '## Try it in your browser', '## Read more', 'Maintained by the AosEdge team']


def test_the_readme_is_one_screen_in_and_short():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert len(readme.splitlines()) < 80
    assert [readme.index(part) for part in README_ORDER] == sorted(readme.index(part) for part in README_ORDER)
    assert [line for line in readme.splitlines() if line.startswith('## ')] == [
        '## How the job knows it is time', '## Add it to your repository', '## Try it in your browser', '## Read more']
    assert readme.splitlines()[-1].startswith('Maintained by the AosEdge team')
    for gone in ('Defaults', '## Limits', 'It ran on production', '## Step by step', '## Prerequisites', '## Parameters',
                 '## Security', 'sequence.svg', 'Not yet run on GitHub'):
        assert gone not in readme, gone
    for target in ('docs/setup.md', 'docs/how-it-works.md', 'docs/tests.md', 'docs/checks.md', 'docs/api.md', 'docs/faq.md',
                   'docs/run-locally.md'):
        assert f']({target})' in readme.split('## Read more')[1], target


def test_the_readme_snippet_is_the_verify_job_of_the_example():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    snippet = readme.split('```yaml\n')[1].split('```')[0]
    snippet = textwrap.dedent('\n'.join(line[3:] for line in snippet.splitlines()))
    example = (ROOT / 'examples' / 'release-caller.yml').read_text(encoding='utf-8')
    assert snippet.strip() and textwrap.indent(snippet.strip(), '  ') in example


def test_the_proof_and_the_limits_moved_to_one_place_each():
    page = (ROOT / 'docs' / 'how-it-works.md').read_text(encoding='utf-8')
    proof = page[page.index('## Tested on AosCloud'):]
    assert proof.count('| 05.11.2026 | GitHub |') == 2 and '| 30.10.2026 | Terminal |' in proof
    assert '| 06.11.2026 | GitHub | A fresh fork' in proof and 'Production AosCloud 6.1.97.' in proof
    faq = (ROOT / 'docs' / 'faq.md').read_text(encoding='utf-8')
    assert faq.index('## Limits') < faq.index('## Before you start')
    limits = faq[faq.index('## Limits'):faq.index('## Before you start')]
    for part in ('no webhook', 'best-effort', 'second decision', 'offline means no decision', 'Not yet run on GitHub', '6.2.31',
                 'one Service per repository'):
        assert part in limits, part
    for name in ('README.md', 'docs/setup.md', 'docs/how-it-works.md'):
        rest = (ROOT / name).read_text(encoding='utf-8')
        for caveat in ('Scheduled starts are being confirmed', 'Not yet run on GitHub', '0 scheduled runs', 'Status 05.11.2026'):
            assert caveat not in rest, (name, caveat)


def test_setup_and_how_it_works_hold_what_left_the_readme():
    setup = (ROOT / 'docs' / 'setup.md').read_text(encoding='utf-8')
    for letter in 'ABC':
        assert f'\n### {letter}. ' in setup
    for heading in ('## Before you start', '## Who has to act', '## Secrets', '## Start A: after your upload',
                    '## Start B: the watch', '## Parameters', '## Security', '## Who maintains this'):
        assert f'\n{heading}\n' in setup, heading
    assert '| `AOS_WATCH` |' in setup and '| `AOS_DECISION` |' in setup and 'SP, once (a user who may create users)' in setup
    page = (ROOT / 'docs' / 'how-it-works.md').read_text(encoding='utf-8')
    for heading in ('## Step by step', '### 5. Wait', '### 6. Test', '### 7. Decide', '## Decision modes',
                    '## The watch (Start B)', '## Who tells whom', '## Tested on AosCloud'):
        assert f'\n{heading}' in page, heading
    assert '](img/sequence.svg)' in page and '| Who | Tells whom |' in page
    assert not (ROOT / 'docs' / 'who-tells-whom.md').exists()


def test_one_numbering_1_to_7_everywhere():
    for name in ('README.md', 'TRY_IT.md', 'docs/setup.md', 'docs/how-it-works.md', 'docs/checks.md', 'docs/faq.md',
                 'docs/img/flow.svg', 'docs/img/sequence.svg', 'tests/report.py', WATCH.relative_to(ROOT).as_posix()):
        if not (ROOT / name).exists():  # TRY_IT.md is not in the partner zip
            continue
        text = (ROOT / name).read_text(encoding='utf-8')
        assert not re.search('[⓪③④]|step 0|0 is the', text), name
    page = (ROOT / 'docs' / 'how-it-works.md').read_text(encoding='utf-8')
    steps = re.findall(r'^\| (\d) \| ', page[page.index('## Step by step'):page.index('## The verification job')], re.M)
    assert steps == list('1234567')
    assert '- name: 4. Ask AosCloud' in WATCH.read_text(encoding='utf-8')
    for name in ('flow.svg', 'sequence.svg'):
        svg = (ROOT / 'docs' / 'img' / name).read_text(encoding='utf-8')
        digits = re.findall(r'<circle cx="[\d.]+" cy="[\d.]+" r="(\d+)"[^>]*/>\n<text [^>]*>(\d)</text>', svg)
        assert sorted({d for _, d in digits}) == list('1234567'), name
        assert {r for r, _ in digits} == {'14'}, name
        alt = re.search(r'aria-label="([^"]*)"', svg)[1]
        assert alt == re.search(r'<title>([^<]*)</title>', svg)[1] and ' 0,' not in alt and 'Step 0' not in alt


def github_anchor(heading: str) -> str:
    heading = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', heading).replace('`', '')
    return re.sub(r'[^\w\- ]', '', heading.strip().lower()).replace(' ', '-')


def test_every_link_between_the_pages_resolves():
    pages = [ROOT / 'README.md', ROOT / 'TRY_IT.md', *sorted((ROOT / 'docs').glob('*.md'))]
    for page in (p for p in pages if p.exists()):
        text = re.sub(r'```.*?```', '', page.read_text(encoding='utf-8'), flags=re.S)
        for target in re.findall(r'\]\(([^)\s]+)\)', text):
            if target.startswith(('http://', 'https://')):
                continue
            path, _, anchor = target.partition('#')
            file = (page.parent / path).resolve() if path else page
            assert file.exists(), (page.name, target)
            if anchor and file.suffix == '.md':
                headings = re.findall(r'^#{1,6} (.*)$', file.read_text(encoding='utf-8'), re.M)
                assert anchor in {github_anchor(h) for h in headings}, (page.name, target)


def test_start_a_is_the_main_start_and_the_watch_the_fallback():
    flow = (ROOT / 'docs/img/flow.svg').read_text(encoding='utf-8')
    sequence = (ROOT / 'docs/img/sequence.svg').read_text(encoding='utf-8')
    assert '>Start A: chain (main)<' in flow and '>Start B: watch (fallback)<' in flow
    assert 'Start A (main)' in sequence and '>Start B: watch (fallback)<' in sequence
    for picture in (flow, sequence):
        assert 'Why polling' not in picture and "5 min is GitHub's minimum, best effort" in picture
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert 'Start it after your upload (Start A)' in readme and 'The watch finds each new Service version by itself' not in readme


def test_no_internal_words_reach_a_partner():
    for name in ('README.md', 'TRY_IT.md', 'docs/faq.md', 'docs/api.md', 'docs/setup.md', 'docs/how-it-works.md'):
        if not (ROOT / name).exists():  # TRY_IT.md is not in the partner zip
            continue
        text = (ROOT / name).read_text(encoding='utf-8')
        assert not re.search(r'[Tt]ell us|rehearsal|showcase', text), name
