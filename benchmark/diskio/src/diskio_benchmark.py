#!/usr/bin/env python3
"""Disk I/O benchmark item: measures throughput/IOPS and latency with fio and reports it.

Runs four fio jobs in a row against a file on the enabled storage volume, each for RUNTIME
seconds, in this order:
    sequential_write  write,     1M blocks, iodepth 16 - reports throughput
    sequential_read   read,      1M blocks, iodepth 16 - reports throughput
    random_write      randwrite, 4K blocks, iodepth 32 - reports IOPS
    random_read       randread,  4K blocks, iodepth 32 - reports IOPS

Every job also reports average/p99 completion latency, regardless of its headline metric.

fio does the measuring; this script only starts it, parses its JSON, and reports the result.

Results go two ways. Every job's full result, fio's own JSON document included, is printed to the
service log, which is what makes a failed run diagnosable afterwards. Only the few numbers worth
charting are pushed to VictoriaMetrics as benchmark_result samples. The whole run is bracketed by
checkpoint_event Start/Stop, exactly as services/template/py does, and each job additionally pushes
its own checkpoint_event (event=<job name>) the moment it begins, so Grafana can mark where each
job started within the run.

Configuration comes from the environment, so one image serves every instance-count tier and only
the deployment differs:
    TEST_DIR     directory to read/write in on the storage volume (default /storage); the file
                 itself is always TEST_DIR/${AOS_INSTANCE_ID}.dat, so concurrent instances sharing
                 a volume never collide on the same file
    SIZE         size of the test file (default 16M)
    RUNTIME      seconds per job (default 60)

fio block size and iodepth are fixed per job, not configurable via the environment.

Usage:
    diskio_benchmark.py [--victoria-url http://victoriametrics:8428]
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

TEST_DIR = os.environ.get("TEST_DIR", "/storage")
TEST_FILE = os.path.join(TEST_DIR, f"{os.environ['AOS_INSTANCE_ID']}.dat")
SIZE = os.environ.get("SIZE", "16M")
RUNTIME = os.environ.get("RUNTIME", "60")

IDLE_DELAY = 60

NODE = "main"  # No mechanism yet for an instance to learn which node it's actually running on.

# Fixed block size/iodepth and headline-metric extraction shared by both jobs of each kind.
SEQUENTIAL = {
    "block_size": "1M",
    "iodepth": "16",
    "metric_key": "throughput_bps",
    "value_name": "{} throughput, MB/s",
    "value_round": 3,
    "extract_value": lambda section: (section["bw_bytes"] / 1e6 if section.get("bw_bytes") is not None else None),
}
RANDOM = {
    "block_size": "4k",
    "iodepth": "32",
    "metric_key": "iops",
    "value_name": "{} IOPS",
    "value_round": 1,
    "extract_value": lambda section: section.get("iops"),
}

# The jobs run in this exact order: sequential write, sequential read, then random write, random
# read.
JOBS = [
    {"name": "sequential_write", "rw": "write", **SEQUENTIAL},
    {"name": "sequential_read", "rw": "read", **SEQUENTIAL},
    {"name": "random_write", "rw": "randwrite", **RANDOM},
    {"name": "random_read", "rw": "randread", **RANDOM},
]


def log(message, file=sys.stdout):
    """Print a message prefixed with this instance's AOS_INSTANCE_ID."""
    print(f"[{os.environ.get('AOS_INSTANCE_ID', '')}] {message}", file=file)


def parse_args():
    """Parse the --victoria-url command-line option."""
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
        log(f"failed to push to VictoriaMetrics: {err}", file=sys.stderr)


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


def log_permissions():
    """Log the process's uid/gid and TEST_DIR's owner/mode, to diagnose permission errors from fio.

    A new file can only be created in TEST_DIR if this process's uid/gid is allowed to write there;
    an existing file can be written regardless, since that only needs permission on the file itself.
    """
    try:
        dir_stat = os.stat(TEST_DIR)
        dir_info = f"owner={dir_stat.st_uid}:{dir_stat.st_gid} mode={oct(dir_stat.st_mode & 0o777)}"
    except OSError as err:
        dir_info = f"stat failed: {err}"

    log(
        f"Running as uid={os.getuid()} gid={os.getgid()} euid={os.geteuid()} egid={os.getegid()}; "
        f"{TEST_DIR} {dir_info}"
    )


