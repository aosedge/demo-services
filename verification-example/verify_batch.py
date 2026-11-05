#!/usr/bin/env python3
# Copyright (c) 2018-2026 EPAM Systems Inc.
# SPDX-License-Identifier: Apache-2.0
"""Find the Verification batch of a new Service version, wait for it, then approve or reject it.

    python verify_batch.py discover [--service-id ID] [--unit UNIT_ID ...] [--dry-run]
    python verify_batch.py services
    python verify_batch.py check  [--service-id ID] [--unit UNIT_ID ...]
    python verify_batch.py wait   --service-id ID --version X.Y.Z [--unit UNIT_ID ...]
    python verify_batch.py status --service-id ID --version X.Y.Z --batch-id ID --unit UNIT_ID [--unit ...]
    python verify_batch.py decide --batch-id ID (--approve | --reject) [--arch ARCH ...]

discover, services, check and status only read. services: the Services this certificate sees, one line each (id, codename,
title). discover: is there a new version to verify, and on which Units? It prints one
JSON line {"batch_id", "version", "units"} when there is. check: the connection, the latest batch of the Service and what each Unit runs.
status: the checks that wait already proved, read again, one tab-separated line each (code, name, PASS/FAIL, detail).

Environment: AOS_API_URL (https://<api host>:10000/api/v11), AOS_CERT (one PEM file with your user certificate, the
intermediate certificates and the key; or set AOS_KEY to a separate key file), AOS_CA (the AosEdge root CA file).
Needs Python 3.10+ and requests.

Exit codes of discover: 0 a batch was found (the JSON line is printed) or nothing waits (nothing is printed),
                      2 start nothing: no test Unit for the batch, or an error. --dry-run: 0 also without a test Unit.
Exit codes of check:  0 connected, 2 an error (TLS, a wrong role or id): read the message.
Exit codes of status: 0 every check passed, 1 a check failed, 2 an error.
Exit codes of wait:   0 ready (the batch id is printed), 1 the version failed on a Unit (the batch id is printed:
                      reject it), 2 decide nothing: timed out, no batch, the batch is already decided or locked, no
                      test Unit, or an error that waiting cannot fix (a setting, a TLS error, an HTTP 4xx answer),
                      3 decide nothing: timed out while AosCloud could not be reached or a test Unit was offline.
Exit codes of decide: 0 sent, 2 refused or not sent (the batch is not waiting, an unknown architecture, AosCloud
                      not reachable, or an error), 3 the decision may have been applied (no answer, or HTTP 5xx):
                      check the batch in the UI.
Only a lost connection, a timeout and HTTP 5xx are retried, and only while waiting.
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

WAITING = 'Waiting_validation'
FAILED_STATUSES = ('error', 'resource conflict', 'rejected')
PAGE = 50
PREVIEW = 3  # the batch list shows at most this many items of a batch; read the batch itself for the rest
SEMVER = re.compile(r'(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?')
AN_ID = re.compile(r'[0-9A-Za-z_-]+')


class Stop(Exception):
    """An error that waiting cannot fix: a setting, a TLS error, or an HTTP 4xx answer."""


class Unavailable(Exception):
    """AosCloud did not answer, or answered HTTP 5xx: worth another try."""


def api() -> tuple[str, requests.Session]:
    """The API base URL and a session that uses mutual TLS with your certificate."""
    base = os.environ.get('AOS_API_URL', '').strip().rstrip('/')
    if not base.startswith('https://'):
        raise Stop(f'AOS_API_URL must be https://<api host>:10000/api/v11, not {base!r}')
    for name in ('AOS_CERT', 'AOS_CA'):
        if not os.environ.get(name):
            raise Stop(f'{name} is not set')
        if not os.path.isfile(os.environ[name]):
            raise Stop(f'{name}: there is no file {os.environ[name]}')
    session = requests.Session()
    key = os.environ.get('AOS_KEY')
    session.cert = (os.environ['AOS_CERT'], key) if key else os.environ['AOS_CERT']
    session.verify = os.environ['AOS_CA']
    return base, session


def get(path: str, **params) -> dict:
    base, session = api()
    shown = re.sub(r'[0-9a-f]{8}-[0-9a-f-]{27}', lambda m: short(m[0]), path)
    try:
        answer = session.get(f'{base}/{path}', params=params, timeout=30)
    except requests.exceptions.SSLError as e:
        raise Stop(f'TLS error: check AOS_CERT (with the intermediate certificates) and AOS_CA: {e}') from e
    except (requests.ConnectionError, requests.Timeout) as e:
        raise Unavailable(str(e)) from e
    except requests.RequestException as e:
        raise Stop(f'GET {shown}: {e}') from e
    if answer.status_code >= 500:
        raise Unavailable(f'GET {shown}: HTTP {answer.status_code}')
    if answer.status_code in (401, 403):
        raise Stop(f'GET {shown}: HTTP {answer.status_code}: this certificate may not read it; use the SP user '
                   f'certificate of this AosCloud')
    if answer.status_code == 404:
        raise Stop(f'GET {shown}: HTTP 404: not found; check the id (the Unit id, not its System UID)')
    if answer.status_code >= 400:
        raise Stop(f'GET {shown}: HTTP {answer.status_code} {answer.text[:200]}')
    return answer.json()


def hide(*values) -> None:
    """In GitHub Actions, keep these ids out of the rest of the job's log. Only well-formed ids reach the command."""
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        for value in values:
            if isinstance(value, str) and AN_ID.fullmatch(value):
                print(f'::add-mask::{value}', file=sys.stderr, flush=True)


