#!/usr/bin/env python3
# Copyright (c) 2018-2026 EPAM Systems Inc.
# SPDX-License-Identifier: Apache-2.0
"""Write report.html, report.md and junit.xml from the results that tests/run_tests.sh collected.

    python tests/report.py --results report/results.tsv --out report --service-id ID --version X.Y.Z
                           --batch-id ID --units "UNIT_ID ..." --observe SECONDS
                           [--found-by watch|run|version] [--units-from TEXT] [--decision-mode MODE] [--service-name NAME]
    python tests/report.py --out report --batch-id ID --decision approved|rejected|not-sent|report-only|unknown
                           [--state STATE] [--why TEXT]

results.tsv: one line per check: builtin|yours, scenario code, name, PASS|FAIL|SKIP|ERROR, detail. Standard library only.
Ids are shortened to ... plus their last 4 characters. The second form writes the decision into report.html; it leaves a
report of another batch alone. Exit 0 when written, or when there is nothing to update.
"""
import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from xml.sax.saxutils import quoteattr

GROUPS = [('A', 'upload and Verification batch'), ('B', 'delivery to the Verification Set Units'), ('C', 'the Service runs'),
          ('D', 'next to other Services'), ('E', 'configuration and recovery')]
SCENARIOS = [
    ('4', 'Found automatically (discover)'),
    ('A1', 'Exactly this Service and Service version'), ('A2', 'Upload processed'), ('A3', 'Same Service version uploaded again'),
    ('A4', 'Evidence for every release'), ('B1', 'On every Verification Set Unit'), ('B2', 'A Unit that never gets it'),
    ('C1', "The Service's own tests"), ('C2', 'Crash on start'), ('C3', 'Late failure'), ('C4', 'Restarts'),
    ('D1', 'Resource conflict'), ('D2', 'Other Services on the same Unit'),
    ('E1', 'Bad configuration'), ('E2', 'Recovery: the fixed Service version'),
]
CHECKED, WAITING_LOGIC, NOT_RUN, NEEDS_62, NEXT = ('Checked now', 'Covered by the waiting logic', 'Not run',
                                                   'Needs AosCloud 6.2', 'Next Service version')
STATUS_CLASS = {CHECKED: 'pass', WAITING_LOGIC: 'skip', NOT_RUN: 'amber', NEEDS_62: 'skip', NEXT: 'skip'}
OBSERVATION_OFF = '60-second observation was off, AOS_WATCH_SECONDS=0'
# What you can change for each scenario; a scenario that is not here has nothing to change.
CHANGE_IT = {'B1': 'AOS_WAIT_TIMEOUT', 'B2': 'AOS_WAIT_TIMEOUT', 'C1': 'your_tests.sh', 'C3': 'AOS_WATCH_SECONDS',
             'C4': 'AOS_WATCH_SECONDS, your_tests.sh', 'E1': 'your_tests.sh'}
NO_TESTS_YET = 'no tests/your_tests.sh (start from tests/your_tests.sh.example)'
BY_HAND = 'To decide by hand: ./aos-verify decide approve|reject, or SP UI → Verification batches.'
STATE_NAMES = {'Valid': 'Verified', 'Waiting_validation': 'Waiting verification'}
META = 'report.json'


def short(value: str) -> str:
    return f'…{value[-4:]}' if value else '-'


