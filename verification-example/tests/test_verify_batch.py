# Copyright (c) 2018-2026 EPAM Systems Inc.
# SPDX-License-Identifier: Apache-2.0
"""Tests of verify_batch.py against a tiny in-memory AosCloud. No network: requests.Session is replaced.

    pip install pytest requests && python -m pytest -q
"""
import json
import os
import re
import shutil
import subprocess
import sys
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
    assert len([line for line in capsys.readouterr().err.splitlines() if line.startswith('batch ')]) == 1


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
    assert out.out == '' and 'no test Unit for arm64' in out.err


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
    assert out.err.index(f'::add-mask::{BATCH}') < out.err.index(f'batch {BATCH}')


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
    assert out.out == '' and cloud.patches == [] and 'no test Unit for amd64' in out.err


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
    assert out.out == '' and 'not in batch-9' in out.err


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
    assert 'approve amd64, arm64' in capsys.readouterr().err


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
    assert decide() == 0 and capsys.readouterr().out.strip() == f'batch {BATCH}: approved, sent'


# discover: step 0, reads only

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
    assert out.out == '' and 'no waiting batch' in out.err and 'stale' in out.err and github_output.read_text() == ''
    assert not [path for path, _ in cloud.gets if 'statistics' in path]


def test_discover_with_nothing_waiting_prints_nothing_and_exits_0(cloud, capsys):
    cloud.batch['state'] = 'Valid'
    assert discover() == 0
    out = capsys.readouterr()
    assert out.out == '' and 'no waiting batch' in out.err


def test_discover_ignores_batches_of_other_services(cloud, capsys):
    cloud.batch = None
    cloud.others = [waiting('9.0.0', service='another-service')]
    assert discover() == 0
    out = capsys.readouterr()
    assert out.out == '' and 'no waiting batch' in out.err


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
    assert out.out == '' and 'no test Unit' in out.err and github_output.read_text() == ''


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
    assert out.out == '' and 'no test Unit for arm64' in out.err


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
    assert found['units'] == [] and 'no test Unit' in err and github_output.read_text() == ''


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
    assert out.out == '' and 'no test Unit for arm64' in out.err


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

