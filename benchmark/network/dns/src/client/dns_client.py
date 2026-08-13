#!/usr/bin/env python3
"""DNS benchmark item: measures name resolution time and reports it.

Resolves NAME QUERIES times against the resolver the container was handed,
timing each query from just before the packet leaves to just after the matching
answer arrives, and reports the distribution as percentiles.

The query is built and sent here rather than shelled out to dig on purpose. dig
reports its query time in whole milliseconds, and a local dnsmasq answers in
hundreds of microseconds, so every sample would read as zero; timing the dig
process from outside is worse still, as starting it costs more than the query.
Doing it in-process also keeps dig, and the bind-utils package behind it, out
of the image.

Results go two ways. The full result, every individual sample included, is
printed to the service log. Only the percentiles worth charting are pushed to
VictoriaMetrics as benchmark_result samples, bracketed by checkpoint_event
Start/Stop, exactly as services/template/py does.

Configuration comes from the environment, so one image serves every scenario
and only the deployment differs:
    NAME            name to resolve (required)
    QUERIES         how many queries to send (default 2000)
    RANDOM_LABEL    1 prepends a random label to every query, to miss the cache
    RESOLVER        DNS server to ask, host or host:port; unset means the
                    nameservers from the container's /etc/resolv.conf

Usage:
    dns_client.py [--victoria-url http://victoriametrics:8428]
"""

import argparse
import datetime
import json
import os
import random
import socket
import string
import struct
import sys
import time
import urllib.error
import urllib.request

NAME = os.environ.get("NAME", "")
RESOLVER = os.environ.get("RESOLVER", "")
QUERIES = int(os.environ.get("QUERIES", "2000"))
RANDOM_LABEL = os.environ.get("RANDOM_LABEL", "0") == "1"

CONNECT_ATTEMPTS = 30
CONNECT_DELAY = 2
QUERY_TIMEOUT = 2
IDLE_DELAY = 60

RESOLV_CONF = "/etc/resolv.conf"
DNS_PORT = 53

NODE = "main"  # No mechanism yet for an instance to learn which node it's actually running on.

# Percentiles are picked the way sockperf picks them, so DNS and latency
# numbers can be read side by side.
PERCENTILES = (("p50_us", 0.50), ("p90_us", 0.90), ("p99_us", 0.99), ("p999_us", 0.999))

# Of those, the ones worth a time series.
CHARTED = (("p50_us", "p50"), ("p99_us", "p99"), ("p999_us", "p999"))


def log(message, file=sys.stdout):
    """Print a message prefixed with this instance's AOS_INSTANCE_ID."""
    print(f"[{os.environ.get('AOS_INSTANCE_ID', '')}] {message}", file=file)


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


def default_resolvers():
    """Every nameserver the container was handed, in order.

    All of them are returned, not just the first, because not all of them
    necessarily answer: on this stack the container is handed the bridge
    address first, while dnsmasq is bound to the node address only, so a query
    to the first one just times out. A libc resolver walks the list until
    something replies, and so does this client.
    """
    resolvers = []

    try:
        with open(RESOLV_CONF) as conf:
            for line in conf:
                fields = line.split()
                if len(fields) >= 2 and fields[0] == "nameserver":
                    resolvers.append(fields[1])
    except OSError:
        pass

    return resolvers


def resolver_address(resolver):
    """Split an optional port off the resolver, so a non standard one can be used."""
    host, separator, port = resolver.partition(":")

    return (host, int(port)) if separator else (host, DNS_PORT)


def build_query(name, query_id):
    """A minimal DNS query for an A record."""
    # Standard query, recursion desired, one question.
    header = struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0)
    labels = b"".join(bytes([len(p)]) + p.encode() for p in name.split(".") if p)
    # QTYPE A, QCLASS IN.
    return header + labels + b"\x00" + struct.pack("!HH", 1, 1)


def query_once(sock, resolver, name):
    """Send one query and return how long the answer took, in microseconds."""
    query_id = random.getrandbits(16)
    packet = build_query(name, query_id)

    start = time.perf_counter()
    sock.sendto(packet, resolver_address(resolver))

    while True:
        try:
            reply, _ = sock.recvfrom(4096)
        except socket.timeout:
            return None, "timeout"

        # Ignore anything that is not the answer to this query.
        if len(reply) >= 12 and struct.unpack("!H", reply[:2])[0] == query_id:
            break

    elapsed_us = (time.perf_counter() - start) * 1e6

    flags, _, answers = struct.unpack("!HHH", reply[2:8])
    rcode = flags & 0x0F

    if rcode != 0:
        return None, f"rcode {rcode}"

    if answers == 0:
        return None, "no answer records"

    return elapsed_us, ""


