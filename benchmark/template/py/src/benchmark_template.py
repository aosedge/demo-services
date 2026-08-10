#!/usr/bin/env python3
"""Template for a benchmark deployable item that reports its results to VictoriaMetrics.

Copy this script as the starting point for a real benchmark item (disk I/O, network, etc. - see
doc/benchmark.md's "Container disk I/O" / "Network performance" chapters) and replace
run_benchmark()'s placeholder body with the real fio/iperf3/etc. invocation and result parsing.
Everything else (start/stop events, result pushing, VictoriaMetrics wiring) can be reused as-is.

Pushes three things to VictoriaMetrics' /api/v1/import/prometheus endpoint - localhost:8428 if this
runs on the main node, or the main node's address if run on a secondary (victoriametrics.service
binds 0.0.0.0:8428 precisely so secondary-node containers can reach it over the network):
  - a checkpoint_event sample when the benchmark starts (event="Start"), the same metric
    event_exporter.py produces from log checkpoints, so this shows up in the same Grafana Events
    table/annotations as AosCore's own instance start/stop checkpoints.
  - one benchmark_result sample per measured value (there can be several - e.g. throughput and
    latency from the same run), labeled by "name" so multiple values don't collide.
  - a checkpoint_event sample when the benchmark ends (event="Stop").

The "source" label comes from the AOS_INSTANCE_ID environment variable AosCore sets for every app
instance, not a command-line option, so each running instance of this item is told apart
automatically. "node" is always "main": there's currently no mechanism for an instance to learn
which node it's actually running on.

Usage:
    benchmark_template.py [--victoria-url http://victoriametrics:8428]
"""

import argparse
import datetime
import os
import sys
import time
import urllib.error
import urllib.request

BENCHMARK_DELAY_SECONDS = 10
NODE = "main"  # No mechanism yet for an instance to learn which node it's actually running on.


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
    # Exact integer arithmetic, not timestamp_us / 1e6 - float64 is right at the edge of enough
    # precision for a 10-digit epoch second count plus 6 more decimal digits, so this avoids any
    # risk of the last microsecond digit being wrong.
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
    """Push a checkpoint_event sample (the same metric event_exporter.py produces).

    Carries a "time_us" label alongside the sample's own timestamp: VictoriaMetrics' sample
    timestamps are millisecond-precision only, and the Grafana Events table reads its "Timestamp"
    column from this label rather than the sample's own Time (see aos-benchmark.json's "organize"
    transformation) - without it, these rows show up with no timestamp at all.
    """
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
    """Push a single benchmark_result sample for one measured value.

    Carries a "time_us" label alongside the sample's own timestamp, same as push_event: the
    Grafana Benchmark Results table reads its "Timestamp" column from this label rather than the
    sample's own Time, which Grafana only renders down to whole seconds.
    """
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


def run_benchmark():
    """Run the actual benchmark and return its results as {value_name: value}.

    Placeholder: sleeps for BENCHMARK_DELAY_SECONDS to stand in for real benchmark work, then
    returns fixed example values. Replace this body with a real fio/iperf3/etc. invocation and
    parse its output into the same {name: value} shape.
    """
    time.sleep(BENCHMARK_DELAY_SECONDS)

    return {
        "throughput, mbps": 123.4,
        "latency, ms": 5.6,
    }


def main():
    """Push a start event, run the benchmark, push its results, then push a stop event."""
    args = parse_args()
    source = f"Instance: {os.environ['AOS_INSTANCE_ID']}"

    push_event(args.victoria_url, NODE, source, "Start")

    try:
        results = run_benchmark()

        for name, value in results.items():
            push_result(args.victoria_url, NODE, source, name, value)
    finally:
        push_event(args.victoria_url, NODE, source, "Stop")


if __name__ == "__main__":
    main()
