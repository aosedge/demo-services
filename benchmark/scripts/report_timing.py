#!/usr/bin/env python3
"""Watch VictoriaMetrics for AosCore deployment test suites and print an aggregated timing report
for each one as it completes, until interrupted with Ctrl+C.

Computes the elapsed time between each of the following checkpoint_event pairs (all pushed by
AosCore itself via event_exporter.py, except "Instance ... Start", which each benchmark-timing
instance pushes directly - see benchmark/timing/src/benchmark-timing.cpp):

    Metric           Start source     Start event                        End source        End event
    ---------------  ---------------  ----------------------------------  ----------------  ----------------------------------
    Download         aos-cm.service   Download update items start         aos-cm.service    Download update items end
    Install          aos-sm.service   Install items begin                 aos-sm.service    Install items end
    Prepare          aos-sm.service   Prepare instances begin             aos-sm.service    Prepare instances end
    Init SM          init.scope       Starting AosCore Service Manager...  aos-sm.service    Update instances begin
    Start network    aos-sm.service   Start networks begin                aos-sm.service    Start networks end
    Start instances  aos-sm.service   Start instances begin               aos-sm.service    Start instances end
    Stop network      aos-sm.service   Stop all networks begin             aos-sm.service    Stop all networks end
    Stop instances    aos-sm.service   Stop all instances begin            aos-sm.service    Stop all instances end
    Release SM        aos-sm.service   Stop all networks end               init.scope        Stopped AosCore Service Manager.
    Total             aos-cm.service   Process desired status              (last instance)   Start

A few things this has to account for, all confirmed empirically against a live VictoriaMetrics:

  - AosCore appends its own ": key=value, ..." detail suffix to some of its checkpoint lines (e.g.
    "Download update items start: count=1", "Update instances begin: stopCount=1, startCount=1"),
    and some contain regex metacharacters of their own (the literal "..." in "Starting AosCore
    Service Manager..."), so every lookup matches with a regex that both tolerates an optional
    suffix and escapes the checkpoint text itself, rather than comparing it exactly - see
    event_query().

  - "Process desired status" is logged twice per deployment: once when AosCore starts processing
    the desired status, and again once it finishes. Taking the plain latest occurrence would grab
    the second (finish) one, which lands after the last instance already started, making "Total" go
    negative. Resolved by first finding the last instance's own Start, then looking up "Process
    desired status" constrained to at or before that moment - see TotalJob.

  - "Init SM"'s start event ("Starting AosCore Service Manager...") only happens once per SM
    restart, but its end event ("Update instances begin") happens on every single deployment
    afterward. Taking the plain latest occurrence of each independently can pair a rare, long-ago SM
    restart with an unrelated, much later deployment's "Update instances begin", inflating "Init SM"
    by however long SM has been running since. Resolved the same way as "Total": once the rare side
    is found, the frequent side is constrained to the *earliest* occurrence at or after it, not the
    latest overall - see RangeJob's `nearest_end`.

  - AosCore logs "Stop network"/"Stop instances"' checkpoints two ways: "Stop all networks/instances
    begin/end" when the whole SM process is shutting down (releasing everything, paired with
    "Release SM"), and just "Stop networks/instances begin/end" (no "all") for a routine per-update
    teardown of only the instances/networks actually being replaced - confirmed empirically, the
    latter is what happens on every ordinary deployment, the former only on an actual SM
    restart/shutdown. Since a full SM shutdown essentially never happens during routine testing,
    tying these two specifically to the "all" variant would leave the watcher stuck on the very first
    suite that doesn't happen to restart SM - "all " is instead optional in the match (RangeJob's
    `optional_all`), so either form counts.

  - Not every checkpoint pair fires for every kind of test run - the benchmark plan's own chapters
    exercise different, non-overlapping subsets of this table. "Install new/cached deployable items"
    drives Download/Install/Prepare/Start network/Start instances/Total; nothing is ever torn down as
    part of that run, so Init SM/Stop network/Stop instances/Release SM never fire. "Start/stop
    already installed instances" drives the opposite set - Init SM/Start network/Start
    instances/Stop network/Stop instances/Release SM, all part of the same stop-then-start cycle - but
    nothing is being installed, so Download/Install/Prepare never fire. Waiting indefinitely for a
    checkpoint pair that a given run simply never produces would mean this watcher never gets past
    that run's own suite, no matter how long it waits. Confirmed empirically: restarting aos.target
    with nothing installed left the watcher stuck on Download forever, since only Init SM/Release SM
    used to have a bounded wait. So every RangeJob metric (see BOUNDED_LABELS) instead gets a bounded
    wait (BOUNDED_WAIT_SECONDS) - reported as n/a once it runs out, same as a one-shot report used to
    do for everything - while "Total" (the checkpoint that defines the suite in the first place, and
    so always present by the time a suite is even detected - see wait_for_new_suite()) still waits
    indefinitely.

  - A freshly ingested sample isn't necessarily visible to a query right away: VictoriaMetrics holds
    back the most recent ~30s of data by default (-search.latencyOffset), and separately caches
    query responses by query string. Every lookup here passes nocache=1; BOUNDED_WAIT_SECONDS is
    wide enough to comfortably clear this for every metric that does apply to the run being watched -
    see "Continuous watching" below.

  - Every lookup here is "the latest occurrence of X", with no inherent notion of which suite it
    belongs to - which is fine as long as nothing else recent enough to still be visible could be
    mistaken for this suite's own occurrence. Two suites close together break that assumption two
    different ways, both confirmed empirically against a live watch session:

      - A suite that never produces a given checkpoint can pick up a *previous* suite's own
        occurrence of it instead of correctly reporting n/a: back-to-back suites (a real deployment
        immediately followed by a plain aos.target restart, ~90s apart) left the second suite's
        Download/Install/Prepare/Total showing the *first* suite's own values (Total inflated to 80+
        seconds), because the first suite's checkpoints were still well within VictoriaMetrics'
        staleness window when the second suite's lookups ran, unconstrained by any lower bound.

      - The opposite leak is just as real: with only a lower bound added, a suite still being
        resolved (e.g. waiting out BOUNDED_WAIT_SECONDS for a checkpoint that doesn't apply) can pick
        up a checkpoint that belongs to whatever *next* suite starts before this one finishes -
        confirmed empirically: a suite's own "Init SM" resolved to a plausible-looking value that
        actually belonged to the restart triggered *after* it, purely because that restart's own
        "Starting AosCore Service Manager..." was the only occurrence more recent than the lower
        bound by the time the query ran, with nothing capping how far forward it could reach.

    So every lookup is constrained on both sides: `not_before_us`, the previous suite's own end
    (None for the very first suite of a watch session, since there is no earlier suite to exclude),
    and `suite_end_us`, *this* suite's own end - exactly the value wait_for_new_suite() already found
    to detect it - used as the query's own evaluation time. Every checkpoint this script measures
    necessarily happens at or before "the last instance's own Start" within its own suite (see the
    checkpoint table above - nothing in either chapter's chain happens after the last instance
    starts), so bounding every lookup to at-or-before suite_end_us never excludes a checkpoint that
    legitimately belongs to the suite being resolved.

Continuous watching: this runs forever, one test suite at a time, until Ctrl+C. Each iteration
first waits for a newer "Total" end (the last instance's own Start) than the previous suite it
reported on - i.e. a new deployment run - then resolves every metric for that specific suite exactly
as a one-shot report would. Every RangeJob metric still missing after BOUNDED_WAIT_SECONDS is reported
as n/a (see above) rather than blocking the suite forever; "Total" itself keeps getting polled (every
POLL_INTERVAL_SECONDS) with no bound. Each suite gets its own numbered "=== Test suite N ===" report,
so a long-running watch session can be scrolled back through to see every run it caught.

"Start instances" is reported twice: once as the single number above (AosCore's own "Start
instances begin"/"end" bracket around the whole batch), and again broken down per instance - each
instance's own elapsed time from that same "Start instances begin" to its own "Instance ... Start"
checkpoint (the same one "Total"'s instance list comes from) - printed after the "Instances started"
summary line, one row per instance.

--publish: besides printing the report, also pushes every resolved metric back to VictoriaMetrics as
a benchmark_result sample (the same metric event_exporter.py's/benchmark-timing's own push_result()
produce), so it shows up in Grafana's "Benchmark Results" table alongside every other benchmark's
results - see publish_results(). Off by default: this script's normal job is read-only reporting,
writing to the shared store is opt-in.

Usage:
    report_timing.py [--victoria-url http://victoriametrics:8428] [--publish]
"""

