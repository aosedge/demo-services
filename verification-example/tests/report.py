#!/usr/bin/env python3
# Copyright (c) 2018-2026 EPAM Systems Inc.
# SPDX-License-Identifier: Apache-2.0
"""Write report.html and junit.xml from the results that tests/run_tests.sh collected.

    python tests/report.py --results report/results.tsv --out report --service-id ID --version X.Y.Z
                           --batch-id ID --units "UNIT_ID ..." --watch SECONDS
                           [--found-by watch|version] [--units-from TEXT]

results.tsv: one line per check: builtin|yours, scenario code, name, PASS|FAIL|SKIP|ERROR, detail. Standard library only.
Ids are shortened to ... plus their last 4 characters. Exit 0 when written.
"""
import argparse
import html
import sys
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import quoteattr

SCENARIOS = [
    ('⓪', 'Found by the watch'),
    ('A1', 'Exactly this Service and version'), ('A2', 'Upload processed'), ('A3', 'Same version uploaded again'),
    ('A4', 'Evidence for every release'), ('B1', 'On every listed Unit'), ('B2', 'A Unit that never gets it'),
    ('C1', "The Service's own tests"), ('C2', 'Crash on start'), ('C3', 'Late failure'),
    ('C4', 'Restarts and memory'), ('E1', 'Bad configuration'), ('E2', 'Recovery: the fixed version'),
    ('D1', 'Resource conflict'), ('D2', 'Other Services on the same Unit'),
]


def short(value: str) -> str:
    return f'…{value[-4:]}' if value else '-'


def read_results(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip():
            part, code, name, result, detail = (line.split('\t') + [''] * 5)[:5]
            rows.append({'part': part, 'code': code, 'name': name, 'result': result, 'detail': detail})
    return rows


def coverage(rows: list[dict], watch: int, found: str) -> dict[str, str]:
    """How each scenario was covered by this run."""
    builtin = {r['code']: r['result'] for r in rows if r['part'] == 'builtin'}
    yours = [r for r in rows if r['part'] == 'yours' and r['result'] != 'SKIP']
    your_tests = f'your tests: {len(yours)} ran' if yours else 'not run: add tests/your_tests.sh'
    watched = f"checked now: {builtin.get('C3/C4', 'FAIL')}, watched {watch} s" if watch > 0 else 'off (WATCH_SECONDS=0)'
    return {
        '⓪': found,
        'A1': f"checked now: {builtin.get('A1', 'FAIL')}",
        'A2': 'by wait: the batch exists, so AosCloud processed the upload',
        'A3': 'by wait: a version that already exists gets no new batch, and nothing is decided',
        'A4': 'this report, junit.xml and the job log; the batch keeps its state in AosCloud',
        'B1': f"checked now: {builtin.get('B1', 'FAIL')}",
        'B2': 'by wait: a Unit that never gets the version ends in a timeout, with no decision',
        'C1': your_tests,
        'C2': f"checked now: {builtin.get('C2', 'FAIL')}",
        'C3': watched,
        'C4': watched,
        'E1': your_tests + ' (a config test)' if yours else your_tests,
        'E2': 'the next version goes through the same steps',
        'D1': 'needs AosCloud 6.2',
        'D2': 'needs AosCloud 6.2',
    }


def badge(result: str) -> str:
    return f'<span class="r {result.lower()}">{html.escape(result)}</span>'


def table(rows: list[dict], with_code: bool) -> str:
    head = ('<th>#</th>' if with_code else '') + '<th>Check</th><th>Result</th><th>Detail</th>'
    body = ''.join('<tr>' + (f'<td>{html.escape(r["code"])}</td>' if with_code else '')
                   + f'<td>{html.escape(r["name"])}</td><td>{badge(r["result"])}</td>'
                   f'<td>{html.escape(r["detail"])}</td></tr>' for r in rows)
    return f'<div class="t"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def render(args, rows: list[dict], when: str) -> str:
    failed = any(r['result'] == 'FAIL' for r in rows)
    unread = any(r['result'] == 'ERROR' for r in rows)
    if failed or (not unread and not any(r['result'] == 'PASS' for r in rows)):
        verdict, tone = 'FAIL → reject', 'fail'
    elif unread:
        verdict, tone = 'AosCloud could not be read → no decision', 'none'
    else:
        verdict, tone = 'PASS → approve', 'pass'
    builtin = [r for r in rows if r['part'] == 'builtin']
    yours = [r for r in rows if r['part'] == 'yours']
    how = 'found by the watch' if args.found_by == 'watch' else 'started with the version: not by the watch'
    found = f'{how}: batch {short(args.batch_id)}, version {args.version}, Units from {args.units_from}'
    cover = coverage(rows, args.watch, found)
    scenario_rows = ''.join(f'<tr><td>{code}</td><td>{html.escape(name)}</td><td>{html.escape(cover[code])}</td></tr>'
                            for code, name in SCENARIOS)
    units = ', '.join(short(u) for u in args.units.split()) or '-'
    facts = [('Service', short(args.service_id)), ('Version', args.version), ('Batch', short(args.batch_id)),
             ('Test Units', units), ('Time', when)]
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Verification report: {html.escape(args.version)}</title>
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
footer{{color:var(--muted);font-size:12px;padding:12px 0 24px}}
</style>
</head>
<body>
<header><b>Aos</b>Edge · Verify a new Service version</header>
<main>
<h1>Verification report</h1>
<div class="verdict {tone}">{verdict}</div>
<div class="facts">{''.join(f'<div><span>{k}</span>{html.escape(v)}</div>' for k, v in facts)}</div>
<h2>Built-in checks</h2>
{table(builtin, True)}
<h2>Your tests</h2>
{table(yours, False)}
<h2>Scenarios covered</h2>
<div class="t"><table><thead><tr><th>#</th><th>Scenario</th><th>How, in this run</th></tr></thead>
<tbody>{scenario_rows}</tbody></table></div>
<footer>The decision itself is the next step: verify_batch.py decide.</footer>
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
        kind = 'built-in checks' if r['part'] == 'builtin' else 'your tests'
        cases.append(f'  <testcase classname="{kind}" name={name}>{inner}</testcase>')
    failures = sum(r['result'] == 'FAIL' for r in rows)
    skipped = sum(r['result'] == 'SKIP' for r in rows)
    errors = sum(r['result'] == 'ERROR' for r in rows)
    suite = quoteattr(f'verification {args.version}')
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name={suite} tests="{len(rows)}" '
            f'failures="{failures}" errors="{errors}" skipped="{skipped}" timestamp="{when}">\n' + '\n'.join(cases) + '\n</testsuite>\n')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('--results', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--service-id', required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--batch-id', required=True)
    parser.add_argument('--units', default='')
    parser.add_argument('--watch', type=int, default=0)
    parser.add_argument('--found-by', default='version')
    parser.add_argument('--units-from', default='the values you gave')
    args = parser.parse_args(argv)
    rows = read_results(args.results)
    when = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / 'report.html').write_text(render(args, rows, when), encoding='utf-8')
    (args.out / 'junit.xml').write_text(junit(args, rows, when.replace(' UTC', '')), encoding='utf-8')
    print(f'report: {args.out / "report.html"} (and junit.xml)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