def run_fio(name, rw, block_size, iodepth):
    """Run one fio job and return its parsed result and error message.

    A job can fail two different ways: fio itself can't be run to completion (e.g. it can't open
    TEST_FILE at all), in which case it exits non-zero and prints no usable JSON; or fio runs to
    completion but the job hit an I/O error partway through (e.g. TEST_FILE's volume filled up),
    in which case it still prints a JSON report, but the job's own "error" field is non-zero. Both
    are treated as failures - a partial report with silently-truncated I/O is not a real result.
    """
    cmd = [
        "fio",
        f"--name={name}",
        f"--filename={TEST_FILE}",
        f"--rw={rw}",
        f"--bs={block_size}",
        f"--iodepth={iodepth}",
        "--ioengine=libaio",
        "--direct=1",
        f"--size={SIZE}",
        f"--runtime={RUNTIME}",
        "--time_based",
        "--group_reporting",
        "--lat_percentiles=1",
        "--thread",
        "--output-format=json",
    ]

    log(f"Running: {' '.join(cmd)}")

    process = subprocess.run(cmd, capture_output=True, text=True)

    try:
        result = json.loads(process.stdout)
    except ValueError:
        result = None

    if result is None:
        error = process.stderr.strip() or f"fio exited with code {process.returncode}"
        return None, error

    job = result.get("jobs", [{}])[0]
    job_error = job.get("error", 0)

    if job_error:
        error = job.get("error_msg") or f"fio job failed, errno {job_error}: {os.strerror(job_error)}"
        return result, error

    if process.returncode != 0:
        error = process.stderr.strip() or f"fio exited with code {process.returncode}"
        return result, error

    return result, ""


def latency_stats(section):
    """Average and p99 completion latency (ms), if fio reported them.

    NOTE: assumes clat_ns carries a "percentile" map keyed "99.000000" - verify this shape
    against a real fio JSON output on target before trusting it, per --lat_percentiles=1.
    """
    clat_ns = section.get("clat_ns", {})
    stats = {}

    if "mean" in clat_ns:
        stats["avg_ms"] = clat_ns["mean"] / 1e6

    percentile = clat_ns.get("percentile", {})
    p99 = percentile.get("99.000000")
    if p99 is not None:
        stats["p99_ms"] = p99 / 1e6

    return stats


def run_job(job, victoria_url, source):
    """Push a checkpoint_event for the job's start, run it, log its result, and return the values worth charting."""
    name, rw, block_size, iodepth = job["name"], job["rw"], job["block_size"], job["iodepth"]

    push_event(victoria_url, NODE, source, name)

    result, error = run_fio(name, rw, block_size, iodepth)

    metric = {
        "job": name,
        "rw": rw,
        "file": TEST_FILE,
        "block_size": block_size,
        "iodepth": iodepth,
    }

    if error:
        metric["error"] = error

        log(json.dumps(metric), file=sys.stderr)

        return {}

    job_result = result.get("jobs", [{}])[0]
    section = job_result.get("read" if rw in ("randread", "read") else "write", {})

    metric[job["metric_key"]] = job["extract_value"](section)
    metric.update(latency_stats(section))

    # The log keeps everything, fio's own document included; only a handful of numbers
    # are worth a time series.
    log(json.dumps(metric))

    values = {}

    value = metric[job["metric_key"]]
    if value is not None:
        values[job["value_name"].format(name)] = round(value, job["value_round"])

    if metric.get("avg_ms") is not None:
        values[f"{name} latency avg, ms"] = round(metric["avg_ms"], 3)

    if metric.get("p99_ms") is not None:
        values[f"{name} latency p99, ms"] = round(metric["p99_ms"], 3)

    return values


def run_benchmark(victoria_url, source):
    """Run every job in JOBS, in order, and return their results as {value_name: value}."""
    results = {}

    for job in JOBS:
        results.update(run_job(job, victoria_url, source))

    return results


def main():
    """Push a start event, run the benchmark, push its results, then push a stop event."""
    args = parse_args()
    source = f"Instance: {os.environ['AOS_INSTANCE_ID']}"

    log_permissions()

    log(f"Disk I/O benchmark: file={TEST_FILE} size={SIZE} runtime={RUNTIME}s")

    push_event(args.victoria_url, NODE, source, "Start")

    try:
        results = run_benchmark(args.victoria_url, source)

        for name, value in results.items():
            push_result(args.victoria_url, NODE, source, name, value)
    finally:
        push_event(args.victoria_url, NODE, source, "Stop")

    log("All jobs finished")

    # The benchmark is a one shot run, but the instance keeps running so its logs stay
    # available and the unit does not restart it in a loop.
    while True:
        time.sleep(IDLE_DELAY)


if __name__ == "__main__":
    sys.exit(main())