import argparse
import datetime
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_VICTORIA_URL = "http://10.0.0.100:8428"

# "node" label on every sample this pushes (--publish) - matches benchmark-timing's own cNode:
# there's no mechanism yet for an instance to learn which node it's actually running on, so it's
# always "main".
NODE = "main"

AOS_CM_SERVICE = "aos-cm.service"
AOS_SM_SERVICE = "aos-sm.service"
INIT_SCOPE = "init.scope"

START_INSTANCES_LABEL = "Start instances"
STOP_INSTANCES_LABEL = "Stop instances"
START_INSTANCES_BEGIN_EVENT = "Start instances begin"
INIT_SM_LABEL = "Init SM"
RELEASE_SM_LABEL = "Release SM"

# Keyword dicts, not positional tuples, since only some entries need `nearest_end`/`optional_all`
# (both default to False - see RangeJob) and positional args would make that error-prone to read.
CHECKPOINT_RANGES = (
    {
        "label": "Download",
        "start_source": AOS_CM_SERVICE,
        "start_event": "Download update items start",
        "end_source": AOS_CM_SERVICE,
        "end_event": "Download update items end",
    },
    {
        "label": "Install",
        "start_source": AOS_SM_SERVICE,
        "start_event": "Install items begin",
        "end_source": AOS_SM_SERVICE,
        "end_event": "Install items end",
    },
    {
        "label": "Prepare",
        "start_source": AOS_SM_SERVICE,
        "start_event": "Prepare instances begin",
        "end_source": AOS_SM_SERVICE,
        "end_event": "Prepare instances end",
    },
    {
        "label": INIT_SM_LABEL,
        "start_source": INIT_SCOPE,
        "start_event": "Starting AosCore Service Manager...",
        "end_source": AOS_SM_SERVICE,
        "end_event": "Update instances begin",
        "nearest_end": True,
    },
    {
        "label": "Start network",
        "start_source": AOS_SM_SERVICE,
        "start_event": "Start networks begin",
        "end_source": AOS_SM_SERVICE,
        "end_event": "Start networks end",
    },
    {
        "label": START_INSTANCES_LABEL,
        "start_source": AOS_SM_SERVICE,
        "start_event": START_INSTANCES_BEGIN_EVENT,
        "end_source": AOS_SM_SERVICE,
        "end_event": "Start instances end",
    },
    {
        "label": "Stop network",
        "start_source": AOS_SM_SERVICE,
        "start_event": "Stop all networks begin",
        "end_source": AOS_SM_SERVICE,
        "end_event": "Stop all networks end",
        "optional_all": True,
    },
    {
        "label": STOP_INSTANCES_LABEL,
        "start_source": AOS_SM_SERVICE,
        "start_event": "Stop all instances begin",
        "end_source": AOS_SM_SERVICE,
        "end_event": "Stop all instances end",
        "optional_all": True,
    },
    {
        "label": RELEASE_SM_LABEL,
        "start_source": AOS_SM_SERVICE,
        "start_event": "Stop all networks end",
        "end_source": INIT_SCOPE,
        "end_event": "Stopped AosCore Service Manager.",
    },
)

