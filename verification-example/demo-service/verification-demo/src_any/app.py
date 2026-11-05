# Copyright (c) 2018-2026 EPAM Systems Inc.
"""Demo Service for the verification flow.

``DEMO_VERSION``, ``DEMO_MODE``, ``DEMO_FAIL_AFTER_S`` and ``DEMO_GREEDY_MB`` are baked in by ``demo-release.yml``
(the ``__DEMO_*__`` tokens below). Environment variables of the same names override the baked values, which keeps
the script runnable locally.

At start the Service runs its named tests and prints one line per test and one summary line::

    AOS-TEST <test_name> PASS|FAIL|SKIP [reason=<text>] version=<version>
    AOS-TESTS version=<version> passed=<n> failed=<n> skipped=<n> total=<n>

The tests: ``config_loaded`` (the configuration is usable), ``storage_rw`` (a file can be written and read back),
``network_reachable`` (the allowed connections, ``DEMO_ALLOWED_HOSTS``; SKIP when none are declared),
``heartbeat_started`` (the heartbeat loop is ready) and ``quota_within_limits`` (the memory the Service holds fits
``DEMO_RAM_LIMIT_MB`` when given). The old ``SELFTEST OK|FAIL <version>`` line is still printed first for one
release, so that older runs stay comparable; the verification kit no longer depends on it.

good:       every test passes, then a heartbeat line every ``DEMO_HEARTBEAT_INTERVAL`` seconds.
broken:     ``storage_rw`` fails (``reason=demo-broken``), the summary has ``failed=1`` and the Service exits with
            code 1, so it crashes and is restarted by the Unit.
late-fail:  healthy at first; after ``DEMO_FAIL_AFTER_S`` seconds it prints ``AOS-TEST runtime_health FAIL
            reason=late`` and exits with code 1. Only an observation that lasts longer catches it.
greedy:     healthy, but allocates and touches ``DEMO_GREEDY_MB`` MiB and holds them. Above the RAM quota the
            Unit kills the instance (OOM) and restarts it: a second summary line appears in the log.
cpu-hog:    healthy, but keeps one CPU busy all the time instead of sleeping between heartbeats.
bad-config: healthy unless the environment variable ``DEMO_FEATURE_MODE`` is ``strict``; then ``config_loaded``
            fails (``reason=bad-config``) and the Service exits with code 1.
"""
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

BAKED_VERSION = '__DEMO_VERSION__'
BAKED_MODE = '__DEMO_MODE__'
BAKED_FAIL_AFTER_S = '__DEMO_FAIL_AFTER_S__'
BAKED_GREEDY_MB = '__DEMO_GREEDY_MB__'

MODE_GOOD = 'good'
MODE_BROKEN = 'broken'
MODE_LATE_FAIL = 'late-fail'
MODE_GREEDY = 'greedy'
MODE_CPU_HOG = 'cpu-hog'
MODE_BAD_CONFIG = 'bad-config'
MODES = (MODE_GOOD, MODE_BROKEN, MODE_LATE_FAIL, MODE_GREEDY, MODE_CPU_HOG, MODE_BAD_CONFIG)

ENV_FEATURE_MODE = 'DEMO_FEATURE_MODE'
ENV_ALLOWED_HOSTS = 'DEMO_ALLOWED_HOSTS'
ENV_RAM_LIMIT_MB = 'DEMO_RAM_LIMIT_MB'
FEATURE_MODE_STRICT = 'strict'

TEST_CONFIG = 'config_loaded'
TEST_STORAGE = 'storage_rw'
TEST_NETWORK = 'network_reachable'
TEST_HEARTBEAT = 'heartbeat_started'
TEST_QUOTA = 'quota_within_limits'
TEST_RUNTIME = 'runtime_health'
START_TESTS = (TEST_CONFIG, TEST_STORAGE, TEST_NETWORK, TEST_HEARTBEAT, TEST_QUOTA)

PASS = 'PASS'
FAIL = 'FAIL'
SKIP = 'SKIP'

DEFAULT_HEARTBEAT_INTERVAL = 30.0
DEFAULT_FAIL_AFTER_S = 300.0
DEFAULT_GREEDY_MB = 64
MIB = 1024 * 1024
PAGE = 4096
ALLOCATION_REPORT_STEP_MB = 16
CONNECT_TIMEOUT_S = 3.0