def short(value) -> str:
    """An id shortened for logs and reports: ... plus its last 4 characters."""
    return f'…{str(value)[-4:]}' if value else '-'


def holds(batch: dict, service_id: str, version: str | None = None) -> bool:
    return any(item.get('identity_id') == service_id and (version is None or item.get('version') == version)
               for item in batch.get('update_items') or [])


def items(page) -> list:
    """The entries of a list answer: {total, offset, items} (API v11) or {count, results}."""
    if isinstance(page, dict):
        return page.get('items') or page.get('results') or []
    return page or []


def pages(path: str, **params):
    """Every entry of a list call, page by page."""
    offset = 0
    while True:
        page = get(path, limit=PAGE, offset=offset, **params)
        entries = items(page)
        yield from entries
        offset += len(entries)
        total = (page.get('total') or page.get('count')) if isinstance(page, dict) else None
        if not entries or total is None or offset >= total:
            return


def complete(batch: dict, service_id: str) -> dict:
    """The batch with all its items: the list may cut them off, and your Service may be among the rest."""
    if not holds(batch, service_id) and len(batch.get('update_items') or []) >= PREVIEW:
        return get(f"verification-batch/{batch['id']}/")
    return batch


def find_batch(service_id: str, version: str) -> dict | None:
    """GET verification-batch/, page by page: the batch of this Service and version, a waiting one first."""
    found = None
    for batch in pages('verification-batch/', ordering='-created_at'):
        if holds(complete(batch, service_id), service_id, version):
            if batch.get('state') == WAITING:
                return batch
            found = found or batch
    return found


def semver(version) -> tuple | None:
    """A sort key that follows the SemVer rules (4.10.0 is above 4.9.0, 1.0.0-rc1 below 1.0.0); None if not SemVer."""
    match = SEMVER.fullmatch(version) if isinstance(version, str) else None
    if not match:
        return None
    release = tuple(int(part) for part in match.groups()[:3])
    if not match[4]:
        return (*release, 1, ())
    return (*release, 0, tuple((0, int(p), '') if p.isdigit() else (1, 0, p) for p in match[4].split('.')))