# VictoriaMetrics' own default instant-query lookback (how far back from a given evaluation time it
# considers a sample still "current") - matches the window RangeJob.nearest_end relies on to see
# every occurrence between the rare start event and the earliest end event after it.
INSTANT_QUERY_LOOKBACK_US = 5 * 60 * 1_000_000

TOTAL_START_SOURCE = AOS_CM_SERVICE
TOTAL_START_EVENT = "Process desired status"
INSTANCE_START_QUERY = 'checkpoint_event{source=~"^Instance: .*",event="Start"}'

# How often an unresolved metric or an not-yet-started suite is re-polled - see resolve_suite() and
# wait_for_new_suite(). There is no attempt limit for most metrics: this script waits until Ctrl+C
# instead - except BOUNDED_LABELS, see below.
POLL_INTERVAL_SECONDS = 5

# Every RangeJob metric label, plus "Total" - resolve_suite() gives up on each of these (reporting
# n/a, same as a one-shot report used to do for everything) after BOUNDED_WAIT_SECONDS rather than
# waiting forever, since any of them can be genuinely absent depending on which kind of run is being
# watched - see the module docstring's checkpoint-pair-applicability note. "Total" needs "Process
# desired status", which - like Download/Install/Prepare - only fires for a genuine desired-status
# deployment, not a plain aos.target restart, so it needs the same bounded treatment (confirmed
# empirically: a pure restart otherwise left resolve_suite() stuck on "Total" even after the
# Download/Install/Prepare fix below). What does keep resolve_suite() waiting indefinitely is
# TotalJob's `end_us` - the last instance's own Start, the checkpoint that defines the suite in the
# first place (see wait_for_new_suite()) - which is always present by the time resolve_suite() starts
# and isn't gated by BOUNDED_LABELS at all, see TotalJob. BOUNDED_WAIT_SECONDS is wide enough to
# comfortably clear VictoriaMetrics' default ~30s -search.latencyOffset.
BOUNDED_LABELS = {range_def["label"] for range_def in CHECKPOINT_RANGES} | {"Total"}
BOUNDED_WAIT_SECONDS = 40


