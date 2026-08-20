# Timing benchmark services

These services measure how long AosCore takes to deploy and start a set of instances. Each generated service is an
independent AosEdge service running `benchmark-timing`, a C++ binary that pushes a `checkpoint_event` sample
(`event="Start"`) to VictoriaMetrics as soon as the instance starts, then prints its `AOS_AOS_ITEM_ID`,
`AOS_SUBJECT_ID`, `AOS_INSTANCE_ID` environment variables and its service ID every 10 seconds - the Start event shows
up in the same Grafana Events table/annotations as AosCore's own instance start/stop checkpoints, so it can be used
to measure deployment/start timing across a batch of services. Each service also ships a `test.dat` file of
configurable random-payload size, so the batch can double as a deployment-size benchmark.

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