def units_of_batch(batch_id: str, service_id: str, given: list[str]) -> tuple[list[str], str | None]:
    """The test Units of a batch, and what stops them from covering it (None when they do).

    GET verification-batch/{id}/statistics/ gives the Units of the Verification Set; it does not look at the Service.
    GET services/{id}/units/ gives the Units linked to the Service through a Subject, in the Set or not. A test Unit
    has to be in both. Units you give replace the second list, but they must be in the Set, and every architecture of
    the batch needs a test Unit: decide approves all of them.
    Never use installed.count from the statistics: on 6.1 it stays 0 after an install (EPMPAOS-7657).
    """
    statistics = get(f'verification-batch/{batch_id}/statistics/')
    wanted = set(given) if given else {unit.get('id') for unit in pages(f'services/{service_id}/units/')}
    units, bare, in_any_set = [], [], []
    for sub_batch in statistics.get('sub_batches') or []:
        in_set = checked_ids((sub_batch.get('verification_set') or {}).get('unit_ids') or [])
        hide(*in_set)
        in_any_set += in_set
        mine = [unit for unit in in_set if unit in wanted]
        for unit in in_set:
            if unit not in wanted and not given:
                print(f'Unit {short(unit)} is in the Verification Set, but the Service is not linked to it: left out',
                      file=sys.stderr)
        if not mine:
            bare.append(sub_batch.get('architecture') or '?')
        units += [unit for unit in mine if unit not in units]
    outside = [unit for unit in given if unit not in in_any_set]
    if outside:
        return units, f"Unit {', '.join(short(unit) for unit in outside)} is not in the Verification Set"
    if bare or not units:
        return units, (f"batch {short(batch_id)}: no test Unit for {', '.join(bare) or 'any architecture'}: the batch "
                       f'cannot be verified. Check the Verification Set and the Subject of the Service '
                       f'(README, prerequisite 2)')
    return units, None


def still_open(batch: dict) -> bool:
    """Waiting, and not locked: on AosCloud 6.2 a Campaign locks its batch, and a decision would end in HTTP 405."""
    return batch.get('state') == WAITING and not batch.get('is_locked')


def is_online(unit_id: str) -> bool:
    """GET units/{id}/: online_status. AosCloud keeps the last state of a Unit that went offline, even 'active'."""
    return str(get(f'units/{unit_id}/').get('online_status')).lower() == 'online'


def state_on_unit(unit_id: str, service_id: str, version: str) -> str:
    """'offline' (what AosCloud shows for the Unit is old), or from GET units/{id}/subjects-services/{service_id}/:
    'installed' (an instance of it is active), 'failed' or 'waiting'."""
    if not is_online(unit_id):
        return 'offline'
    for entry in items(get(f'units/{unit_id}/subjects-services/{service_id}/')):
        versions = entry.get('service_versions') or {}
        installed = (versions.get('installed_service_version') or {}).get('version')
        pending = (versions.get('pending_service_version') or {}).get('version')
        instances = [i for i in entry.get('instances') or [] if i.get('version') == version]
        if any(i.get('run_state') == 'failed' for i in instances):
            return 'failed'
        if pending == version and versions.get('pending_service_version_status') in FAILED_STATUSES:
            return 'failed'
        if installed == version and any(i.get('run_state') == 'active' for i in instances):
            return 'installed'
    return 'waiting'


def on_unit(unit_id: str, service_id: str) -> tuple[str | None, list]:
    """The installed version of the Service on a Unit, and its instances."""
    installed, instances = None, []
    for entry in items(get(f'units/{unit_id}/subjects-services/{service_id}/')):
        versions = entry.get('service_versions') or {}
        installed = installed or (versions.get('installed_service_version') or {}).get('version')
        instances += entry.get('instances') or []
    return installed, instances


def services(_args) -> int:
    for service in pages('services/'):
        print(f"{service.get('id')}\t{service.get('codename')}\t{service.get('title')}")
    return 0


def check(args) -> int:
    if not args.service_id:
        raise Stop('no Service id: pass --service-id or export SERVICE_ID')
    units = [unit for unit in args.unit if unit.strip()]
    page = get('verification-batch/', ordering='-created_at', limit=PAGE, offset=0)
    print('connection OK: the certificate, the root CA and the API address work')
    latest = next((b for b in items(page) if holds(b, args.service_id)), None)
    if not units:  # the test Units of the latest batch, or else every Unit that has the Service
        linked = [unit.get('id') for unit in pages(f'services/{args.service_id}/units/')]
        units = units_of_batch(latest['id'], args.service_id, [])[0] if latest else []
        units = units or linked
    if latest:
        version = next(i.get('version') for i in latest['update_items'] if i.get('identity_id') == args.service_id)
        print(f"Service {short(args.service_id)}: latest batch {short(latest.get('id'))}, version {version}, "
              f"state {latest.get('state')}")
    else:
        print(f'Service {short(args.service_id)}: no batch among the latest {PAGE}')
    for unit in units:
        installed, instances = on_unit(unit, args.service_id)
        states = ', '.join(f"{i.get('version')} {i.get('run_state')}" for i in instances) or 'no instance'
        online = 'online' if is_online(unit) else 'OFFLINE, what follows is old'
        print(f'Unit {short(unit)}: {online}; installed {installed or "nothing"}; instances: {states}')
    if not units:
        print('no Unit has this Service yet: check the Subject of the Service (README, prerequisite 2)')
    return 0