def parse_args():
    """Parse --victoria-url and --publish."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--victoria-url",
        default=DEFAULT_VICTORIA_URL,
        help="VictoriaMetrics base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="also push every resolved metric to VictoriaMetrics as a benchmark_result sample, so "
        'it shows up in Grafana\'s "Benchmark Results" table (default: report-only, nothing written)',
    )
    return parser.parse_args()


def parse_time_us(text):
    """Parses a "YYYY-MM-DD HH:MM:SS.ffffff" UTC "time_us" label back into a microsecond epoch
    timestamp."""
    dt = datetime.datetime.strptime(text, "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=datetime.timezone.utc)
    return int(dt.timestamp()) * 1_000_000 + dt.microsecond


def format_time_us(us):
    """Formats a microsecond epoch timestamp as "HH:MM:SS.fff" (UTC), for display."""
    seconds, microseconds = divmod(us, 1_000_000)
    dt = datetime.datetime.fromtimestamp(seconds, tz=datetime.timezone.utc)
    dt += datetime.timedelta(microseconds=microseconds)
    return dt.strftime("%H:%M:%S.%f")[:-3]


# RE2 (VictoriaMetrics' regex engine) metacharacters that can appear in one of our own checkpoint
# texts - notably not whitespace, unlike Python's re.escape(), which also escapes it (for its own
# VERBOSE-mode support) - RE2 doesn't accept "\ " as an escape sequence and rejects the whole query
# with "invalid regex" (confirmed empirically) if it's used here.
_RE2_METACHARS = re.compile(r"([.^$|()\[\]{}*+?\\])")


def _escape_regex_literal(text):
    """Escapes RE2 metacharacters in `text` for safe embedding in a PromQL regex - see
    _RE2_METACHARS. The result still needs escape_promql_string() before it can be embedded in a
    quoted PromQL string literal - the two are separate layers."""
    return _RE2_METACHARS.sub(r"\\\1", text)


def escape_promql_string(text):
    """Escapes `text` for safe embedding inside a double-quoted PromQL string literal. This is a
    separate layer from _escape_regex_literal(): a backslash meant for the regex engine (e.g. from
    "\\." there) has to arrive at the regex engine as a single backslash, which means it has to be
    written as two backslashes in the PromQL source text - confirmed empirically: a single backslash
    made VictoriaMetrics reject the whole query as an invalid string literal, before the regex
    engine ever saw it."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


STOP_ALL_PREFIX = "Stop all "


def event_query(source, event):
    """A PromQL selector matching a checkpoint_event whose event is exactly `event`, or `event`
    followed by AosCore's own ": key=value, ..." detail suffix - see the module docstring. `event`
    is regex-escaped first, since some checkpoint texts contain regex metacharacters of their own
    (e.g. the literal "..." in "Starting AosCore Service Manager..."), then PromQL-string-escaped -
    see escape_promql_string()."""
    pattern = escape_promql_string(_escape_regex_literal(event))
    return f'checkpoint_event{{source="{escape_promql_string(source)}",event=~"^{pattern}(:.*)?$"}}'


def query_metrics(victoria_url, promql, at_time_us=None):
    """Runs an instant PromQL query against VictoriaMetrics and returns every matching sample's full
    label set (a dict, as VictoriaMetrics' own "metric" field). Returns [] if nothing matches or the
    query fails."""
    params = {
        "query": promql,
        # Without this, VictoriaMetrics can serve a cached response from an earlier attempt at the
        # same query string, masking a checkpoint that has since become visible.
        "nocache": "1",
    }
    if at_time_us is not None:
        params["time"] = f"{at_time_us / 1_000_000:.6f}"

    url = f"{victoria_url.rstrip('/')}/api/v1/query?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, method="GET")

    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read())
    except (urllib.error.URLError, json.JSONDecodeError) as err:
        print(f"failed to query VictoriaMetrics: {err}", file=sys.stderr)
        return []

    return [entry.get("metric", {}) for entry in body.get("data", {}).get("result", [])]


