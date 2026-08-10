#!/usr/bin/env python3
"""Bandwidth benchmark item: measures throughput with iperf3 and reports it.

Runs four tests in a row against TARGET, each for DURATION seconds: TCP and
UDP, in both directions. iperf3 does the measuring; this script only starts it,
parses its JSON and reports the result.

Results go two ways. Every test's full result, iperf3's own JSON document
included, is printed to the service log, which is what makes a failed run
diagnosable afterwards. Only the few numbers worth charting are pushed to
VictoriaMetrics as benchmark_result samples, bracketed by checkpoint_event
Start/Stop, exactly as services/template/py does.

Configuration comes from the environment, so one image serves every scenario
and only the deployment differs:
    TARGET          server hostname or IP (required)
    DURATION        seconds per test (default 5)
    PORT            iperf3 port (default 5201)
    UDP_BANDWIDTH   target rate for the UDP tests, 0 is unlimited (default 0)

Usage:
    bandwidth_client.py [--victoria-url http://victoriametrics:8428]
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

TARGET = os.environ.get("TARGET", "")
DURATION = os.environ.get("DURATION", "5")
PORT = os.environ.get("PORT", "5201")
UDP_BANDWIDTH = os.environ.get("UDP_BANDWIDTH", "0")

CONNECT_ATTEMPTS = 30
CONNECT_DELAY = 2
TEST_GAP = 3
IDLE_DELAY = 60

NODE = "main"  # No mechanism yet for an instance to learn which node it's actually running on.

# Test name -> extra iperf3 arguments. "up" is client -> server, "down" is
# server -> client (iperf3 -R, reverse mode).
TESTS = [
    ("tcp_up", "tcp", []),
    ("tcp_down", "tcp", ["-R"]),
    ("udp_up", "udp", ["-u", "-b", UDP_BANDWIDTH]),
    ("udp_down", "udp", ["-u", "-b", UDP_BANDWIDTH, "-R"]),
]

# iperf3 error fragments that mean the server is not up yet, as opposed to the
# test itself having failed. Only these are worth retrying.
CONNECT_ERRORS = (
    "unable to connect to server",
    "unable to send control message",
    "unable to receive control message",
    "Connection refused",
    "No route to host",
    "Name or service not known",
    "Temporary failure in name resolution",
)


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


def throughput_bps(result, protocol):
    """Extract the throughput iperf3 measured, in bits per second.

    TCP results are reported per direction in `sum_sent` / `sum_received`; the
    receiver side is what actually arrived, so it is preferred. UDP results
    carry the interesting numbers (including loss and jitter) in `sum`.
    """
    end = result.get("end", {})

    if protocol == "udp":
        sections = ("sum", "sum_received", "sum_sent")
    else:
        sections = ("sum_received", "sum_sent", "sum")

    for name in sections:
        section = end.get(name)
        if isinstance(section, dict) and "bits_per_second" in section:
            return section["bits_per_second"]

    return None


def udp_stats(result):
    """Loss and jitter counters reported for a UDP test, if present."""
    summary = result.get("end", {}).get("sum")
    if not isinstance(summary, dict):
        return {}

    keys = ("jitter_ms", "lost_packets", "packets", "lost_percent")

    return {key: summary[key] for key in keys if key in summary}


def is_connect_error(message):
    return any(fragment in message for fragment in CONNECT_ERRORS)


def run_iperf(extra_args):
    """Run one iperf3 test and return its parsed result and error message."""
    cmd = ["iperf3", "-c", TARGET, "-p", PORT, "-t", DURATION, "-J"] + extra_args

    print(f"Running: {' '.join(cmd)}")

    process = subprocess.run(cmd, capture_output=True, text=True)

    try:
        result = json.loads(process.stdout)
    except ValueError:
        error = process.stderr.strip() or f"iperf3 exited with code {process.returncode}"
        return None, error

    # With -J iperf3 reports failures inside the JSON document itself.
    return result, result.get("error", "")


def run_test(name, protocol, extra_args, attempts=1):
    """Run one test, log its whole result, and return the values worth charting.

    The server instance may still be starting when the client starts, so the
    first test is given several attempts: otherwise its result would say more
    about instance start order than about the network.
    """
    for attempt in range(1, attempts + 1):
        result, error = run_iperf(extra_args)

        if not error or not is_connect_error(error) or attempt == attempts:
            break

        print(f"Server {TARGET}:{PORT} not ready ({attempt}/{attempts}): {error}")
        time.sleep(CONNECT_DELAY)

    metric = {
        "test": name,
        "protocol": protocol,
        "target": TARGET,
        "port": int(PORT),
        "duration_s": int(DURATION),
    }

    if error:
        metric["error"] = error
    else:
        metric["throughput_bps"] = throughput_bps(result, protocol)

        if protocol == "udp":
            metric.update(udp_stats(result))

    if result is not None:
        metric["raw"] = result

    # The log keeps everything, iperf3's own document included; only a handful
    # of numbers are worth a time series.
    print(json.dumps(metric))

    values = {}

    if metric.get("throughput_bps") is not None:
        values[f"{name} throughput, Mbps"] = round(metric["throughput_bps"] / 1e6, 3)

    if metric.get("lost_percent") is not None:
        values[f"{name} loss, %"] = metric["lost_percent"]

    if metric.get("jitter_ms") is not None:
        values[f"{name} jitter, ms"] = metric["jitter_ms"]

    return values


def run_benchmark():
    """Run the four tests and return their results as {value_name: value}."""
    results = {}
    attempts = CONNECT_ATTEMPTS

    for index, (name, protocol, extra_args) in enumerate(TESTS):
        # An iperf3 server runs one test at a time and needs a moment to reset
        # between them. Starting the next test the instant the previous one
        # ends makes it fail on the control connection, most often right after
        # an unlimited UDP test, which leaves the server draining its buffers.
        if index:
            time.sleep(TEST_GAP)

        results.update(run_test(name, protocol, extra_args, attempts))
        attempts = 1

    return results


def main():
    """Push a start event, run the benchmark, push its results, then push a stop event."""
    args = parse_args()
    source = f"Instance: {os.environ['AOS_INSTANCE_ID']}"

    if not TARGET:
        print("TARGET environment variable is required", file=sys.stderr)
        return 1

    print(
        f"Bandwidth benchmark: target={TARGET} port={PORT} "
        f"duration={DURATION}s udp_bandwidth={UDP_BANDWIDTH}"
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