def status(args) -> int:
    """A1, B1 and C2, read again: what wait proved, as report lines."""
    units = [unit for unit in args.unit if unit.strip()]
    if not units:
        raise Stop('no test Units given (--unit)')
    batch = get(f'verification-batch/{args.batch_id}/')
    exact, state = holds(batch, args.service_id, args.version), batch.get('state')
    if not exact:
        a1 = f'batch {short(args.batch_id)} does not hold version {args.version} of Service {short(args.service_id)}'
    elif state != WAITING:
        a1 = f'batch {short(args.batch_id)} is {state}, not {WAITING}'
    else:
        a1 = f'batch {short(args.batch_id)} holds version {args.version} of Service {short(args.service_id)}'
    rows = [('A1', 'exactly this Service and version', exact and state == WAITING, a1)]
    running, failed = [], []
    for unit in units:
        if not is_online(unit):  # neither a pass nor a fail: its state in AosCloud says nothing about this version
            raise Stop(f'Unit {short(unit)} is offline: what AosCloud shows for it is old, so it proves nothing')
        installed, instances = on_unit(unit, args.service_id)
        mine = [i for i in instances if i.get('version') == args.version]
        if installed == args.version and any(i.get('run_state') == 'active' for i in mine):
            running.append(unit)
        for i in mine:
            if i.get('run_state') == 'failed':
                failed.append(f"{short(unit)}: {i.get('error_message') or i.get('error_exit_code') or 'failed'}")
    rows.append(('B1', 'on every listed Unit', len(running) == len(units),
                 f'version {args.version} active on {len(running)} of {len(units)} Units'))
    rows.append(('C2', 'no crash on start', not failed,
                 'no failed instance' if not failed else 'failed instance on ' + '; '.join(failed)))
    for code, name, passed, detail in rows:
        print(f"{code}\t{name}\t{'PASS' if passed else 'FAIL'}\t{detail}")
    return 0 if all(r[2] for r in rows) else 1


def discover(args) -> int:
    """Step 0: is there a new version to verify? Reads only. Writes GITHUB_OUTPUT unless --dry-run."""
    if not args.service_id:
        raise Stop('no Service id: pass --service-id or export SERVICE_ID')
    given = checked_ids(args.unit)
    rows, unusable = [], []
    for batch in pages('verification-batch/', ordering='-created_at'):
        for item in complete(batch, args.service_id).get('update_items') or []:
            if item.get('identity_id') == args.service_id:
                key = semver(item.get('version'))
                if key:
                    rows.append((key, item['version'], batch))
                else:
                    unusable.append(item.get('version'))
    if unusable:
        print(f'left out, not SemVer versions: {unusable}', file=sys.stderr)
    if args.dry_run:
        for _, version, batch in rows[:10]:
            print(f"batch {short(batch.get('id'))}: version {version}, {batch.get('state')}", file=sys.stderr)
        if len(rows) > 10:
            print(f'... and {len(rows) - 10} older batches', file=sys.stderr)
    # Only the highest version of all counts, decided or not. AosCloud moves the Units on to a newer version and never
    # closes the older batch, so an older batch can wait forever while its version will never run again.
    top = max(rows, key=lambda row: row[0], default=None)  # the newest one, if two have the same precedence
    chosen = top if top and still_open(top[2]) else None
    for row in rows:
        if row[2].get('state') == WAITING and row is not chosen:
            print(f"stale, left alone: batch {short(row[2].get('id'))}, version {row[1]}", file=sys.stderr)
    if not chosen:
        state = top and ('locked' if top[2].get('is_locked') else top[2].get('state'))
        print('no waiting batch' + (f': the highest version {top[1]} is {state}' if top else ''), file=sys.stderr)
        return 0
    _, version, batch = chosen
    if not AN_ID.fullmatch(str(batch.get('id'))):
        raise Stop(f'an unexpected batch id: {batch.get("id")!r}')
    hide(batch['id'])
    units, problem = units_of_batch(batch['id'], args.service_id, given)
    if problem:
        print(problem, file=sys.stderr)
        if not args.dry_run:
            return 2
    print(json.dumps({'batch_id': batch['id'], 'version': version, 'units': units}))
    output = os.environ.get('GITHUB_OUTPUT')
    if output and not args.dry_run:
        with open(output, 'a', encoding='utf-8') as file:
            file.write(f"batch_id={batch['id']}\nversion={version}\nunits={' '.join(units)}\n")
    return 0