def query_samples_us(victoria_url, promql, at_time_us=None, not_before_us=None):
    """Every query_metrics() match's precise timestamp - read from its "time_us" label rather than
    the sample's own millisecond-precision timestamp - excluding any at or before `not_before_us`,
    if given (see the module docstring's stale-carryover note)."""
    samples = []
    for metric in query_metrics(victoria_url, promql, at_time_us):
        time_us_label = metric.get("time_us")
        if time_us_label:
            us = parse_time_us(time_us_label)
            if not_before_us is None or us > not_before_us:
                samples.append(us)

    return samples


def query_latest_us(victoria_url, promql, at_time_us=None, not_before_us=None):
    """The latest of query_samples_us()'s results, or None if it returned none."""
    samples = query_samples_us(victoria_url, promql, at_time_us, not_before_us)
    return max(samples) if samples else None


def query_instance_samples(victoria_url, at_time_us=None):
    """Like query_samples_us(), but for INSTANCE_START_QUERY specifically, returning (instance_id,
    time_us) pairs - the "source" label with the "Instance: " prefix stripped, since that's the
    per-instance breakdown "Start instances" needs (see resolve_suite())."""
    samples = []
    for metric in query_metrics(victoria_url, INSTANCE_START_QUERY, at_time_us):
        time_us_label = metric.get("time_us")
        if not time_us_label:
            continue

        source = metric.get("source", "")
        prefix = "Instance: "
        instance_id = source[len(prefix) :] if source.startswith(prefix) else source
        samples.append((instance_id, parse_time_us(time_us_label)))

    return samples