def read_results(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip():
            part, code, name, result, detail = (line.split('\t') + [''] * 5)[:5]
            rows.append({'part': part, 'code': code, 'name': name, 'result': result, 'detail': detail})
    return rows


def stamp(now: datetime) -> str:
    """UTC, and the local time in brackets when it is another time. The date only if it differs."""
    utc, local = now.astimezone(timezone.utc), now.astimezone()
    text = utc.strftime('%Y-%m-%d %H:%M UTC')
    if local.utcoffset() == utc.utcoffset():
        return text
    shown = '%H:%M' if local.date() == utc.date() else '%Y-%m-%d %H:%M'
    return f'{text} ({local.strftime(shown)} local)'


def found_how(found_by: str) -> tuple[str, str]:
    """Who found the batch, from where this ran: on a laptop it was ./aos-verify, in GitHub the watch."""
    if found_by == 'watch':
        return CHECKED, 'found by the watch in GitHub'
    if found_by == 'run':
        return CHECKED, 'found by ./aos-verify on this computer'
    return NOT_RUN, 'not needed: started with the Service version'


def coverage(rows: list[dict], observe: int, found: tuple[str, str]) -> dict[str, tuple[str, str]]:
    """For each scenario: its status, and what that means in this run."""
    builtin = {r['code']: r['result'] for r in rows if r['part'] == 'builtin'}
    yours = [r for r in rows if r['part'] == 'yours' and r['result'] != 'SKIP']
    late = builtin.get('C3/C4', 'FAIL')
    observed = observe > 0 and late != 'SKIP'
    # The check says why it did not run: it was switched off, or the version had already failed.
    off = (NOT_RUN, next((r['detail'] for r in rows if r['code'] == 'C3/C4' and r['result'] == 'SKIP' and r['detail']),
                         OBSERVATION_OFF))
    no_tests = (NOT_RUN, 'none added: add them to tests/your_tests.sh')
    return {
        '4': found,
        'A1': (CHECKED, builtin.get('A1', 'FAIL')),
        'A2': (CHECKED, 'The Verification batch exists, so AosCloud processed the upload.'),
        'A3': (WAITING_LOGIC, 'Did not happen in this run. If it does: a Service version that already exists gets no '
                              'new Verification batch, and nothing is decided.'),
        'A4': (CHECKED, 'This report, junit.xml and the job log; the Verification batch keeps its state in AosCloud.'),
        'B1': (CHECKED, builtin.get('B1', 'FAIL')),
        'B2': (WAITING_LOGIC, 'Did not happen in this run. If it does: a Unit that never gets the Service version ends in a '
                              'timeout, with no decision.'),
        'C1': (CHECKED, f'{len(yours)} partner tests ran') if yours else no_tests,
        'C2': (CHECKED, builtin.get('C2', 'FAIL')),
        'C3': (CHECKED, f'{late}: still running after {observe} s') if observed else off,
        'C4': (CHECKED, f'{late}: no restarts in {observe} s; memory is not measured') if observed else off,
        'D1': (NEEDS_62, 'AosCloud 6.1 cannot show this.'),
        'D2': (NEEDS_62, 'AosCloud 6.1 cannot show this.'),
        'E1': (CHECKED, f'only if the partner tests hold a config test; {len(yours)} partner tests ran') if yours else no_tests,
        'E2': (NEXT, 'Did not happen in this run. The next Service version goes through the same steps.'),
    }


def summary(rows: list[dict]) -> str:
    """3 passed, 1 skipped (C3/C4), 0 failed; partner tests: none added (tests/your_tests.sh)."""
    builtin = [r for r in rows if r['part'] == 'builtin']
    yours = [r for r in rows if r['part'] == 'yours' and r['result'] != 'SKIP']
    count = lambda result: [r['code'] for r in builtin if r['result'] == result]  # pylint: disable=unnecessary-lambda-assignment
    skipped, unread = count('SKIP'), count('ERROR')
    text = f"{len(count('PASS'))} passed, {len(skipped)} skipped"
    text += f" ({', '.join(skipped)})" if skipped else ''
    text += f", {len(count('FAIL'))} failed"
    text += f", {len(unread)} could not be read ({', '.join(unread)})" if unread else ''
    if not yours:
        return text + '; partner tests: none added (tests/your_tests.sh)'
    return text + f"; partner tests: {sum(r['result'] == 'PASS' for r in yours)} of {len(yours)} passed"


def decision_line(decided: dict | None) -> str:
    """What was done with the verdict."""
    if not decided:
        return f'Decision: not sent yet. {BY_HAND}'
    kind, when, why = decided['decision'], decided['when'], decided.get('why')
    if kind in ('approved', 'rejected'):
        state = decided.get('state')
        return f"Decision: {kind}, {when}, " + (f'Verification batch now {STATE_NAMES.get(state, state)}' if state else 'sent')
    if kind == 'report-only':
        return 'Decision: report-only, nothing sent. A person decides in SP UI → Verification batches.'
    if kind == 'unknown':
        return (f'Decision: unknown, {when}: it was sent and the answer was lost. Look at the Verification batch in SP UI → '
                f'Verification batches, and do not send it again.')
    return f"Decision: not sent{f' ({why})' if why else ''}. {BY_HAND}"


def badge(result: str) -> str:
    return f'<span class="r {result.lower()}">{html.escape(result)}</span>'


def table(rows: list[dict], with_code: bool) -> str:
    head = ('<th>#</th>' if with_code else '') + '<th>Check</th><th>Result</th><th>Detail</th>'
    body = ''.join('<tr>' + (f'<td>{html.escape(r["code"])}</td>' if with_code else '')
                   + f'<td>{html.escape(r["name"])}</td><td>{badge(r["result"])}</td>'
                   f'<td>{html.escape(r["detail"])}</td></tr>' for r in rows)
    return f'<div class="t"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def your_tests_section(yours: list[dict]) -> str:
    """The table of your tests, or, when you have none yet, what to do about it."""
    if all(r['result'] == 'SKIP' and r['detail'] == NO_TESTS_YET for r in yours):
        return ('<p class="todo"><b>No partner tests yet: add tests/your_tests.sh</b> (start from '
                'tests/your_tests.sh.example): they run after the built-in checks. If any check or test fails, the verdict '
                'is reject; in auto mode the job sends it, in report-only a person decides.</p>')
    return table(yours, False)


def verdict(rows: list[dict], decided: dict | None = None) -> tuple[str, str]:
    """The big line of the report, and its colour: the verdict of the checks, then what was done with it."""
    failed = any(r['result'] == 'FAIL' for r in rows)
    unread = any(r['result'] == 'ERROR' for r in rows)
    if unread and not failed:
        return 'AosCloud unreadable or a Unit offline → no decision', 'none'
    ok = not failed and any(r['result'] == 'PASS' for r in rows)
    checks, tone = ('PASS → approve', 'pass') if ok else ('FAIL → reject', 'fail')
    kind, state = (decided or {}).get('decision'), (decided or {}).get('state')
    now = f', now {STATE_NAMES.get(state, state)}' if state else ''
    if kind == 'approved':
        return f"{checks.split(' ')[0]} → approved{now}", tone
    if kind == 'rejected':
        return f"{checks.split(' ')[0]} → rejected{now}", tone
    if kind == 'report-only':
        return f'{checks}; report-only: not sent', tone
    if kind == 'unknown':
        return f'{checks}; sent, the answer was lost', tone
    if kind == 'not-sent':
        return f'{checks}; not sent', tone
    return checks, tone


def facts_of(args, when: str) -> list[tuple[str, str]]:
    units = ', '.join(short(u) for u in args.units.split()) or '-'
    name = getattr(args, 'service_name', '')
    service = f'{name} ({short(args.service_id)})' if name else short(args.service_id)
    facts = [('Service', service), ('Service version', args.version), ('Verification batch', short(args.batch_id))]
    if getattr(args, 'decision_mode', ''):
        facts.append(('Decision mode', args.decision_mode))
    return facts + [('Verification Set Units', units), ('Report time', when)]


def cover_of(args, rows: list[dict]) -> dict[str, tuple[str, str]]:
    status, how = found_how(args.found_by)
    found = (status, f'{how}: Verification batch {short(args.batch_id)}, Service version {args.version}, Units from {args.units_from}')
    return coverage(rows, args.observe, found)


def md(text: str, cell: bool = True) -> str:
    """One line of Markdown, or a table cell (its pipes escaped): no HTML, no line break."""
    line = html.escape(' '.join(str(text).split()), quote=False)
    return line.replace('|', '\\|') if cell else line


ARTIFACTS = 'Full report.html and junit.xml: Artifacts at the bottom of this page'


def render_md(args, rows: list[dict], when: str, decided: dict | None = None) -> str:
    """The same report as report.html, in Markdown: the job summary of verify-batch."""
    big, _ = verdict(rows, decided)
    mark = {'PASS': '✅ ', 'FAIL': '❌ '}.get(big.split(' ', 1)[0], '')
    lines = ['### Verification report', '', f'# {mark}{md(big, False)}', '', f'**{md(summary(rows), False)}**', '',
             md(decision_line(decided), False), '', '| What | Value |', '|---|---|']
    lines += [f'| {k} | {md(v)} |' for k, v in facts_of(args, when)]
    table_md = lambda part, with_code: ([('| # ' if with_code else '') + '| Check | Result | Detail |',  # pylint: disable=unnecessary-lambda-assignment
                                          ('|---' if with_code else '') + '|---|---|---|']
                                         + [(f"| {md(r['code'])} " if with_code else '') + f"| {md(r['name'])} | **{md(r['result'])}** | {md(r['detail'])} |"
                                            for r in rows if r['part'] == part])
    lines += ['', '### Built-in checks', '', ' · '.join(f'**{letter}** = {name}' for letter, name in GROUPS), '']
    lines += table_md('builtin', True)
    lines += ['', 'PASS = checked, good · FAIL = checked, bad, leads to reject · SKIP = not run'
              + (' · ERROR = AosCloud could not be read: no decision' if any(r['result'] == 'ERROR' for r in rows) else '')]
    lines += ['', '### Partner tests', '']
    yours = [r for r in rows if r['part'] == 'yours']
    if all(r['result'] == 'SKIP' and r['detail'] == NO_TESTS_YET for r in yours):
        lines += ['> **No partner tests yet: add tests/your_tests.sh** (start from tests/your_tests.sh.example): they run after '
                  'the built-in checks. If any check or test fails, the verdict is reject; in auto mode the job sends it, in '
                  'report-only a person decides.']
    else:
        lines += table_md('yours', False)
    cover = cover_of(args, rows)
    lines += ['', '### Scenarios covered', '', '| # | Scenario | Status | In this run | You can change it |', '|---|---|---|---|---|']
    lines += [f'| {code} | {md(name)} | {md(cover[code][0])} | {md(cover[code][1])} | {md(CHANGE_IT.get(code, "—"))} |'
              for code, name in SCENARIOS]
    return '\n'.join(lines + ['', ARTIFACTS, ''])


def render(args, rows: list[dict], when: str, decided: dict | None = None) -> str:
    big, tone = verdict(rows, decided)
    builtin = [r for r in rows if r['part'] == 'builtin']
    yours = [r for r in rows if r['part'] == 'yours']
    cover = cover_of(args, rows)
    scenario_rows = ''.join(f'<tr><td>{code}</td><td>{html.escape(name)}</td>'
                            f'<td><span class="s {STATUS_CLASS[cover[code][0]]}">{html.escape(cover[code][0])}</span></td>'
                            f'<td>{html.escape(cover[code][1])}</td><td>{html.escape(CHANGE_IT.get(code, "—"))}</td></tr>'
                            for code, name in SCENARIOS)
    groups = ' · '.join(f'<b>{letter}</b> = {html.escape(name)}' for letter, name in GROUPS)
    badges = [('PASS', 'checked, good'), ('FAIL', 'checked, bad, leads to reject'), ('SKIP', 'not run')]
    if any(r['result'] == 'ERROR' for r in rows):
        badges.append(('ERROR', 'AosCloud could not be read: no decision'))
    legend = ' · '.join(f'{badge(name)} = {html.escape(text)}' for name, text in badges)
    facts = facts_of(args, when)
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verification report: Service version {html.escape(args.version)}</title>
<style>
:root{{--navy:#173D7A;--sky:#4AA8FF;--pale:#D2EDFF;--ink:#14243F;--muted:#4F5F75;--line:#C9DDF0;--bg:#F6FAFE}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 Roboto,-apple-system,"Segoe UI","Helvetica Neue",Arial,sans-serif}}
header{{background:var(--navy);color:#fff;padding:14px 16px}}
header b{{color:var(--sky)}}
main{{max-width:980px;margin:0 auto;padding:16px}}
h1{{font-size:1.6em;color:var(--navy);margin:.2em 0 .6em}}
h2{{font-size:1.2em;color:var(--navy);margin:1.4em 0 .5em}}
.verdict{{border-radius:12px;padding:16px 20px;font-size:1.7em;font-weight:700;color:#fff}}
.verdict.pass{{background:#1E7F4F}}.verdict.fail{{background:#B42318}}.verdict.none{{background:#4F5F75}}
.facts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:14px 0}}
.facts div{{background:#fff;border:1px solid var(--line);border-radius:8px;padding:8px 12px}}
.facts span{{display:block;color:var(--muted);font-size:12px}}
.t{{overflow-x:auto}}
table{{border-collapse:collapse;width:100%;background:#fff}}
th,td{{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}}
th{{background:var(--pale);color:var(--navy)}}
.r{{display:inline-block;min-width:48px;text-align:center;border-radius:5px;padding:1px 6px;font-weight:700;font-size:13px}}
.r.pass{{background:#DDF3E6;color:#14603A}}.r.fail{{background:#FBE1DE;color:#8F1D13}}.r.skip{{background:#ECEFF3;color:var(--muted)}}.r.error{{background:#ECEFF3;color:var(--ink)}}
.s{{display:inline-block;border-radius:5px;padding:1px 6px;font-weight:600;font-size:13px}}
.s.pass{{background:#DDF3E6;color:#14603A}}.s.skip{{background:#ECEFF3;color:var(--muted)}}.s.amber{{background:#FDF0D5;color:#7A4B00}}
.summary{{margin:10px 0 2px;font-weight:700}}
.decision{{margin:2px 0 12px}}
.legend{{color:var(--muted);font-size:13px;margin:6px 0}}
.todo{{background:#FDF0D5;color:#7A4B00;border-radius:8px;padding:8px 12px;margin:6px 0}}
</style>
</head>
<body>
<header><b>Aos</b>Edge · Verify a new Service version</header>
<main>
<h1>Verification report</h1>
<div class="verdict {tone}">{html.escape(big)}</div>
<p class="summary">{html.escape(summary(rows))}</p>
<p class="decision">{html.escape(decision_line(decided))}</p>
<div class="facts">{''.join(f'<div><span>{k}</span>{html.escape(v)}</div>' for k, v in facts)}</div>
<h2>Built-in checks</h2>
<p class="legend">{groups}</p>
{table(builtin, True)}
<p class="legend">{legend}</p>
<h2>Partner tests</h2>
{your_tests_section(yours)}
<h2>Scenarios covered</h2>
<div class="t"><table><thead><tr><th>#</th><th>Scenario</th><th>Status</th><th>In this run</th><th>You can change it</th></tr></thead>
<tbody>{scenario_rows}</tbody></table></div>
</main>
</body>
</html>
'''


def junit(args, rows: list[dict], when: str) -> str:
    cases = []
    for r in rows:
        name = quoteattr(f"{r['code']} {r['name']}".strip())
        inner = ''
        if r['result'] == 'FAIL':
            inner = f'<failure message={quoteattr(r["detail"])}/>'
        elif r['result'] == 'ERROR':
            inner = f'<error message={quoteattr(r["detail"])}/>'
        elif r['result'] == 'SKIP':
            inner = f'<skipped message={quoteattr(r["detail"])}/>'
        kind = 'built-in checks' if r['part'] == 'builtin' else 'partner tests'
        cases.append(f'  <testcase classname="{kind}" name={name}>{inner}</testcase>')
    failures = sum(r['result'] == 'FAIL' for r in rows)
    skipped = sum(r['result'] == 'SKIP' for r in rows)
    errors = sum(r['result'] == 'ERROR' for r in rows)
    suite = quoteattr(f'verification of Service version {args.version}')
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name={suite} tests="{len(rows)}" '
            f'failures="{failures}" errors="{errors}" skipped="{skipped}" timestamp="{when}">\n' + '\n'.join(cases) + '\n</testsuite>\n')


def write_decision(args) -> int:
    """Render the report again with the decision in it. The checks and their time stay as they were."""
    meta_file = args.out / META
    try:
        meta = json.loads(meta_file.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        print('no report to update', file=sys.stderr)
        return 0
    if meta['batch_id'] != short(args.batch_id or ''):
        print(f"the report is of Verification batch {meta['batch_id']}, not {short(args.batch_id or '')}: left alone", file=sys.stderr)
        return 0
    decided = {'decision': args.decision, 'state': args.state, 'why': args.why,
               'when': stamp(datetime.now(timezone.utc))}
    report = SimpleNamespace(**{k: meta[k] for k in ('service_id', 'version', 'batch_id', 'units', 'observe', 'found_by',
                                                     'units_from')}, decision_mode=meta.get('decision_mode', ''),
                             service_name=meta.get('service_name', ''))
    (args.out / 'report.html').write_text(render(report, meta['rows'], meta['when'], decided), encoding='utf-8')
    (args.out / 'report.md').write_text(render_md(report, meta['rows'], meta['when'], decided), encoding='utf-8')
    print(f'report: {args.out / "report.html"} (with the decision)')
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('--results', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--service-id', default='')
    parser.add_argument('--version', default='')
    parser.add_argument('--batch-id', default='')
    parser.add_argument('--units', default='')
    parser.add_argument('--observe', type=int, default=0)
    parser.add_argument('--found-by', default='version')
    parser.add_argument('--units-from', default='the values you gave')
    parser.add_argument('--decision-mode', default='')
    parser.add_argument('--service-name', default='')
    parser.add_argument('--decision', choices=['approved', 'rejected', 'not-sent', 'report-only', 'unknown'])
    parser.add_argument('--state')
    parser.add_argument('--why')
    args = parser.parse_args(argv)
    if args.decision:
        return write_decision(args)
    if not args.results or not args.version or not args.batch_id or not args.service_id:
        parser.error('--results, --service-id, --version and --batch-id are needed to write a report')
    rows = read_results(args.results)
    now = datetime.now(timezone.utc)
    when = stamp(now)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'report.html').write_text(render(args, rows, when), encoding='utf-8')
    (args.out / 'report.md').write_text(render_md(args, rows, when), encoding='utf-8')
    (args.out / 'junit.xml').write_text(junit(args, rows, now.strftime('%Y-%m-%d %H:%M')), encoding='utf-8')
    # What the decision needs to render this report again, with the ids shortened: the folder is kept as an artifact.
    meta = {'service_id': short(args.service_id), 'version': args.version, 'batch_id': short(args.batch_id),
            'units': ' '.join(short(u) for u in args.units.split()), 'observe': args.observe, 'found_by': args.found_by,
            'units_from': args.units_from, 'decision_mode': args.decision_mode, 'service_name': args.service_name, 'when': when, 'rows': rows}
    (args.out / META).write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    print(f'report: {args.out / "report.html"} (and junit.xml)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