def checked_ids(values: list[str]) -> list[str]:
    """The non-empty values; an id goes into a URL, so anything else stops here."""
    ids = [value.strip() for value in values if value.strip()]
    for value in ids:
        if not AN_ID.fullmatch(value):
            raise Stop(f'not an id: {value!r}')
    return ids


def wait(args) -> int:
    units = checked_ids(args.unit)
    deadline = time.monotonic() + args.timeout
    batch, last, told, covered, unreachable, offline = None, None, False, False, False, []
    while time.monotonic() < deadline:
        try:
            batch = batch or find_batch(args.service_id, args.version)
            hide(batch and batch.get('id'))
            if batch and args.batch_id and batch['id'] != args.batch_id:
                print(f"version {args.version} is in batch {batch['id']}, not in {args.batch_id}: decide nothing",
                      file=sys.stderr)
                return 2
            if batch and not still_open(get(f"verification-batch/{batch['id']}/")):
                print(f"batch {batch['id']} is no longer {WAITING}: nothing to decide", file=sys.stderr)
                return 2
            unreachable = False
            if batch and not covered:  # every test Unit in the Set, every architecture covered, or no decision at all
                found, problem = units_of_batch(batch['id'], args.service_id, units)
                if problem:
                    if not told:
                        print(f'{problem}: waiting', file=sys.stderr)
                        told = True
                    time.sleep(args.interval)
                    continue
                units, covered = found, True
                print(f"test Units: {', '.join(short(unit) for unit in units)}", file=sys.stderr)
            if batch:
                if args.units_file:
                    Path(args.units_file).write_text(' '.join(units), encoding='utf-8')
                states = {unit: state_on_unit(unit, args.service_id, args.version) for unit in units}
                offline = [short(unit) for unit, state in states.items() if state == 'offline']
                if states != last:  # print only changes, so the log stays short
                    print(f'batch {batch["id"]}: {states}', file=sys.stderr)
                    if offline:
                        print(f"Unit {', '.join(offline)} is offline: what AosCloud shows for it is old, so it does "
                              f'not count; waiting until it is online', file=sys.stderr)
                    last = states
                if 'failed' in states.values():
                    print(batch['id'])
                    return 1
                if all(s == 'installed' for s in states.values()):
                    print(batch['id'])
                    return 0
            elif last is None:
                print(f'no batch yet for {args.version}, waiting', file=sys.stderr)
                last = {}
        except Unavailable as e:  # a short outage is no reason to stop waiting
            print(f'AosCloud not reachable, retrying: {e}', file=sys.stderr)
            unreachable = True
        time.sleep(args.interval)
    print(f'timed out after {args.timeout:.0f} s: decide nothing'
          + (f": Unit {', '.join(offline)} is offline" if offline else ''), file=sys.stderr)
    return 3 if unreachable or offline else 2