class RangeJob:
    """Resolves one of CHECKPOINT_RANGES' elapsed times into results[label]."""

    def __init__(
        self, label, start_source, start_event, end_source, end_event, nearest_end=False, optional_all=False
    ):
        self.label = label
        self.start_source = start_source
        self.start_event = start_event
        self.end_source = end_source
        self.end_event = end_event
        # If True, `end_event` can recur many times after `start_event` (e.g. a deployment-cycle
        # checkpoint following a rare service restart) - the plain latest occurrence could belong to
        # a much later, unrelated cycle, so the end is instead resolved as the earliest occurrence at
        # or after `start_event`'s own timestamp. See the module docstring's "Init SM" note.
        self.nearest_end = nearest_end
        # If True, `start_event`/`end_event` (which must start with STOP_ALL_PREFIX) are tried as
        # literally logged first, falling back to the "all "-less routine-teardown variant only if
        # that pair is never found - see try_resolve() and the module docstring's "Stop
        # network"/"Stop instances" note.
        self.optional_all = optional_all
        self.start_us = None
        self.end_us = None

    def try_resolve(self, victoria_url, results, not_before_us, suite_end_us):
        """Attempts to fill in whichever of this range's two ends is still missing. Every lookup is
        scoped to this suite specifically: `not_before_us` excludes any candidate at or before the
        previous suite's own end, and `suite_end_us` (this suite's own end - see
        wait_for_new_suite()) is used as the query's own evaluation time, so a candidate belonging to
        whatever *next* suite happens to have started by the time this call runs can't be mistaken
        for this one's either - every checkpoint this script measures necessarily happens at or
        before "the last instance's own Start" within its own suite, so this is never too tight - see
        the module docstring's stale-carryover note. Returns True (and records the elapsed time into
        `results`) once both ends are found."""
        if self.optional_all:
            self._try_resolve_optional_all(victoria_url, not_before_us, suite_end_us)
        else:
            self._try_resolve_plain(victoria_url, not_before_us, suite_end_us)

        if self.start_us is None or self.end_us is None:
            return False

        results[self.label] = (self.end_us - self.start_us) / 1_000_000
        return True

    def _try_resolve_plain(self, victoria_url, not_before_us, suite_end_us):
        """Resolves self.start_us/self.end_us for the common case: independently-cached lookups, no
        variant to choose between - see try_resolve()."""
        if self.start_us is None:
            self.start_us = query_latest_us(
                victoria_url,
                event_query(self.start_source, self.start_event),
                at_time_us=suite_end_us,
                not_before_us=not_before_us,
            )

        if self.start_us is None:
            return

        if self.end_us is None:
            if self.nearest_end:
                # Querying at start_us + the lookback window makes VictoriaMetrics consider every
                # occurrence from start_us up to that point "current" and return all of them (see
                # query_samples_us) - then the earliest of those at or after start_us is this range's
                # actual end, not whichever happens to be the most recent right now. Already
                # necessarily after not_before_us, since it's after self.start_us, which is - no
                # separate not_before_us filtering needed here.
                candidates = query_samples_us(
                    victoria_url,
                    event_query(self.end_source, self.end_event),
                    at_time_us=self.start_us + INSTANT_QUERY_LOOKBACK_US,
                )
                candidates = [us for us in candidates if self.start_us <= us <= suite_end_us]
                self.end_us = min(candidates) if candidates else None
            else:
                self.end_us = query_latest_us(
                    victoria_url,
                    event_query(self.end_source, self.end_event),
                    at_time_us=suite_end_us,
                    not_before_us=not_before_us,
                )

    def _try_resolve_optional_all(self, victoria_url, not_before_us, suite_end_us):
        """Resolves self.start_us/self.end_us for optional_all: AosCore logs the same teardown two
        ways - "Stop all X begin/end" on an actual full SM shutdown/restart, or just "Stop X
        begin/end" (no "all") for a routine per-update teardown of only what's actually being
        replaced. Both variants can appear in the very same suite (e.g. a full aos.target restart
        logs the real "Stop all ..." teardown, and the freshly-started SM then also logs its own
        "Stop ...: count=0" no-op immediately before starting back up) - matching "whichever variant
        is latest" would then pick the meaningless no-op over the real teardown it's actually meant
        to measure. So the "all" pair is tried first and preferred whenever found (matching the
        guide's own checkpoint table); the "all "-less pair is only used as a fallback, for a run
        that never produces a "Stop all ..." at all (confirmed empirically: this is what an ordinary
        routine deployment does). Unlike _try_resolve_plain(), start_us/end_us are cached together as
        one pair, not independently, since mixing an "all" start with a "no all" end (or vice versa)
        would silently measure nothing meaningful."""
        if self.start_us is not None and self.end_us is not None:
            return

        def latest(source, event):
            return query_latest_us(
                victoria_url, event_query(source, event), at_time_us=suite_end_us, not_before_us=not_before_us
            )

        start_us = latest(self.start_source, self.start_event)
        end_us = latest(self.end_source, self.end_event) if start_us is not None else None

        if start_us is None or end_us is None:
            rest_start = self.start_event[len(STOP_ALL_PREFIX) :]
            rest_end = self.end_event[len(STOP_ALL_PREFIX) :]
            start_us = latest(self.start_source, "Stop " + rest_start)
            end_us = latest(self.end_source, "Stop " + rest_end) if start_us is not None else None

        if start_us is not None and end_us is not None:
            self.start_us, self.end_us = start_us, end_us


class TotalJob:
    """Resolves "Total" into results["Total"]. `end_us` - the last instance's own Start, i.e. the
    checkpoint that defines a suite's boundary - is the exact value wait_for_new_suite() already
    found to detect this suite in the first place, given directly at construction rather than
    re-queried here: independently re-querying "latest instance Start" partway through resolving a
    suite risks picking up a *newer* one that has started in the meantime (the very
    next-suite-leaking-into-this-one problem RangeJob.try_resolve()'s `suite_end_us` also guards
    against - see the module docstring's stale-carryover note), which would silently attribute this
    suite's "Total" to instances that don't belong to it. "Total" needs "Process desired status" too
    (see the module docstring for why it needs the at-or-before constraint below), which - unlike
    `end_us` - is one of BOUNDED_LABELS, since it only fires for a genuine desired-status deployment,
    not a plain aos.target restart.

    Which instances made up this suite - as (instance_id, start_time_us) pairs, sorted by start time
    - is resolved separately, by resolve_suite() itself once "Start instances" (or its own bounded
    n/a) is known, rather than here: that breakdown only needs `end_us`, not "Process desired
    status", so tying it to this job would leave it just as stuck on "Total" as everything else used
    to be."""

    def __init__(self, end_us):
        self.label = "Total"
        self.end_us = end_us

    def try_resolve(self, victoria_url, results, not_before_us, suite_end_us):
        # suite_end_us is unused here (self.end_us already *is* that value) - kept only so
        # resolve_suite() can call every pending job, RangeJob or TotalJob, the same way.
        start_us = query_latest_us(
            victoria_url,
            event_query(TOTAL_START_SOURCE, TOTAL_START_EVENT),
            at_time_us=self.end_us,
            not_before_us=not_before_us,
        )

        if start_us is None:
            return False

        results["Total"] = (self.end_us - start_us) / 1_000_000
        return True


