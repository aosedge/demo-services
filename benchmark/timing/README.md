# Timing benchmark services

Purpose: This benchmark measures AosCore lifecycle timing under different service workloads,
including service deployment, preparation, network setup, and instance start/stop operations.

Each generated service is an independent AosEdge service running `benchmark-timing`,
a C++ binary that sends a `checkpoint_event (event="Start")` to VictoriaMetrics
as soon as the instance starts.
This event is correlated with AosCore's own lifecycle checkpoints to measure timing across a batch of services.

Benchmark workload can be varied by:

- number of services (`--num-services`)
- instances per service (`--num-instances`)
- payload size per service (`--data-size`)

A typical test flow is:

build the benchmark binary → generate services → start `report_timing.py` → execute the Operational Speed benchmark procedure

## Building

Build the `benchmark-timing` binary with the SDK toolchain, using the shared `build.sh` at the repo root. `config.yaml.in`
declares both an `amd64` and an `arm64` image for every service, so the binary must be built for both architectures,
each with its own toolchain, before `copy_images.py` is run:

```sh
../../build.sh . --toolchain=<amd64-toolchain-path> --arch=amd64
../../build.sh . --toolchain=<arm64-toolchain-path> --arch=arm64
```

`--toolchain` is optional; if omitted, the current environment's toolchain is used. `--arch` defaults to `amd64`. Each
run adds `output/<arch>/benchmark-timing`, so after both runs `output/amd64/benchmark-timing` and
`output/arm64/benchmark-timing` are in place for `copy_images.py` to pick up.

## Generating services

Two steps, both from the shared `benchmark/scripts`.

First, copy each built architecture's output into a numbered folder per service:

```sh
../scripts/copy_images.py --num-services N [--data-size MiB]
```

- `--num-services` - number of service folders to create
- `--data-size` - optional; size (in MiB) of a `test.dat` file generated for each service (default: no `test.dat`)

This copies every architecture subfolder found under `output/` into `services/<service_id>/<arch>/` (default
`--dest-dir`) for each service ID from 1 to `--num-services` (clearing `--dest-dir` first if it already exists),
adding a `test.dat` file with `--data-size` MiB of random payload to each copy if `--data-size` was given -
`config.yaml.in`'s `sourceFolder` entries point at this same `services/@SERVICE_ID@/<arch>` path.

Then render `config.yaml` from `config.yaml.in`:

```sh
../scripts/create_services.py --num-services N [--num-instances N] [--version VERSION]
```

- `--num-services` - number of services to generate (must match what was passed to `copy_images.py`)
- `--num-instances` - optional; `minInstances` set for each service (default: `1`)
- `--version` - optional; `version` set for each service (default: `1.0.0-beta.1`)

This renders one `items` entry per generated service, with `@SERVICE_ID@`, `@NUM_INSTANCES@` and
`@VERSION@` substituted, ready to publish with `aos-sp`.

## Measuring results

[Operational Speed](https://github.com/aosedge/meta-aos/blob/main/doc/benchmark_execution.md#operational-speed) chapter defines each timing metric (Download, Install, Prepare,
Init SM, Start/Stop network, Start/Stop instances, Release SM, Total) as the elapsed time between a pair of
`checkpoint_event` samples in VictoriaMetrics - mostly AosCore's own log checkpoints (pushed by
`event_exporter.py`), except "Total" and the per-instance "Start instances" breakdown, which also need this
service's own instance-start checkpoint (pushed by `benchmark-timing` itself, see above). Reading those timestamps
out of Grafana's Events view and subtracting them by hand for every metric, every instance count, is what
`../scripts/report_timing.py` automates.

Run it from `benchmark/scripts` **before** starting the execution steps in `doc/benchmark_execution.md` (deploying
new items, or stopping/starting `aos.target`), so it's already watching when the checkpoints it needs start showing
up:

```sh
../scripts/report_timing.py [--victoria-url http://victoriametrics:8428] [--publish]
```

- `--victoria-url` - defaults to the unit's own VictoriaMetrics (`http://10.0.0.100:8428`)
- `--publish` - also pushes every resolved metric back to VictoriaMetrics as a `benchmark_result` sample, so it
  shows up in Grafana's "Benchmark Results" table alongside every other benchmark's results; off by default, since
  the script's normal job is read-only reporting

It runs indefinitely, one test suite (one execution of the guide's steps) at a time, until Ctrl+C: each time a new
suite's checkpoints appear, it prints a numbered `=== Test suite N ===` report with every metric that applies to
that suite (a plain `aos.target` restart never produces Download/Install/Prepare/Total, for instance; a fresh
install never produces Init SM/Release SM - see the metric tables in `doc/benchmark_execution.md`), reporting `n/a`
for whichever don't, plus a per-instance breakdown of "Start instances". So the same running instance of the script
can be left watching across the guide's whole "repeat from step 2 for 8 and 16 services" loop, catching each run's
report as it happens rather than being invoked fresh per data point. See the script's own module docstring for the
exact checkpoint pairs and the edge cases it accounts for (suffixed checkpoint text, rare-vs-frequent event
pairing, stale/future checkpoint leakage between suites close together in time).

Example output for one suite (a fresh install, 3 instances - "Init SM"/"Release SM" are `n/a` since nothing
restarted AosCore Service Manager for this run):

```text
Watching http://10.0.0.100:8428 for new test suites - press Ctrl+C to stop.

=== Test suite 1 ===

  Download            2.123 s
  Install             0.094 s
  Prepare             0.123 s
  Init SM               n/a
  Start network       0.137 s
  Start instances     0.015 s
  Stop network        0.043 s
  Stop instances      0.017 s
  Release SM            n/a
  Total               2.824 s

Instances started: 3 (first 19:05:27.729, last 19:05:27.730, spread 0.001 s)

Start instances, per instance:
  90397562-3500-3bf3-b2e1-333f51e60e6c     0.019 s
  daa792b1-6d97-3374-9067-4302e0b740fe     0.020 s
  57655bc9-4c5d-337d-95ad-812a27c5eb64     0.020 s
```