def decide(args) -> int:
    # AosCloud accepts a PATCH on a batch that is already decided, and the last one wins: an approve can become a reject.
    # This check, right before the one PATCH, and the concurrency of the job are the only guard against that.
    try:
        batch = get(f'verification-batch/{args.batch_id}/')
    except Unavailable as e:
        print(f'cannot read batch {args.batch_id}, nothing sent: {e}', file=sys.stderr)
        return 2
    if batch.get('state') != WAITING:
        print(f"refused: batch {args.batch_id} is {batch.get('state')}, not {WAITING}", file=sys.stderr)
        return 2
    architectures = batch.get('architectures') or []
    if not architectures:
        print(f'refused: batch {args.batch_id} lists no architectures, nothing sent', file=sys.stderr)
        return 2
    chosen = args.arch or architectures
    unknown = [arch for arch in chosen if arch not in architectures]
    if unknown:
        print(f'refused: batch {args.batch_id} has no architecture {", ".join(unknown)} '
              f'(it has {", ".join(architectures)}), nothing sent', file=sys.stderr)
        return 2
    print(f"batch {args.batch_id}: {'approve' if args.approve else 'reject'} {', '.join(chosen)}", file=sys.stderr)
    body = [{'architecture': arch, 'is_approved': args.approve} for arch in chosen]
    base, session = api()
    try:  # sent exactly once: a retry could apply a second decision
        answer = session.patch(f'{base}/verification-batch/{args.batch_id}/', json=body, timeout=60)
    except requests.RequestException as e:
        print(f'no answer, the decision may have been applied; check the batch in the UI: {e}', file=sys.stderr)
        return 3
    if answer.status_code >= 500:
        print(f'HTTP {answer.status_code}: the decision may have been applied; check the batch in the UI',
              file=sys.stderr)
        return 3
    if answer.status_code >= 400:
        print(f'refused by AosCloud: HTTP {answer.status_code} {answer.text[:200]}', file=sys.stderr)
        return 2
    try:
        result = answer.json()
    except ValueError:  # a success with an empty or non-JSON body is still a success
        result = None
    state = result.get('state') if isinstance(result, dict) else None
    print(f"batch {args.batch_id}: {'approved' if args.approve else 'rejected'}, {f'now {state}' if state else 'sent'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    commands = parser.add_subparsers(dest='command', required=True)
    f = commands.add_parser('discover', help='read only: the batch to verify, and its test Units')
    f.add_argument('--service-id', default=os.environ.get('SERVICE_ID'))
    f.add_argument('--unit', action='append', default=[], help='use these test Units, not the ones AosCloud lists')
    f.add_argument('--dry-run', action='store_true', help='also list the batches; start nothing, exit 0, write nothing')
    c = commands.add_parser('check', help='read only: the connection, the latest batch, what the Units run')
    c.add_argument('--service-id', default=os.environ.get('SERVICE_ID'))
    c.add_argument('--unit', action='append', default=[], help='a test Unit (repeat it); default: from AosCloud')
    commands.add_parser('services', help='read only: the Services this certificate sees')
    s = commands.add_parser('status', help='read only: the checks that wait proved, read again')
    s.add_argument('--service-id', required=True)
    s.add_argument('--version', required=True)
    s.add_argument('--batch-id', required=True)
    s.add_argument('--unit', action='append', default=[], help='a test Unit (repeat it)')
    w = commands.add_parser('wait', help='wait until the batch waits and the version is on every Unit')
    w.add_argument('--service-id', required=True)
    w.add_argument('--version', required=True)
    w.add_argument('--unit', action='append', default=[],
                   help='a test Unit (repeat it); default: the Units of the Verification Set that have the Service')
    w.add_argument('--batch-id', help='stop unless the version is in this batch')
    w.add_argument('--units-file', help='write the test Units to this file, separated by spaces')
    w.add_argument('--timeout', type=float, default=1800, help='seconds (default 1800)')
    w.add_argument('--interval', type=float, default=15, help='seconds between checks (default 15)')
    d = commands.add_parser('decide', help='approve or reject the batch, once')
    d.add_argument('--batch-id', required=True)
    choice = d.add_mutually_exclusive_group(required=True)
    choice.add_argument('--approve', action='store_true')
    choice.add_argument('--reject', action='store_true')
    d.add_argument('--arch', action='append', help='an architecture you tested (repeat it); default: all of the batch')
    args = parser.parse_args(argv)
    hide(getattr(args, 'service_id', None), getattr(args, 'batch_id', None), *getattr(args, 'unit', []))
    outcome = {'discover': 'start nothing', 'wait': 'decide nothing',
               'decide': 'nothing sent'}.get(args.command, 'nothing was changed')
    try:
        return {'discover': discover, 'services': services, 'check': check, 'status': status, 'wait': wait,
                'decide': decide}[args.command](args)
    except (Stop, Unavailable) as e:
        print(f'stopped, {outcome}: {e}', file=sys.stderr)
    except Exception as e:  # pylint: disable=broad-except  # never end with a code the job does not expect
        print(f'unexpected error, {outcome}: {type(e).__name__}: {e}', file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.exit(main())