def baked_or_env(name: str, baked: str) -> str:
    """Return the environment value, else the baked one; an unreplaced token means nothing was baked."""
    value = os.environ.get(name)
    if value:
        return value
    # the token is assembled here so that the build-time replacement never touches this comparison
    token = '__' + name + '__'
    return '' if baked == token else baked


def test_line(name: str, status: str, version: str, reason: str = '') -> str:
    """``AOS-TEST`` line of one result."""
    middle = f' reason={reason}' if reason else ''
    return f'AOS-TEST {name} {status}{middle} version={version}'


def summary_line(version: str, results: list[tuple[str, str, str]]) -> str:
    """``AOS-TESTS`` line of a run; ``results`` are ``(name, status, reason)``."""
    passed = sum(1 for _, status, _ in results if status == PASS)
    failed = sum(1 for _, status, _ in results if status == FAIL)
    skipped = sum(1 for _, status, _ in results if status == SKIP)
    return f'AOS-TESTS version={version} passed={passed} failed={failed} skipped={skipped} total={len(results)}'


def allocate(megabytes: int, out) -> list[bytearray]:
    """Allocate ``megabytes`` MiB and write one byte per page, so the memory is really resident."""
    chunks = []
    for index in range(megabytes):
        chunk = bytearray(MIB)
        chunk[::PAGE] = b'\x01' * (MIB // PAGE)
        chunks.append(chunk)
        if (index + 1) % ALLOCATION_REPORT_STEP_MB == 0:
            print(f'ALLOCATED {index + 1} MiB', file=out, flush=True)
    return chunks


def burn(seconds: float, clock=time.monotonic) -> int:
    """Busy-loop for ``seconds``; return the number of loop rounds (keeps the work observable)."""
    end = clock() + seconds
    rounds = 0
    while clock() < end:
        rounds += 1
    return rounds


def check_config(mode: str, feature_mode: str) -> tuple[str, str]:
    """``config_loaded``: the configuration of this version is usable."""
    if mode not in MODES:
        return FAIL, f'unknown-mode:{mode}'
    if mode == MODE_BAD_CONFIG and feature_mode == FEATURE_MODE_STRICT:
        return FAIL, f'bad-config {ENV_FEATURE_MODE}={feature_mode}'
    return PASS, ''


def check_storage(mode: str, directory: str | None = None) -> tuple[str, str]:
    """``storage_rw``: a file can be written and read back; ``broken`` fails here on purpose."""
    if mode == MODE_BROKEN:
        return FAIL, 'demo-broken'
    try:
        with tempfile.NamedTemporaryFile(prefix='aos-demo-', suffix='.txt', dir=directory, delete=False) as handle:
            path = Path(handle.name)
            handle.write(b'storage check')
        try:
            data = path.read_bytes()
        finally:
            path.unlink(missing_ok=True)
    except OSError as e:
        return FAIL, f'storage-error:{e.__class__.__name__}'
    return (PASS, '') if data == b'storage check' else (FAIL, 'storage-mismatch')


def check_network(allowed_hosts: str, connect=socket.create_connection) -> tuple[str, str]:
    """``network_reachable``: every ``host:port`` of ``DEMO_ALLOWED_HOSTS`` accepts a connection; none: SKIP."""
    targets = [part.strip() for part in allowed_hosts.split(',') if part.strip()]
    if not targets:
        return SKIP, 'no-allowed-connections'
    for target in targets:
        host, _, port = target.rpartition(':')
        if not host or not port.isdigit():
            return FAIL, f'bad-target:{target}'
        try:
            connect((host, int(port)), timeout=CONNECT_TIMEOUT_S).close()
        except OSError as e:
            return FAIL, f'unreachable:{target}:{e.__class__.__name__}'
    return PASS, ''


def check_quota(held_mb: int, ram_limit_mb: str) -> tuple[str, str]:
    """``quota_within_limits``: the memory held by the Service fits the quota, when a quota is given."""
    if not ram_limit_mb:
        return PASS, ''
    try:
        limit = int(ram_limit_mb)
    except ValueError:
        return SKIP, f'bad-quota:{ram_limit_mb}'
    return (PASS, '') if held_mb <= limit else (FAIL, f'held {held_mb} MiB above quota {limit} MiB')


def run_start_tests(mode: str, version: str, out, *, feature_mode: str = '', allowed_hosts: str = '',
                    ram_limit_mb: str = '', greedy_mb: int = 0, connect=socket.create_connection,
                    storage_dir: str | None = None) -> list[tuple[str, str, str]]:
    """Run the start tests in order, print a line per test and the summary; return the results."""
    results = [(TEST_CONFIG, *check_config(mode, feature_mode))]
    results.append((TEST_STORAGE, *check_storage(mode, storage_dir)))
    results.append((TEST_NETWORK, *check_network(allowed_hosts, connect)))
    results.append((TEST_HEARTBEAT, PASS, ''))
    results.append((TEST_QUOTA, *check_quota(greedy_mb if mode == MODE_GREEDY else 0, ram_limit_mb)))
    for name, status, reason in results:
        print(test_line(name, status, version, reason), file=out, flush=True)
    print(summary_line(version, results), file=out, flush=True)
    return results


def run(mode: str, version: str, interval: float = DEFAULT_HEARTBEAT_INTERVAL,  # pylint: disable=too-many-arguments
        max_heartbeats: int | None = None, out=None, *, fail_after: float = DEFAULT_FAIL_AFTER_S,
        greedy_mb: int = DEFAULT_GREEDY_MB, feature_mode: str = '', allowed_hosts: str = '', ram_limit_mb: str = '',
        sleep=time.sleep, clock=time.monotonic, connect=socket.create_connection,
        storage_dir: str | None = None) -> int:
    """Run the tests, then the main loop; return the process exit code. ``max_heartbeats`` bounds the loop."""
    out = out or sys.stdout
    config_status, config_reason = check_config(mode, feature_mode)
    # the legacy line, kept for one release so that earlier runs stay comparable
    if mode == MODE_BROKEN:
        print(f'SELFTEST FAIL {version} reason=demo-broken', file=out, flush=True)
    elif config_status == FAIL:
        print(f'SELFTEST FAIL {version} reason={config_reason}', file=out, flush=True)
    else:
        print(f'SELFTEST OK {version}', file=out, flush=True)
    results = run_start_tests(mode, version, out, feature_mode=feature_mode, allowed_hosts=allowed_hosts,
                              ram_limit_mb=ram_limit_mb, greedy_mb=greedy_mb, connect=connect,
                              storage_dir=storage_dir)
    if any(status == FAIL for _, status, _ in results):
        return 2 if mode not in MODES else 1
    held = None
    if mode == MODE_GREEDY:
        held = allocate(greedy_mb, out)
        print(f'GREEDY HOLD {len(held)} MiB', file=out, flush=True)
    started = clock()
    count = 0
    while max_heartbeats is None or count < max_heartbeats:
        pause = interval
        if mode == MODE_LATE_FAIL:
            pause = max(0.0, min(interval, fail_after - (clock() - started)))
        if mode == MODE_CPU_HOG:
            burn(pause, clock)
        else:
            sleep(pause)
        count += 1
        if mode == MODE_LATE_FAIL and clock() - started >= fail_after:
            print(f'LATE FAIL {version} after={clock() - started:.0f}s', file=out, flush=True)
            print(test_line(TEST_RUNTIME, FAIL, version, 'late'), file=out, flush=True)
            return 1
        print(f'HEARTBEAT {version} seq={count}', file=out, flush=True)
    return 0


def main() -> int:
    """Entry point used by the Service ``cmd``."""
    version = baked_or_env('DEMO_VERSION', BAKED_VERSION) or 'unknown'
    mode = baked_or_env('DEMO_MODE', BAKED_MODE) or MODE_GOOD
    interval = float(os.environ.get('DEMO_HEARTBEAT_INTERVAL', DEFAULT_HEARTBEAT_INTERVAL))
    fail_after = float(baked_or_env('DEMO_FAIL_AFTER_S', BAKED_FAIL_AFTER_S) or DEFAULT_FAIL_AFTER_S)
    greedy_mb = int(baked_or_env('DEMO_GREEDY_MB', BAKED_GREEDY_MB) or DEFAULT_GREEDY_MB)
    return run(mode, version, interval, fail_after=fail_after, greedy_mb=greedy_mb,
               feature_mode=os.environ.get(ENV_FEATURE_MODE, ''),
               allowed_hosts=os.environ.get(ENV_ALLOWED_HOSTS, ''),
               ram_limit_mb=os.environ.get(ENV_RAM_LIMIT_MB, ''))


if __name__ == '__main__':
    sys.exit(main())
