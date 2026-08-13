#!/usr/bin/env python3
"""Latency benchmark item: measures round trip time with sockperf and reports it.

Runs a ping-pong test against TARGET over UDP and over TCP, each for DURATION
seconds, and reports the round trip time as percentiles rather than an average:
latency distributions are skewed, and an average hides the tail that real-time
and RPC traffic actually feel.

Results go two ways. Every test's full result, sockperf's own report included,
is printed to the service log, which is what makes a failed run diagnosable
afterwards. Only the percentiles worth charting are pushed to VictoriaMetrics
as benchmark_result samples, bracketed by checkpoint_event Start/Stop, exactly
as services/template/py does.

Configuration comes from the environment, so one image serves every scenario
and only the deployment differs:
    TARGET      server hostname or IP (required)
    DURATION    seconds per test (default 5)
    PORT        sockperf port (default 11111)
    MSG_SIZE    payload size in bytes (default 64)

Usage:
    latency_client.py [--victoria-url http://victoriametrics:8428]
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

TARGET = os.environ.get("TARGET", "")
DURATION = os.environ.get("DURATION", "5")
PORT = os.environ.get("PORT", "11111")
MSG_SIZE = os.environ.get("MSG_SIZE", "64")

CONNECT_ATTEMPTS = 30
CONNECT_DELAY = 2
TEST_GAP = 3
IDLE_DELAY = 60

NODE = "main"  # No mechanism yet for an instance to learn which node it's actually running on.

# Test name -> extra sockperf arguments. sockperf speaks UDP unless told
# otherwise, so only the TCP test needs a flag.
TESTS = [
    ("udp_rtt", "udp", []),
    ("tcp_rtt", "tcp", ["--tcp"]),
]

# sockperf reports percentiles by their exact label; these are the ones the
# benchmark plan asks for, plus the neighbours that make the tail readable.
PERCENTILES = {
    "50.000": "p50_us",
    "90.000": "p90_us",
    "99.000": "p99_us",
    "99.900": "p999_us",
    "99.990": "p9999_us",
}

# Of those, the ones worth a time series.
CHARTED = (("p50_us", "p50"), ("p99_us", "p99"), ("p999_us", "p999"))

PERCENTILE_RE = re.compile(r"percentile\s+([\d.]+)\s*=\s*([\d.]+)")
MIN_RE = re.compile(r"<MIN>\s+observation\s*=\s*([\d.]+)")
MAX_RE = re.compile(r"<MAX>\s+observation\s*=\s*([\d.]+)")
# sockperf labels the average avg-rtt with --full-rtt and avg-latency without.
AVG_RE = re.compile(r"avg-(?:rtt|latency)\s*=\s*([\d.]+)")
STDDEV_RE = re.compile(r"std-dev\s*=\s*([\d.]+)")
OBSERVATIONS_RE = re.compile(r"Total\s+(\d+)\s+observations")
DROPPED_RE = re.compile(r"dropped messages\s*=\s*(\d+)")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def parse_args():
    """Parse --victoria-url command-line option."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--victoria-url",
        default="http://victoriametrics:8428",
        help="main node's VictoriaMetrics base URL (default: %(default)s)",
    )
    return parser.parse_args()


