# Copyright (c) 2018-2026 EPAM Systems Inc.
# SPDX-License-Identifier: Apache-2.0
"""Tests of ./aos-verify in a temporary HOME, with fake certificates and a small HTTPS AosCloud on localhost.

    pip install pytest requests && python -m pytest -q tests/test_aos_verify.py
"""
import json
import os
import re
import shutil
import ssl
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVICE, OTHER, UNIT = 'aaaaaaaa-0000-4000-8000-00000000ec65', 'aaaaaaaa-0000-4000-8000-00000000beef', \
    'bbbbbbbb-0000-4000-8000-00000000318d'
OLD_BATCH, NEW_BATCH = 'cccccccc-0000-4000-8000-00000000ef77', 'dddddddd-0000-4000-8000-00000000a410'
PERSON = 'Some.Person+1@example.com'

pytestmark = pytest.mark.skipif(not all(shutil.which(tool) for tool in ('bash', 'openssl', 'jq', 'curl')),
                                reason='needs bash, openssl, jq and curl')


def run(*command, **kwargs):
    return subprocess.run([str(part) for part in command], check=True, capture_output=True, text=True, **kwargs)


class Pki:
    """A root CA, an intermediate CA, and a server certificate."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.ca, self.intermediate = folder / 'ca.crt', folder / 'intermediate.crt'
        ca_ext = folder / 'ca.ext'
        ca_ext.write_text('basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n'
                          'subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n')
        run('openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', folder / 'ca.key', '-out', self.ca,
            '-days', '3', '-subj', '/O=Test/CN=Test Root CA', '-addext', 'basicConstraints=critical,CA:TRUE',
            '-addext', 'keyUsage=critical,keyCertSign,cRLSign')
        self.sign('intermediate', '/O=Test/CN=Test Intermediate', ca_ext)
        server_ext = folder / 'server.ext'
        server_ext.write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n'
                              'extendedKeyUsage=serverAuth\nsubjectAltName=IP:127.0.0.1,DNS:localhost\n'
                              'subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid:always\n')
        self.sign('server', '/O=Test/CN=localhost', server_ext)

    def sign(self, name, subject, extensions, issuer='ca'):
        folder = self.folder
        run('openssl', 'req', '-newkey', 'rsa:2048', '-nodes', '-keyout', folder / f'{name}.key', '-out',
            folder / f'{name}.csr', '-subj', subject)
        issuer_cert = folder / ('ca.crt' if issuer == 'ca' else f'{issuer}.crt')
        run('openssl', 'x509', '-req', '-in', folder / f'{name}.csr', '-CA', issuer_cert, '-CAkey',
            folder / f'{issuer}.key', '-CAcreateserial', '-out', folder / f'{name}.crt', '-days', '3', '-extfile', extensions)

    def p12(self, out: Path, role='SP', password='', domain='aoscloud.test', tenant=None):
        """A user .p12 with its intermediate certificate, as aos-keys saves it."""
        name = f'user-{role}-{out.stem}'
        extensions = self.folder / 'user.ext'
        extensions.write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n')
        person = PERSON.replace('+', r'\+')  # as it is in a real certificate, and -subj needs the + escaped
        unit = role if tenant == '' else f"{role} {tenant or person}"
        self.sign(name, f'/CN={person}/O={domain}/OU={unit}', extensions, issuer='intermediate')
        out.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, P=password)
        run('openssl', 'pkcs12', '-export', '-inkey', self.folder / f'{name}.key', '-in', self.folder / f'{name}.crt',
            '-certfile', self.intermediate, '-out', out, '-passout', 'env:P', env=env)


class Cloud(BaseHTTPRequestHandler):
    """The answers of the calls the script makes. The class holds the state of one test."""

    state = {}

    def log_message(self, *_):
        pass

    def answer(self, body, code=200):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def batch(self, batch_id, version, state):
        return {'id': batch_id, 'state': state, 'is_locked': False, 'architectures': ['amd64'],
                'update_items': [{'identity_id': SERVICE, 'version': version}]}

    def do_PATCH(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.state['patches'].append(body)
        self.state['new_state'] = 'Valid' if body[0]['is_approved'] else 'Invalid'
        self.answer(self.batch(NEW_BATCH, '4.10.0', self.state['new_state']))

    def do_GET(self):
        state = self.state
        path = self.path.split('?')[0].split('/api/v11/')[-1]
        if state.get('forbid'):
            return self.answer({'detail': 'forbidden'}, 403)
        if path == 'services/':
            rows = [{'id': SERVICE, 'codename': 'demo', 'title': 'Demo'}]
            if state['two_services']:
                rows.append({'id': OTHER, 'codename': 'second', 'title': 'Second'})
            return self.answer({'total': len(rows), 'offset': 0, 'items': rows})
        if path == 'verification-batch/':
            rows = [self.batch(OLD_BATCH, '4.9.0', 'Valid')]
            if state['waiting']:
                rows.insert(0, self.batch(NEW_BATCH, '4.10.0', state['new_state']))
            return self.answer({'total': len(rows), 'offset': 0, 'items': rows})
        if path.endswith('/statistics/'):
            return self.answer({'sub_batches': [{'architecture': 'amd64', 'verification_set': {'unit_ids': [UNIT]}}]})
        if match := re.fullmatch(r'verification-batch/([^/]+)/', path):
            if match[1] == NEW_BATCH:
                return self.answer(self.batch(NEW_BATCH, '4.10.0', state['new_state']))
            return self.answer(self.batch(OLD_BATCH, '4.9.0', 'Valid'))
        if path == f'services/{SERVICE}/units/':
            return self.answer({'total': 1, 'offset': 0, 'items': [{'id': UNIT}]})
        if path == f'units/{UNIT}/':
            return self.answer({'id': UNIT, 'online_status': 'offline' if state['offline'] else 'online'})
        if path == f'units/{UNIT}/subjects-services/{SERVICE}/':
            version = '4.10.0' if state['waiting'] else '4.9.0'
            return self.answer({'items': [{'service_versions': {'installed_service_version': {'version': version}},
                                           'instances': [{'version': version, 'run_state': state['run_state']}]}]})
        return self.answer({'detail': path}, 404)


@pytest.fixture(scope='session')
def pki(tmp_path_factory):
    return Pki(tmp_path_factory.mktemp('pki'))


@pytest.fixture(scope='session')
def server(pki):
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Cloud)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(pki.folder / 'server.crt', pki.folder / 'server.key')
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f'https://127.0.0.1:{httpd.server_address[1]}/api/v11'
    httpd.shutdown()


@pytest.fixture(scope='session')
def venv(tmp_path_factory):
    """One .venv for all tests: setup finds it with requests in it, and does not install anything."""
    folder = tmp_path_factory.mktemp('venv') / '.venv'
    subprocess.run([sys.executable, '-m', 'venv', '--system-site-packages', folder], check=True)
    python = folder / 'bin' / 'python'
    if subprocess.run([python, '-c', 'import requests'], capture_output=True, check=False).returncode:
        (folder / 'lib' / 'requests.pth').unlink(missing_ok=True)
        subprocess.run([python, '-m', 'pip', 'install', '-q', 'requests'], check=True)
    return folder


class Box:
    """A copy of the example in a temporary HOME."""

    def __init__(self, tmp_path, pki, url, venv_folder):
        self.home, self.tree, self.url, self.pki = tmp_path / 'home', tmp_path / 'tree', url, pki
        self.home.mkdir()
        self.tree.mkdir()
        for name in ('aos-verify', 'verify_batch.py'):
            shutil.copy2(ROOT / name, self.tree / name)
        shutil.copytree(ROOT / 'tests', self.tree / 'tests', ignore=shutil.ignore_patterns('__pycache__', 'test_*'))
        (self.tree / '.venv').symlink_to(venv_folder)
        files = self.home / '.aos/venv/lib/python3.12/site-packages/aos_keys/files'
        files.mkdir(parents=True)
        shutil.copy(pki.ca, files / '1rootCA.crt')
        self.path = os.environ['PATH']

    def __call__(self, *args, stdin='', **env):
        clean = {'HOME': str(self.home), 'PATH': self.path, 'LC_ALL': 'C.UTF-8', 'WATCH_SECONDS': '0', **env}
        return subprocess.run([str(self.tree / 'aos-verify'), *args], input=stdin, env=clean, cwd=self.tree,
                              capture_output=True, text=True, timeout=120, check=False)

    def setup(self, stdin='\n', p12=True):
        if p12:
            self.pki.p12(self.home / '.aos/security/aos-user-sp.p12')
        done = self('setup', '--api-url', self.url, stdin=stdin)
        assert done.returncode == 0, done.stdout + done.stderr
        return done


@pytest.fixture
def box(tmp_path, pki, server, venv):
    Cloud.state = {'patches': [], 'two_services': False, 'waiting': False, 'new_state': 'Waiting_validation',
                   'offline': False, 'run_state': 'active', 'forbid': False}
    return Box(tmp_path, pki, server, venv)


def test_setup_with_one_certificate_says_what_it_did_and_ends_with_the_check(box):
    done = box.setup()
    assert done.stdout.count('OK    ') >= 6
    assert '.aos/' not in done.stdout.split('certificate: ')[1].split('\n')[0]
    assert 'security/aos-user-sp.p12' in done.stdout
    assert 'connection OK' in done.stdout and 'latest batch …ef77, version 4.9.0' in done.stdout
    assert 'Unit …318d: online; installed 4.9.0; instances: 4.9.0 active' in done.stdout
    assert 'Ready.' in done.stdout


def test_setup_keeps_the_settings_private_and_the_chain_in_the_pem(box):
    box.setup()
    for name in ('.aos-verify.env', '.aos-verify/user.pem', '.aos-verify/aos-root-ca.crt'):
        assert stat.S_IMODE((box.tree / name).stat().st_mode) == 0o600, name
    pem = (box.tree / '.aos-verify/user.pem').read_text()
    assert pem.count('BEGIN CERTIFICATE') == 2 and 'PRIVATE KEY' in pem  # the user and the intermediate: no -clcerts
    settings = (box.tree / '.aos-verify.env').read_text()
    assert f"export SERVICE_ID='{SERVICE}'" in settings and f"export AOS_API_URL='{box.url}'" in settings
    assert 'PRIVATE KEY' not in settings


def test_the_files_setup_writes_are_ignored_by_git():
    ignored = (ROOT / '.gitignore').read_text().splitlines()
    assert {'.venv/', '.aos-verify/', '.aos-verify.env', '*.pem', 'report/'} <= set(ignored)


def test_setup_with_two_certificates_asks_and_never_shows_the_user(box):
    box.pki.p12(box.home / '.aos/security/aos-user-oem.p12', role='OEM')
    box.pki.p12(box.home / '.aos/security/prod/aos-user-sp-prod.p12')
    box.pki.p12(box.home / '.aos/security/aos-user-sp.p12')
    done = box.setup(stdin='2\n', p12=False)
    assert 'Found 2 SP certificates' in done.stdout
    assert '1) security/aos-user-sp.p12' in done.stdout and '2) security/prod/aos-user-sp-prod.p12' in done.stdout
    assert 'oem' not in done.stdout
    assert PERSON not in done.stdout and PERSON.split('@', maxsplit=1)[0] not in done.stdout
    assert 'certificate: security/prod/aos-user-sp-prod.p12' in done.stdout


def test_a_wrong_number_stops_with_the_command_to_fix_it(box):
    box.pki.p12(box.home / '.aos/security/a.p12')
    box.pki.p12(box.home / '.aos/security/b.p12')
    done = box('setup', '--api-url', box.url, stdin='7\n')
    assert done.returncode == 2 and "not a number from the list: '7'" in done.stderr
    assert 'Fix:  ./aos-verify setup' in done.stderr


def test_no_certificate_is_one_line_and_one_command(box):
    done = box('setup', '--api-url', box.url)
    assert done.returncode == 2
    assert done.stderr.splitlines() == ['STOP: no SP certificate (.p12) under ~/.aos/security',
                                        'Fix:  ./aos-verify setup --p12 /path/to/your-sp.p12']


def test_only_an_oem_certificate_does_not_count(box):
    box.pki.p12(box.home / '.aos/security/aos-user.p12', role='OEM')
    assert 'no SP certificate' in box('setup', '--api-url', box.url).stderr


def test_a_password_is_asked_with_an_explanation_and_stays_off_the_screen(box):
    box.pki.p12(box.home / '.aos/security/aos-user-sp.p12', password='s3cret-pw')
    done = box('setup', '--api-url', box.url, stdin='wrong\ns3cret-pw\n')
    assert done.returncode == 0, done.stderr
    assert done.stdout.count('Password of this .p12 file (not of your computer; often empty') == 2
    assert 's3cret-pw' not in done.stdout + done.stderr
    assert 'PRIVATE KEY' in (box.tree / '.aos-verify/user.pem').read_text()


def test_the_root_ca_of_aos_signer_is_not_taken(box):
    files = box.home / '.aos/venv/lib/python3.12/site-packages'
    shutil.rmtree(files / 'aos_keys')
    (files / 'aos_signer/files').mkdir(parents=True)
    shutil.copy(box.pki.ca, files / 'aos_signer/files/1rootCA.crt')
    box.pki.p12(box.home / '.aos/security/aos-user-sp.p12')
    done = box('setup', '--api-url', box.url)
    assert done.returncode == 2 and 'the AosEdge root CA was not found' in done.stderr
    assert 'Fix:  ./aos-verify setup --ca /path/to/1rootCA.crt' in done.stderr


def test_setup_lists_the_services_and_lets_you_pick(box):
    Cloud.state['two_services'] = True
    done = box.setup(stdin='1\n')
    assert '1) demo  (Demo)' in done.stdout and '2) second  (Second)' in done.stdout
    assert 'Service: demo' in done.stdout


def test_setup_takes_the_answers_as_options(box):
    Cloud.state['two_services'] = True
    box.pki.p12(box.home / 'elsewhere/my.p12')
    done = box('setup', '--p12', box.home / 'elsewhere/my.p12', '--api-url', box.url, '--service', 'demo')
    assert done.returncode == 0 and 'Service: demo' in done.stdout


def test_a_refused_certificate_is_one_line_and_one_command(box):
    Cloud.state['forbid'] = True
    box.pki.p12(box.home / '.aos/security/aos-user-sp.p12')
    done = box('setup', '--api-url', box.url)
    assert done.returncode == 2 and 'not the SP certificate of this AosCloud (403)' in done.stderr
    assert 'Fix:  ./aos-verify setup' in done.stderr and 'Traceback' not in done.stderr


def test_an_address_that_is_not_there_stops(box):
    box.pki.p12(box.home / '.aos/security/aos-user-sp.p12')
    done = box('setup', '--api-url', 'https://127.0.0.1:9/api/v11')
    assert done.returncode == 2 and 'cannot reach https://127.0.0.1:9/api/v11' in done.stderr


@pytest.mark.parametrize('tool', ['jq', 'curl', 'openssl'])
def test_a_missing_tool_gets_the_install_command(box, tool):
    bin_dir = box.home / 'bin'
    bin_dir.mkdir()
    for name in ('bash', 'python3', 'jq', 'curl', 'openssl', 'dirname', 'uname', 'env'):
        if name != tool and shutil.which(name):
            (bin_dir / name).symlink_to(shutil.which(name))
    box.path = str(bin_dir)
    done = box('setup')
    assert done.returncode == 2 and f'missing: {tool}' in done.stderr
    assert re.search(r'Fix:  (brew install|sudo apt-get install|winget install)', done.stderr)


def test_a_python_that_is_too_old_gets_the_install_command(box):
    bin_dir = box.home / 'bin'
    bin_dir.mkdir()
    for name in ('bash', 'jq', 'curl', 'openssl', 'dirname', 'uname', 'env'):
        (bin_dir / name).symlink_to(shutil.which(name))
    old = bin_dir / 'python3'
    old.write_text('#!/bin/sh\nexit 1\n')
    old.chmod(0o755)
    box.path = str(bin_dir)
    done = box('setup')
    assert done.returncode == 2 and 'missing: python3 (3.10 or later)' in done.stderr


def test_setup_creates_a_venv_when_there_is_none(box):
    (box.tree / '.venv').unlink()
    box.pki.p12(box.home / '.aos/security/aos-user-sp.p12')
    done = box('setup', '--api-url', box.url, PIP_INDEX_URL='http://127.0.0.1:9/', PIP_RETRIES='0', PIP_TIMEOUT='1')
    assert (box.tree / '.venv/bin/python').exists()
    assert done.returncode == 2 and 'could not install requests' in done.stderr


def test_run_before_setup_says_setup(box):
    done = box('run')
    assert done.stderr.splitlines() == ['STOP: not set up yet', 'Fix:  ./aos-verify setup']


def test_run_with_nothing_waiting_says_so_and_sends_nothing(box):
    box.setup()
    done = box('run', '--poll', '0')
    assert done.returncode == 0 and 'no waiting batch: the highest version 4.9.0 is Valid' in done.stdout
    assert 'Nothing to verify. Upload first, then run.' in done.stdout and Cloud.state['patches'] == []


def test_run_dry_run_goes_up_to_the_decision_and_sends_nothing(box):
    box.setup()
    Cloud.state['waiting'] = True
    done = box('run', '--dry-run', '--poll', '0')
    assert done.returncode == 0, done.stderr
    assert 'batch …a410, version 4.10.0, test Units: …318d' in done.stdout
    assert 'PASS A1' in done.stdout and 'PASS B1' in done.stdout and 'PASS C2' in done.stdout
    assert 'Verdict: approve (all checks passed)' in done.stdout
    assert 'Dry run: nothing sent to AosCloud' in done.stdout
    assert Cloud.state['patches'] == [] and (box.tree / 'report/report.html').exists()
    assert 'found by ./aos-verify on this computer' in (box.tree / 'report/report.html').read_text()


def test_run_asks_before_the_decision_and_no_means_nothing_sent(box):
    box.setup()
    Cloud.state['waiting'] = True
    done = box('run', '--poll', '0', stdin='n\n')
    assert done.returncode == 0 and 'approve version 4.10.0 in AosCloud?' in done.stdout
    assert 'Nothing sent. To decide later: ./aos-verify decide approve' in done.stdout
    assert Cloud.state['patches'] == []


def test_run_with_no_terminal_and_no_yes_sends_nothing(box):
    box.setup()
    Cloud.state['waiting'] = True
    assert box('run', '--poll', '0', stdin='').returncode == 0
    assert Cloud.state['patches'] == []


def test_run_yes_at_the_prompt_sends_one_approval(box):
    box.setup()
    Cloud.state['waiting'] = True
    done = box('run', '--poll', '0', stdin='y\n')
    assert done.returncode == 0, done.stderr
    assert 'batch …a410 (amd64): approved, now Verified (Valid)' in done.stdout
    assert Cloud.state['patches'] == [[{'architecture': 'amd64', 'is_approved': True}]]


def test_run_with_yes_does_not_ask(box):
    box.setup()
    Cloud.state['waiting'] = True
    done = box('run', '--yes', '--poll', '0')
    assert done.returncode == 0 and 'This writes one decision' not in done.stdout
    assert len(Cloud.state['patches']) == 1


def test_run_twice_does_not_decide_twice(box):
    box.setup()
    Cloud.state['waiting'] = True
    assert box('run', '--yes', '--poll', '0').returncode == 0
    again = box('run', '--yes', '--poll', '0')
    assert again.returncode == 0 and 'no waiting batch: the highest version 4.10.0 is Valid' in again.stdout
    assert len(Cloud.state['patches']) == 1


def test_a_failed_unit_ends_in_a_reject_after_asking(box):
    box.setup()
    Cloud.state.update(waiting=True, run_state='failed')
    done = box('run', '--poll', '0', stdin='y\n')
    assert done.returncode == 1 and 'Verdict: reject (the version failed on a test Unit)' in done.stdout
    assert Cloud.state['patches'] == [[{'architecture': 'amd64', 'is_approved': False}]]


def test_an_offline_unit_decides_nothing(box):
    box.setup()
    Cloud.state.update(waiting=True, offline=True)
    done = box('run', '--yes', '--poll', '0', '--timeout', '1')
    assert done.returncode == 3 and 'decide nothing' in done.stderr
    assert 'Fix:  ./aos-verify check' in done.stderr and Cloud.state['patches'] == []


def test_a_failing_test_of_yours_rejects(box):
    box.setup()
    Cloud.state['waiting'] = True
    (box.tree / 'tests/your_tests.sh').write_text('echo "FAIL smoke: broken"\nexit 1\n')
    done = box('run', '--yes', '--poll', '0')
    assert done.returncode == 1 and 'Verdict: reject (a check failed)' in done.stdout
    assert Cloud.state['patches'][0][0]['is_approved'] is False


def test_your_tests_do_not_see_the_key(box):
    box.setup()
    Cloud.state['waiting'] = True
    (box.tree / 'tests/your_tests.sh').write_text('echo "PASS key_is_${AOS_CERT:-absent}"\n')
    assert 'PASS key_is_absent' in box('run', '--dry-run', '--poll', '0').stdout


def test_the_debug_commands_one_at_a_time(box):
    box.setup()
    assert 'connection OK' in box('check').stdout
    nothing = box('discover')
    assert nothing.returncode == 0 and 'no waiting batch' in nothing.stdout
    assert 'no batch found yet' in box('wait').stderr and 'no batch found yet' in box('test').stderr
    Cloud.state['waiting'] = True
    found = box('discover')
    assert 'batch …a410, version 4.10.0, test Units: …318d' in found.stdout and 'Next: ./aos-verify wait' in found.stdout
    waited = box('wait')
    assert waited.returncode == 0 and 'exit code: 0' in waited.stdout
    tested = box('test')
    assert tested.returncode == 0 and 'all checks passed: approve' in tested.stdout
    assert 'report.html' in box('report', PATH=box.path).stdout or (box.tree / 'report/report.html').exists()
    decided = box('decide', 'reject')
    assert decided.returncode == 0 and 'rejected, now Invalid' in decided.stdout
    assert Cloud.state['patches'] == [[{'architecture': 'amd64', 'is_approved': False}]]
    again = box('wait')
    assert again.returncode == 2 and 'nothing to decide' in again.stderr
    refused = box('decide', 'approve')
    assert refused.returncode == 2 and 'Nothing was sent.' in refused.stdout and len(Cloud.state['patches']) == 1


def test_decide_needs_to_be_told_which(box):
    assert 'say which: approve or reject' in box('decide').stderr


def test_secrets_prints_what_to_paste_and_never_the_key(box):
    box.setup()
    done = box('secrets', '--repo', 'my-org/my-repo')
    assert done.returncode == 0
    text = done.stdout + done.stderr
    assert 'PRIVATE KEY' not in text and 'BEGIN CERTIFICATE' not in text and PERSON not in text
    assert 'gh api -X PUT repos/my-org/my-repo/environments/aos-verification' in text
    for name in ('AOS_USER_PEM', 'AOS_ROOT_CA_PEM', 'AOS_SERVICE_ID', 'AOS_API_URL'):
        assert f'gh secret set {name} --repo my-org/my-repo --env aos-verification' in text
    assert f"AOS_USER_PEM --repo my-org/my-repo --env aos-verification < '{box.tree}/.aos-verify/user.pem'" in text


def test_secrets_asks_for_the_repository_and_runs_setup_when_needed(box):
    box.pki.p12(box.home / '.aos/security/aos-user-sp.p12')
    done = box('secrets', stdin=f'{box.url}\n\nmine/repo\n')
    assert done.returncode == 0, done.stderr
    assert 'Ready.' in done.stdout and 'repos/mine/repo/environments' in done.stdout


@pytest.mark.parametrize('bad', ['nope', 'a/b/c', '/x', 'x/', 'a b/c'])
def test_secrets_refuses_what_is_not_org_slash_name(box, bad):
    box.setup()
    done = box('secrets', '--repo', bad)
    assert done.returncode == 2 and 'not org/name' in done.stderr


def test_the_script_is_executable_and_has_no_newer_bash():
    script = ROOT / 'aos-verify'
    assert os.access(script, os.X_OK)
    text = script.read_text(encoding='utf-8')
    assert 'mapfile' not in text and 'readarray' not in text and 'declare -A' not in text


def test_the_workflows_take_the_api_url_as_a_secret_or_a_variable():
    for name in ('verify-release.yml', 'watch-verification.yml'):
        text = (ROOT / '.github/workflows' / name).read_text(encoding='utf-8')
        assert 'AOS_API_URL: ${{ secrets.AOS_API_URL || vars.AOS_API_URL }}' in text


def report_of(box):
    return (box.tree / 'report/report.html').read_text(encoding='utf-8')


def failing_version(box, kind):
    """A version that fails: on a test Unit (wait sees it), or in your tests."""
    if kind == 'on a Unit':
        Cloud.state['run_state'] = 'failed'
    else:
        (box.tree / 'tests/your_tests.sh').write_text('echo "FAIL smoke: broken"\n')


def test_setup_asks_for_the_decision_mode_once_and_keeps_it(box):
    done = box.setup(stdin='\n')
    assert 'After the checks, who decides?' in done.stdout and 'OK    decision mode: ask' in done.stdout
    assert "export AOS_DECISION='ask'" in (box.tree / '.aos-verify.env').read_text()
    done = box('setup', '--api-url', box.url, stdin='report-only\n')
    assert "export AOS_DECISION='report-only'" in (box.tree / '.aos-verify.env').read_text()
    done = box('setup', '--api-url', box.url, stdin='\n')
    assert 'Decision mode [report-only]: ' in done.stdout
    assert "export AOS_DECISION='report-only'" in (box.tree / '.aos-verify.env').read_text()


def test_setup_takes_the_decision_mode_as_an_option_or_from_the_environment(box):
    box.pki.p12(box.home / '.aos/security/aos-user-sp.p12')
    done = box('setup', '--api-url', box.url, '--decision', 'auto')
    assert 'who decides' not in done.stdout and "export AOS_DECISION='auto'" in (box.tree / '.aos-verify.env').read_text()
    done = box('setup', '--api-url', box.url, AOS_DECISION='report-only')
    assert 'who decides' not in done.stdout and 'decision mode: report-only' in done.stdout


def test_a_wrong_decision_mode_stops_with_the_fix(box):
    box.pki.p12(box.home / '.aos/security/aos-user-sp.p12')
    done = box('setup', '--api-url', box.url, stdin='maybe\n')
    assert done.returncode == 2 and "AOS_DECISION must be auto, ask or report-only, not 'maybe'" in done.stderr
    assert not (box.tree / '.aos-verify.env').exists()
    box.setup()
    Cloud.state['waiting'] = True
    done = box('run', '--poll', '0', AOS_DECISION='sometimes')
    assert done.returncode == 2 and 'not \'sometimes\'' in done.stderr and Cloud.state['patches'] == []


def test_auto_approves_what_passes_without_asking(box):
    box.setup()
    Cloud.state['waiting'] = True
    done = box('run', '--poll', '0', AOS_DECISION='auto')
    assert done.returncode == 0 and 'This writes one decision' not in done.stdout
    assert Cloud.state['patches'] == [[{'architecture': 'amd64', 'is_approved': True}]]
    page = report_of(box)
    assert 'PASS → approve' in page and 'batch now Verified' in page and 'not sent yet' not in page


@pytest.mark.parametrize('kind', ['on a Unit', 'in your tests'])
def test_auto_rejects_what_fails_without_asking(box, kind):
    box.setup()
    Cloud.state['waiting'] = True
    failing_version(box, kind)
    done = box('run', '--poll', '0', AOS_DECISION='auto')
    assert done.returncode == 1 and 'This writes one decision' not in done.stdout
    assert Cloud.state['patches'] == [[{'architecture': 'amd64', 'is_approved': False}]]
    page = report_of(box)
    assert 'FAIL → reject' in page and 'Decision: rejected,' in page and 'batch now Invalid' in page


def test_the_mode_from_setup_counts_and_the_environment_overrides_it(box):
    box.pki.p12(box.home / '.aos/security/aos-user-sp.p12')
    box('setup', '--api-url', box.url, '--decision', 'auto')
    Cloud.state['waiting'] = True
    done = box('run', '--poll', '0', '--dry-run')
    assert 'It would approve batch …a410 (auto)' in done.stdout
    done = box('run', '--poll', '0', '--dry-run', AOS_DECISION='report-only')
    assert 'report-only sends nothing anyway' in done.stdout


def test_yes_is_the_short_form_of_auto_whatever_the_setting(box):
    box.pki.p12(box.home / '.aos/security/aos-user-sp.p12')
    box('setup', '--api-url', box.url, '--decision', 'report-only')
    Cloud.state['waiting'] = True
    assert box('run', '--yes', '--poll', '0').returncode == 0 and len(Cloud.state['patches']) == 1


@pytest.mark.parametrize('answer, patches', [('y\n', 1), ('n\n', 0)])
def test_ask_on_a_version_that_passes(box, answer, patches):
    box.setup()
    Cloud.state['waiting'] = True
    done = box('run', '--poll', '0', stdin=answer)
    assert done.returncode == 0 and 'approve version 4.10.0 in AosCloud?' in done.stdout
    assert len(Cloud.state['patches']) == patches
    assert ('batch now Verified' if patches else 'Decision: not sent (you answered no).') in report_of(box)


@pytest.mark.parametrize('kind', ['on a Unit', 'in your tests'])
@pytest.mark.parametrize('answer, patches', [('y\n', 1), ('n\n', 0)])
def test_ask_on_a_version_that_fails(box, kind, answer, patches):
    box.setup()
    Cloud.state['waiting'] = True
    failing_version(box, kind)
    done = box('run', '--poll', '0', stdin=answer)
    assert done.returncode == 1 and 'reject version 4.10.0 in AosCloud?' in done.stdout
    assert len(Cloud.state['patches']) == patches
    assert 'FAIL → reject' in report_of(box)


def test_report_only_sends_nothing_on_a_version_that_passes(box):
    box.setup()
    Cloud.state['waiting'] = True
    done = box('run', '--poll', '0', AOS_DECISION='report-only')
    assert done.returncode == 0 and 'Report only: nothing sent' in done.stdout and Cloud.state['patches'] == []
    assert 'This writes one decision' not in done.stdout
    assert (box.tree / 'report/junit.xml').exists()
    page = report_of(box)
    assert 'PASS → approve' in page and 'Decision: report-only, nothing sent.' in page


@pytest.mark.parametrize('kind', ['on a Unit', 'in your tests'])
def test_report_only_sends_nothing_on_a_version_that_fails_and_still_writes_it_down(box, kind):
    box.setup()
    Cloud.state['waiting'] = True
    failing_version(box, kind)
    done = box('run', '--poll', '0', AOS_DECISION='report-only')
    assert done.returncode == 1 and Cloud.state['patches'] == [] and 'Report only: nothing sent' in done.stdout
    assert (box.tree / 'report/junit.xml').exists()
    page = report_of(box)
    assert 'FAIL → reject' in page and 'Decision: report-only, nothing sent.' in page


@pytest.mark.parametrize('mode', ['auto', 'ask', 'report-only'])
def test_a_dry_run_sends_nothing_in_any_mode(box, mode):
    box.setup()
    Cloud.state['waiting'] = True
    done = box('run', '--poll', '0', '--dry-run', AOS_DECISION=mode)
    assert done.returncode == 0 and 'Dry run: nothing sent to AosCloud' in done.stdout
    assert 'This writes one decision' not in done.stdout and Cloud.state['patches'] == []
    assert 'Decision: not sent (a dry run).' in report_of(box)


def test_decide_by_hand_writes_the_decision_into_the_report(box):
    box.setup()
    Cloud.state['waiting'] = True
    assert box('run', '--poll', '0', stdin='n\n').returncode == 0
    assert box('decide', 'approve').returncode == 0
    assert 'batch now Verified' in report_of(box)


def test_the_report_shows_the_local_time_too(box):
    box.setup()
    Cloud.state['waiting'] = True
    box('run', '--poll', '0', '--dry-run', TZ='Asia/Tokyo')
    assert re.search(r'\d\d:\d\d UTC \((\d{4}-\d\d-\d\d )?\d\d:\d\d local\)', report_of(box))


def test_run_says_how_long_it_will_still_ask(box):
    box.setup()
    done = box('run', AOS_DISCOVER_TIMEOUT='2')
    assert done.returncode == 0 and Cloud.state['patches'] == []
    assert re.search(r'asking again in \d+ s \(0:0\d left, Ctrl-C to stop\)', done.stdout)
    assert 'Nothing to verify after 2 s. Upload first, then run.' in done.stdout


def test_the_discover_limit_in_words_and_a_wrong_value(box):
    box.setup()
    done = box('run', AOS_DISCOVER_TIMEOUT='soon')
    assert done.returncode == 2 and "must be a number of seconds, not 'soon'" in done.stderr
    assert '2 min' in box('help').stdout or 'default 120' in box('help').stdout


def test_setup_names_the_sp_of_each_certificate(box):
    box.pki.p12(box.home / '.aos/security/a.p12', tenant='Honda Motor')
    box.pki.p12(box.home / '.aos/security/b.p12')  # a test tenant: its name is the e-mail of a person
    box.pki.p12(box.home / '.aos/security/old/c.p12', tenant='')  # no name: the folder says which one
    done = box.setup(stdin='1\n', p12=False)
    assert '1) security/a.p12  (Honda Motor, aoscloud.test, valid to' in done.stdout
    assert '2) security/b.p12  (…+1@example.com, aoscloud.test, valid to' in done.stdout
    assert '3) security/old/c.p12  (no SP name in it, see the folder, aoscloud.test, valid to' in done.stdout
    assert PERSON not in done.stdout and 'Some.Person' not in done.stdout


FULL_ID = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}')


def no_full_id(box, *done):
    for one in done:
        for text in (one.stdout, one.stderr):
            assert not FULL_ID.search(text), text
    for path in (box.tree / 'report').glob('*'):
        assert not FULL_ID.search(path.read_text(encoding='utf-8')), path


def test_no_full_id_on_the_screen_or_in_the_report_when_it_passes(box):
    done = [box.setup(), box('check'), box('discover')]
    Cloud.state['waiting'] = True
    done += [box('discover'), box('wait'), box('test'), box('decide', 'approve'), box('secrets', '--repo', 'a/b'),
             box('check'), box('run', '--poll', '0', '--dry-run')]
    no_full_id(box, *done)


@pytest.mark.parametrize('mode', ['auto', 'report-only'])
@pytest.mark.parametrize('kind', ['on a Unit', 'in your tests'])
def test_no_full_id_on_the_screen_or_in_the_report_when_it_fails(box, mode, kind):
    setup = box.setup()
    Cloud.state['waiting'] = True
    failing_version(box, kind)
    no_full_id(box, setup, box('run', '--poll', '0', AOS_DECISION=mode))


def test_no_full_id_when_a_unit_is_offline_or_the_batch_is_decided(box):
    setup = box.setup()
    Cloud.state.update(waiting=True, offline=True)
    offline = box('run', '--yes', '--poll', '0', '--timeout', '1')
    Cloud.state.update(offline=False)
    first = box('run', '--yes', '--poll', '0')
    no_full_id(box, setup, offline, first, box('decide', 'approve'), box('wait'))


def test_no_full_id_when_aoscloud_is_gone(box):
    setup = box.setup()
    Cloud.state['waiting'] = True
    assert box('discover').returncode == 0
    settings = box.tree / '.aos-verify.env'
    settings.write_text(settings.read_text().replace(box.url, 'https://127.0.0.1:9/api/v11'))
    no_full_id(box, setup, box('check'), box('decide', 'approve'), box('wait', '--timeout', '1'))
    assert Cloud.state['patches'] == []


def test_secrets_never_prints_the_service_id_and_gives_it_from_a_file(box):
    box.setup()
    done = box('secrets', '--repo', 'a/b')
    assert SERVICE not in done.stdout
    assert f"AOS_SERVICE_ID --repo a/b --env aos-verification < '{box.tree}/.aos-verify/service_id'" in done.stdout
    assert (box.tree / '.aos-verify/service_id').read_text() == SERVICE
    assert 'gh variable set AOS_DECISION --repo a/b --env aos-verification --body report-only' in done.stdout