def wait_for_new_suite(victoria_url, last_end_us):
    """Polls (every POLL_INTERVAL_SECONDS, forever) until a newer "Total" end - the last instance's
    own Start - shows up than `last_end_us` (None on the very first call, meaning "whatever's
    current right now"), then returns it. This is what turns this script from a one-shot report into
    a continuous watcher - see the module docstring's "Continuous watching" section."""
    while True:
        end_us = query_latest_us(victoria_url, INSTANCE_START_QUERY, not_before_us=last_end_us)
        if end_us is not None:
            return end_us

        time.sleep(POLL_INTERVAL_SECONDS)


def resolve_suite(victoria_url, not_before_us, suite_end_us):
    """Resolves every metric for one test suite - waiting indefinitely (polling every
    POLL_INTERVAL_SECONDS) for whichever checkpoints haven't shown up yet, with no attempt limit,
    except BOUNDED_LABELS (see the module docstring's "Init SM"/"Release SM" note), which are
    reported as n/a instead once BOUNDED_WAIT_SECONDS passes. A metric outside BOUNDED_LABELS that
    AosCore genuinely never logs for a given run (e.g. "Download" when nothing needed downloading)
    still leaves this waiting forever for it - interrupt with Ctrl+C if that happens.

    `suite_end_us` - this suite's own end, i.e. exactly what wait_for_new_suite() just returned - and
    `not_before_us` - the *previous* suite's own end (None for the very first suite of a watch
    session) - together scope every lookup to this suite specifically, so neither a checkpoint left
    over from an earlier suite nor one belonging to whatever suite comes *next* (started while this
    one was still being resolved) can be mistaken for this one's - see the module docstring's
    stale-carryover note. Returns (results, range_jobs) - main() already knows suite_end_us to tell
    this suite apart from the next one wait_for_new_suite() finds, so it isn't returned again."""
    results = {}
    range_jobs = [RangeJob(**range_def) for range_def in CHECKPOINT_RANGES]
    total_job = TotalJob(suite_end_us)
    pending = list(range_jobs) + [total_job]

    started_at = time.monotonic()

    while pending:
        pending = [
            job for job in pending if not job.try_resolve(victoria_url, results, not_before_us, suite_end_us)
        ]

        if pending and time.monotonic() - started_at > BOUNDED_WAIT_SECONDS:
            pending = [job for job in pending if job.label not in BOUNDED_LABELS]

        if pending:
            time.sleep(POLL_INTERVAL_SECONDS)

    # Which instances made up this suite - (instance_id, start_time_us) pairs, sorted by start time -
    # and "Start instances" as a per-instance breakdown, alongside the single number for the whole
    # batch already in results["Start instances"]: each instance's own duration from when AosCore
    # began starting instances to when that particular instance reported its own Start checkpoint.
    # Needs only start_instances_job.start_us ("Start instances begin") and suite_end_us, not "Total"
    # itself, so this still works even on a run where "Total" doesn't apply (e.g. a plain aos.target
    # restart, no desired-status deployment involved) - see the module docstring.
    start_instances_job = next(job for job in range_jobs if job.label == START_INSTANCES_LABEL)

    if start_instances_job.start_us is not None:
        # Only the instances that belong to this suite, not a stale one still inside VictoriaMetrics'
        # lookback window from an earlier run.
        instances = query_instance_samples(victoria_url, at_time_us=suite_end_us)
        results["instances"] = sorted(
            ((iid, us) for iid, us in instances if us >= start_instances_job.start_us),
            key=lambda pair: pair[1],
        )
        results["instance_start_durations"] = [
            (instance_id, (instance_us - start_instances_job.start_us) / 1_000_000)
            for instance_id, instance_us in results["instances"]
        ]

    return results, range_jobs