def escape_label_value(value):
    """Escape a string for safe embedding inside a Prometheus exposition-format label value."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def format_precise_time(timestamp_us):
    """Format a microsecond epoch timestamp as "YYYY-MM-DD HH:MM:SS.ffffff" (UTC)."""
    seconds, microseconds = divmod(timestamp_us, 1_000_000)
    dt = datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)
    dt += datetime.timedelta(microseconds=microseconds)

    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")


def push_line(victoria_url, line):
    """POST a single Prometheus exposition-format line to VictoriaMetrics."""
    request = urllib.request.Request(
        f"{victoria_url.rstrip('/')}/api/v1/import/prometheus",
        data=line.encode(),
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            response.read()
    except urllib.error.URLError as err:
        print(f"failed to push to VictoriaMetrics: {err}", file=sys.stderr)


def push_event(victoria_url, node, source, event):
    """Push a checkpoint_event sample (the same metric event_exporter.py produces)."""
    timestamp_us = int(time.time() * 1_000_000)
    labels = ",".join(
        f'{name}="{escape_label_value(value)}"'
        for name, value in (
            ("node", node),
            ("source", source),
            ("event", event),
            ("time_us", format_precise_time(timestamp_us)),
        )
    )
    time_s = timestamp_us / 1_000_000
    push_line(victoria_url, f"checkpoint_event{{{labels}}} 1 {time_s:.3f}")


def push_result(victoria_url, node, source, name, value):
    """Push a single benchmark_result sample for one measured value."""
    timestamp_us = int(time.time() * 1_000_000)
    labels = ",".join(
        f'{label}="{escape_label_value(text)}"'
        for label, text in (
            ("node", node),
            ("source", source),
            ("name", name),
            ("time_us", format_precise_time(timestamp_us)),
        )
    )
    time_s = timestamp_us / 1_000_000
    push_line(victoria_url, f"benchmark_result{{{labels}}} {value} {time_s:.3f}")


def parse(output):
    """Pull the latency figures out of sockperf's report.

    sockperf has no machine readable output, so its text report is parsed.
    Every value is in microseconds and, because the tests run with --full-rtt,
    is a full round trip rather than the one way figure sockperf reports by
    default.
    """
    # sockperf colours parts of its report, so drop the escape sequences first.
    output = ANSI_RE.sub("", output)

    result = {}

    for label, value in PERCENTILE_RE.findall(output):
        name = PERCENTILES.get(label)
        if name:
            result[name] = float(value)

    for pattern, name in (
        (MIN_RE, "min_us"),
        (MAX_RE, "max_us"),
        (AVG_RE, "avg_us"),
        (STDDEV_RE, "stddev_us"),
    ):
        match = pattern.search(output)
        if match:
            result[name] = float(match.group(1))

    for pattern, name in ((OBSERVATIONS_RE, "observations"), (DROPPED_RE, "dropped")):
        match = pattern.search(output)
        if match:
            result[name] = int(match.group(1))

    return result


def run_sockperf(extra_args):
    """Run one ping-pong test and return its output and error message."""
    cmd = [
        "sockperf", "ping-pong",
        "-i", TARGET,
        "-p", PORT,
        "-t", DURATION,
        "-m", MSG_SIZE,
        "--full-rtt",
    ] + extra_args

    print(f"Running: {' '.join(cmd)}")

    process = subprocess.run(cmd, capture_output=True, text=True)

    # sockperf writes its report to stdout and its diagnostics to stderr, and
    # both matter: a run can exit cleanly having received nothing at all.
    output = process.stdout + process.stderr

    if process.returncode != 0:
        error = output.strip().splitlines()[-1] if output.strip() else \
            f"sockperf exited with code {process.returncode}"
        return output, error

    return output, ""


def run_test(name, protocol, extra_args, attempts=1):
    """Run one test, log its whole result, and return the values worth charting.

    The server may still be starting when the client reaches it, so the first
    test is given several attempts: otherwise its result would say more about
    start order than about the network.
    """
    for attempt in range(1, attempts + 1):
        output, error = run_sockperf(extra_args)
        values = parse(output)

        # A run that produced no percentiles never reached the server, whatever
        # its exit code says.
        if not error and values.get("p50_us") is not None:
            break

        if attempt == attempts:
            break

        print(f"Server {TARGET}:{PORT} not ready ({attempt}/{attempts}): {error or 'no observations'}")
        time.sleep(CONNECT_DELAY)

    metric = {
        "test": name,
        "protocol": protocol,
        "target": TARGET,
        "port": int(PORT),
        "duration_s": int(DURATION),
        "msg_size": int(MSG_SIZE),
    }

    if values.get("p50_us") is None:
        metric["error"] = error or "sockperf produced no observations"
    else:
        metric.update(values)

    metric["raw"] = {"output": output}

    # The log keeps everything, sockperf's own report included; only the
    # percentiles the plan asks for are worth a time series.
    print(json.dumps(metric))

    return {
        f"{name} {label}, us": metric[key]
        for key, label in CHARTED
        if metric.get(key) is not None
    }


def run_benchmark():
    """Run both tests and return their results as {value_name: value}."""
    results = {}
    attempts = CONNECT_ATTEMPTS

    for index, (name, protocol, extra_args) in enumerate(TESTS):
        # Give the server a moment to reset between tests, the same way the
        # bandwidth benchmark does: starting the next test the instant the
        # previous one ends is what makes it fail on the control connection.
        if index:
            time.sleep(TEST_GAP)

        results.update(run_test(name, protocol, extra_args, attempts))
        attempts = 1

    return results


def main():
    """Push a start event, run the benchmark, push its results, then push a stop event."""
    args = parse_args()
    source = os.environ["AOS_INSTANCE_ID"]

    if not TARGET:
        print("TARGET environment variable is required", file=sys.stderr)
        return 1

    print(
        f"Latency benchmark: target={TARGET} port={PORT} "
        f"duration={DURATION}s msg_size={MSG_SIZE}"
    )

    push_event(args.victoria_url, NODE, source, "Start")

    try:
        results = run_benchmark()

        for name, value in results.items():
            push_result(args.victoria_url, NODE, source, name, value)
    finally:
        push_event(args.victoria_url, NODE, source, "Stop")

    print("All tests finished")

    # The benchmark is a one shot run, but the instance keeps running so its
    # logs stay available and the unit does not restart it in a loop.
    while True:
        time.sleep(IDLE_DELAY)


if __name__ == "__main__":
    sys.exit(main())