def percentile(sorted_samples, fraction):
    index = int(0.5 + fraction * len(sorted_samples)) - 1

    return sorted_samples[max(index, 0)]


def summarize(samples):
    ordered = sorted(samples)
    count = len(ordered)
    mean = sum(ordered) / count
    variance = sum((value - mean) ** 2 for value in ordered) / count

    result = {name: round(percentile(ordered, fraction), 3) for name, fraction in PERCENTILES}
    result["min_us"] = round(ordered[0], 3)
    result["max_us"] = round(ordered[-1], 3)
    result["avg_us"] = round(mean, 3)
    result["stddev_us"] = round(variance**0.5, 3)

    return result


def query_name():
    """The name to resolve, with a unique label when the cache has to be missed.

    dnsmasq caches answers, so repeating one name measures its cache rather
    than resolution. A random leftmost label defeats that, and it still
    resolves as long as the DNS server holding the name answers for the whole
    domain.
    """
    if not RANDOM_LABEL:
        return NAME

    label = "".join(random.choice(string.ascii_lowercase) for _ in range(12))

    return f"{label}.{NAME}"


def pick_resolver(candidates):
    """Settle on a resolver that answers, and wait for the name to exist.

    Two things can keep the first query from succeeding, and both are normal:
    a nameserver in the list may not be listening at all, and a peer service
    may still be starting, since the unit registers its name only once the
    instance runs. So every candidate is tried on every attempt, and the first
    one that answers wins.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(QUERY_TIMEOUT)

    try:
        for attempt in range(1, CONNECT_ATTEMPTS + 1):
            for candidate in candidates:
                _, error = query_once(sock, candidate, query_name())

                if not error:
                    return candidate

                log(f"{candidate} did not resolve {NAME} ({attempt}/{CONNECT_ATTEMPTS}): {error}")

            time.sleep(CONNECT_DELAY)
    finally:
        sock.close()

    return ""


def run_benchmark(resolver):
    """Resolve the name QUERIES times and return the results as {value_name: value}."""
    samples = []
    failures = {}

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(QUERY_TIMEOUT)

    try:
        for _ in range(QUERIES):
            elapsed_us, error = query_once(sock, resolver, query_name())

            if error:
                failures[error] = failures.get(error, 0) + 1
            else:
                samples.append(elapsed_us)
    finally:
        sock.close()

    metric = {
        "test": "dns_resolve",
        "name": NAME,
        "resolver": resolver,
        "queries": QUERIES,
        "resolved": len(samples),
        "failed": QUERIES - len(samples),
    }

    if failures:
        metric["failures"] = failures

    if samples:
        metric.update(summarize(samples))
        metric["raw"] = {"samples_us": [round(value, 3) for value in samples]}
    else:
        metric["error"] = "; ".join(f"{reason} x{count}" for reason, count in failures.items())

    # The log keeps everything, every individual sample included, so the
    # percentiles can be recomputed later; only a few are worth a time series.
    log(json.dumps(metric))

    return {f"resolve {label}, us": metric[key] for key, label in CHARTED if metric.get(key) is not None}


def main():
    """Push a start event, run the benchmark, push its results, then push a stop event."""
    args = parse_args()
    source = f"Instance: {os.environ['AOS_INSTANCE_ID']}"

    if not NAME:
        log("NAME environment variable is required", file=sys.stderr)

        return 1

    candidates = [RESOLVER] if RESOLVER else default_resolvers()

    if not candidates:
        log(
            f"No resolver: RESOLVER is unset and {RESOLV_CONF} has no nameserver",
            file=sys.stderr,
        )

        return 1

    log(
        f"DNS benchmark: name={NAME} resolvers={','.join(candidates)} "
        f"queries={QUERIES} random_label={int(RANDOM_LABEL)}"
    )

    resolver = pick_resolver(candidates)

    if not resolver:
        resolver = candidates[0]
        log(f"No resolver answered for {NAME}, measuring against {resolver} anyway")
    else:
        log(f"Using resolver {resolver}")

    push_event(args.victoria_url, NODE, source, "Start")

    try:
        results = run_benchmark(resolver)

        for name, value in results.items():
            push_result(args.victoria_url, NODE, source, name, value)
    finally:
        push_event(args.victoria_url, NODE, source, "Stop")

    log("All tests finished")

    # The benchmark is a one shot run, but the instance keeps running so its
    # logs stay available and the unit does not restart it in a loop.
    while True:
        time.sleep(IDLE_DELAY)


if __name__ == "__main__":
    sys.exit(main())