def print_report(results):
    """Prints the resolved elapsed times as a table, a one-line summary of which instances made up
    "Total", and - if "Start instances" and its per-instance breakdown both resolved - each
    instance's own start duration, so "Start instances" is available both as a single number for
    the whole batch and broken down per instance, per the instances' own "Instance ... Start"
    checkpoints (see resolve_suite())."""
    labels = [range_def["label"] for range_def in CHECKPOINT_RANGES] + ["Total"]
    label_width = max(len(label) for label in labels)

    print()
    for label in labels:
        value = results.get(label)
        value_text = f"{value:8.3f} s" if value is not None else "     n/a"
        print(f"  {label:<{label_width}}  {value_text}")
    print()

    instances = results.get("instances") or []
    if instances:
        first_us = instances[0][1]
        last_us = instances[-1][1]
        spread_s = (last_us - first_us) / 1_000_000
        print(
            f"Instances started: {len(instances)} "
            f"(first {format_time_us(first_us)}, last {format_time_us(last_us)}, spread {spread_s:.3f} s)"
        )
    else:
        print("Instances started: none found")

    durations = results.get("instance_start_durations")
    if durations:
        print()
        print("Start instances, per instance:")
        id_width = max(len(instance_id) for instance_id, _ in durations)
        for instance_id, duration_s in durations:
            print(f"  {instance_id:<{id_width}}  {duration_s:8.3f} s")


def escape_label_value(value):
    """Escape a string for safe embedding inside a Prometheus exposition-format label value - same
    escaping event_exporter.py's and benchmark-timing's own escape_label_value()/EscapeLabelValue()
    use."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def format_precise_time(timestamp_us):
    """Formats a microsecond epoch timestamp as "YYYY-MM-DD HH:MM:SS.ffffff" (UTC) - the "time_us"
    label's own format, not format_time_us()'s truncated display one."""
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


def push_result(victoria_url, source, name, value):
    """Push a single benchmark_result sample for one measured value - the metric event_exporter.py's
    and benchmark-timing's own push_result()/PushResult() produce."""
    timestamp_us = int(time.time() * 1_000_000)
    labels = ",".join(
        f'{label}="{escape_label_value(text)}"'
        for label, text in (
            ("node", NODE),
            ("source", source),
            ("name", name),
            ("time_us", format_precise_time(timestamp_us)),
        )
    )
    time_s = timestamp_us / 1_000_000
    push_line(victoria_url, f"benchmark_result{{{labels}}} {value:.6f} {time_s:.3f}")


def metric_name(label):
    """Turns a display label like "Start network" into a benchmark_result "name" like
    "start_network_s" - matching the "*_s" convention event_exporter.py's and benchmark-timing's own
    push_result() already used for these same measurements."""
    return label.lower().replace(" ", "_") + "_s"


def publish_results(victoria_url, range_jobs, results):
    """Pushes every resolved metric as a benchmark_result sample (see push_result()) - --publish
    only, see the module docstring. Each RangeJob metric is pushed with its own start_source as
    "source" (e.g. "aos-cm.service" for "Download"), "Total" with TOTAL_START_SOURCE, and each
    per-instance "Start instances" duration (if resolved) with that instance's own "Instance: <id>"
    as source, same as benchmark-timing's own instance-scoped pushes."""
    for job in range_jobs:
        value = results.get(job.label)
        if value is not None:
            push_result(victoria_url, job.start_source, metric_name(job.label), value)

    total = results.get("Total")
    if total is not None:
        push_result(victoria_url, TOTAL_START_SOURCE, metric_name("Total"), total)

    for instance_id, duration_s in results.get("instance_start_durations", []):
        push_result(victoria_url, f"Instance: {instance_id}", metric_name(START_INSTANCES_LABEL), duration_s)


def main():
    args = parse_args()

    print(f"Watching {args.victoria_url} for new test suites - press Ctrl+C to stop.")

    suite_number = 0
    last_end_us = None

    try:
        while True:
            suite_end_us = wait_for_new_suite(args.victoria_url, last_end_us)

            suite_number += 1
            print(f"\n=== Test suite {suite_number} ===")

            results, range_jobs = resolve_suite(args.victoria_url, last_end_us, suite_end_us)
            last_end_us = suite_end_us

            print_report(results)

            if args.publish:
                publish_results(args.victoria_url, range_jobs, results)
    except KeyboardInterrupt:
        print(f"\nStopped after {suite_number} test suite(s).")


if __name__ == "__main__":
    main()