WORKFLOW = ROOT / '.github' / 'workflows' / 'verify-release.yml'
WATCH = ROOT / '.github' / 'workflows' / 'watch-verification.yml'


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
    run = subprocess.run(['bash', '-e', '-c', step('1. Wait')], env=env, capture_output=True, text=True, timeout=60,
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
def test_verify_release_with_units_passes_them_on(tmp_path):
    run, output, _ = run_wait_step(tmp_path, 0, BATCH, units='unit-1 unit-2', batch_input='batch-1')
    args = (tmp_path / 'args').read_text().splitlines()
    assert run.returncode == 0 and args.count('--unit') == 2 and 'unit-2' in args
    assert args[args.index('--batch-id') + 1] == 'batch-1' and f'batch_id={BATCH}' in output


@needs_bash
def test_verify_release_without_units_lets_wait_find_them(tmp_path):
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


@needs_bash
def test_the_override_is_used_when_the_input_is_empty(tmp_path):
    run_wait_step(tmp_path, 0, BATCH, units='', override='unit-chosen')
    assert 'unit-chosen' in (tmp_path / 'args').read_text().splitlines()


@needs_bash
def test_a_star_in_the_units_is_not_expanded(tmp_path):
    (tmp_path / 'somefile').write_text('')
    run_wait_step(tmp_path, 0, BATCH, units='*')
    assert '*' in (tmp_path / 'args').read_text().splitlines()


@needs_bash
def test_verify_release_stops_on_an_empty_service_id(tmp_path):
    run, output, _ = run_wait_step(tmp_path, 0, BATCH, service='')
    assert run.returncode != 0 and 'AOS_SERVICE_ID is empty' in run.stdout and output == ''


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
  jq --arg t "verify ${batch:-manual} $version" '. + [{displayTitle: $t, status: "queued"}]' "$GH_RUNS" > "$GH_RUNS.new"
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
    assert dispatches(calls) == [f'gh workflow run verify-release.yml --ref the-branch -f version={NEW} -f batch_id=batch-aaaa '
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
    run, calls = run_start_step(tmp_path, [{'displayTitle': f'verify batch-aaaa {NEW}', 'status': status}])
    assert run.returncode == 0 and status in run.stdout and dispatches(calls) == []


@needs_jq
def test_the_watch_skips_a_run_started_by_hand_for_the_version(tmp_path):
    _, calls = run_start_step(tmp_path, [{'displayTitle': f'verify manual {NEW}', 'status': 'completed'}])
    assert dispatches(calls) == []


@needs_jq
@pytest.mark.parametrize('title', ['verify batch-bbbb 4.5.0', 'verify batch-aaaa-2 4.5.0', 'verify manual 4.5.0',
                                   f'verify batch-bbbb {NEW}', 'some other title'])
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
    yours = workflow_step('2b.', WORKFLOW)
    assert 'secrets.' not in yours and 'USER_PEM' not in yours and 'RUN_PART: yours' in yours
    assert 'secrets.AOS_USER_PEM' in workflow_step('2a.', WORKFLOW)
    text = WORKFLOW.read_text(encoding='utf-8')
    assert "steps.tests.outputs.code == '0'" in workflow_step('3a.', WORKFLOW)
    assert "steps.tests.outputs.code == '1'" in text and 'persist-credentials: false' in text


def test_only_the_discover_step_of_the_watch_has_the_key():
    text = WATCH.read_text(encoding='utf-8')
    assert text.count('secrets.AOS_USER_PEM') == 1 and 'secrets.AOS_USER_PEM' in workflow_step('0. Ask', WATCH)
    assert 'GH_TOKEN' not in workflow_step('0. Ask', WATCH) and 'persist-credentials: false' in text
    assert 'secrets.' not in workflow_step('Start one', WATCH)


def test_workflow_expressions_never_go_straight_into_a_shell():
    for workflow in (WORKFLOW, WATCH):
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
    env = {'PATH': f"{fake}:{os.environ['PATH']}", 'RUNNER_TEMP': str(tmp_path), 'USER_PEM': 'PEM', 'ROOT_CA': 'CA',
           'TEST_UNITS': 'unit-chosen', 'CALLED': str(tmp_path / 'called')}
    run = subprocess.run(['bash', '-e', '-c', step('0. Ask', WATCH)], env=env, capture_output=True, text=True,
                         timeout=60, check=False)
    assert run.returncode == 0, run.stderr
    assert (tmp_path / 'called').read_text().strip() == 'verify_batch.py discover --unit unit-chosen'
    assert not (tmp_path / 'aos').exists()
    run = subprocess.run(['bash', '-e', '-c', step('0. Ask', WATCH)], env={**env, 'FAKE_CODE': '2'}, capture_output=True,
                         text=True, timeout=60, check=False)
    assert run.returncode == 2 and not (tmp_path / 'aos').exists()
    run = subprocess.run(['bash', '-e', '-c', step('0. Ask', WATCH)], env={**env, 'USER_PEM': ''}, capture_output=True,
                         text=True, timeout=60, check=False)
    assert run.returncode != 0 and 'AOS_USER_PEM' in run.stdout


def test_both_starts_end_in_one_concurrency_group():
    text = WORKFLOW.read_text(encoding='utf-8')
    assert 'group: verify-${{ inputs.service_id || vars.AOS_SERVICE_ID }}-${{ inputs.version }}' in text
    assert 'cancel-in-progress: false' in text and "run-name: verify ${{ inputs.batch_id || 'manual' }} " in text
    caller = (ROOT / 'examples' / 'release-caller.yml').read_text(encoding='utf-8')
    assert 'uses: ./.github/workflows/verify-release.yml' in caller and 'version:' in caller


def test_the_watch_asks_every_10_minutes_and_can_write_actions():
    text = WATCH.read_text(encoding='utf-8')
    assert 'cron: "*/10 * * * *"' in text and 'workflow_dispatch:' in text
    assert 'actions: write' in text and 'contents: read' in text


def test_the_readme_shows_the_watch_as_it_is():
    readme = (ROOT / 'README.md').read_text(encoding='utf-8')
    assert WATCH.read_text(encoding='utf-8').strip() in readme


@needs_bash
def test_the_wait_step_stops_on_an_empty_user_pem(tmp_path):
    run, output, pem_left = run_wait_step(tmp_path, 0, BATCH, user_pem='')
    assert run.returncode != 0 and 'AOS_USER_PEM is empty' in run.stdout and output == '' and not pem_left


@needs_bash
@pytest.mark.parametrize('root_ca, passes', [('', False), ('CA', True)])
def test_the_root_ca_step_stops_on_an_empty_secret(tmp_path, root_ca, passes):
    env = {'PATH': os.environ['PATH'], 'RUNNER_TEMP': str(tmp_path), 'GITHUB_ENV': str(tmp_path / 'env'),
           'ROOT_CA': root_ca}
    run = subprocess.run(['bash', '-e', '-c', step('Write the root CA')], env=env, capture_output=True, text=True,
                         timeout=60, check=False)
    assert (run.returncode == 0) is passes
    assert passes or 'AOS_ROOT_CA_PEM is empty' in run.stdout
    assert (tmp_path / 'aos' / 'aos-root-ca.crt').exists() is passes


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
    assert rows['A1'] == ('FAIL', 'batch …ch-1 is Valid, not Waiting_validation')


# tests/run_tests.sh: the built-in checks, the watch, your tests and the report, with a stand-in for verify_batch.py

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
    exit "$FAKE_WAIT" ;;
  *) exec "$REAL_PYTHON" "$@" ;;
esac
"""


def run_tests_sh(tmp_path, watch='60', status=0, wait_code=0, yours=None, batch='batch-1234567890', part='all',
                 extra=None):
    tests = tmp_path / 'repo' / 'tests'
    tests.mkdir(parents=True, exist_ok=True)
    for name in ('run_tests.sh', 'report.py'):
        shutil.copy(ROOT / 'tests' / name, tests / name)
    if yours is not None:
        (tests / 'your_tests.sh').write_text(yours)
    fake = tmp_path / 'python'
    fake.write_text(FAKE_PYTHON)
    fake.chmod(0o755)
    calls = tmp_path / 'calls'
    env = {'PATH': os.environ['PATH'], 'PYTHON': str(fake), 'REAL_PYTHON': sys.executable, 'CALLS': str(calls),
           'FAKE_STATUS': str(status), 'FAKE_WAIT': str(wait_code), 'WATCH_INTERVAL': '0', 'WATCH_SECONDS': watch,
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
    assert 'PASS → approve' in report and 'Scenarios covered' in report and 'needs AosCloud 6.2' in report
    assert 'service-abcdef123456' not in report and 'unit-abcdef123456' not in report and '…3456' in report
    junit = (tmp_path / 'report' / 'junit.xml').read_text()
    assert 'tests="5"' in junit and 'failures="0"' in junit and 'skipped="1"' in junit


@needs_bash
def test_the_watch_catches_a_version_that_stops_running(tmp_path):
    run, calls, report = run_tests_sh(tmp_path, watch='180', wait_code=1)
    assert run.returncode == 1 and 'FAIL C3/C4' in run.stdout and 'round 1 of 3' in run.stdout
    assert sum(' wait ' in c for c in calls) == 1 and 'FAIL → reject' in report


@needs_bash
def test_the_watch_can_be_turned_off(tmp_path):
    run, calls, _ = run_tests_sh(tmp_path, watch='0')
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
    assert run.returncode == 0 and 'PASS health: no-cert' in run.stdout and 'your tests: 2 ran' in report


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
                           watch=60, found_by='watch', units_from='the Verification Set')
    page = report.render(args, [{'part': 'builtin', 'code': 'A1', 'name': 'a', 'result': 'PASS', 'detail': ''}], 'now')
    assert '.r.pass{' in page and '\n.pass{' not in page and '<div class="verdict pass">PASS → approve</div>' in page
    assert 'service-abcdef' not in page and '…cdef' in page
    assert f'found by the watch: batch …cdef, version {NEW}, Units from the Verification Set' in page
