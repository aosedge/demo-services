# Disk I/O

Disk I/O throughput/IOPS and latency on the storage volume AosCore mounts into the instance, measured with
[`fio`](https://fio.readthedocs.io/). `doc/benchmark.md`'s "Container disk I/O" chapter describes the plan this
implements.

`benchmark-diskio-service`, backed by `src/diskio_benchmark.py`, runs four fio jobs in a row against one file, in
this order: sequential write, sequential read, random write, random read. Sequential jobs report throughput;
random jobs report IOPS; every job also reports average/p99 latency.

## Generating config.yaml

`config.yaml` is generated from `config.yaml.in`, which templates the values the item is deployed with:

- `@NUM_INSTANCES@` - `minInstances`
- `@VERSION@` - `version`
- `@TEST_DIR@` - the `TEST_DIR` env var

`config.yaml` itself is gitignored; render it with the shared `create_services.py` from `benchmark/scripts`. `--test-dir`
selects which storage backend is exercised (see "Storage backends" below):

```sh
# encrypted (AosCore's own storage volume)
../scripts/create_services.py --num-instances 16 --version 1.0.0-beta.1 --test-dir /storage

# unencrypted (the unit's common-data resource)
../scripts/create_services.py --num-instances 16 --version 1.0.0-beta.2 --test-dir /common
```

`--num-services` is not needed here - it defaults to 1, rendering `config.yaml.in`'s single item once
(`config.yaml.in` has no `@SERVICE_ID@`/`@IMAGES@` placeholders to clone, unlike `benchmark/timing`'s template).

## Storage backends

`config.yaml.in` requests both resources the item may need, so the same image works against either backend -
only `TEST_DIR` (via `--test-dir` above) decides which one is actually read/written. The file itself is always
`TEST_DIR/${AOS_INSTANCE_ID}.dat`, so concurrent instances sharing the same volume never collide on one file:

| Backend    | `TEST_DIR` | Provided by                                     | Encrypted                               |
| :--------- | :--------- | :---------------------------------------------- | :-------------------------------------- |
| `/storage` | `/storage` | `quotas.storageLimit`                           | Yes (LUKS-encrypted `aos` volume group) |
| `/common`  | `/common`  | unit resource `common-data` (`resources:` list) | No (plain unencrypted host partition)   |

`/storage` is the volume AosCore mounts per-instance on the LUKS-encrypted `aos` volume group (see
`meta-aos/recipes-aos/aos-setupdisk`); every AosCore-managed volume (`storages`, `downloads`, `workdirs`, `states`)
lives there, so there is no unencrypted AosCore-managed storage volume. `common-data` is a unit-level resource,
already configured on the benchmark unit, that bind-mounts an unencrypted host directory to `/common` in the
instance - it is what makes the unencrypted comparison possible.

## What is measured

The item runs four jobs in a row against `TEST_DIR/${AOS_INSTANCE_ID}.dat`, each for `RUNTIME` seconds, in this
order. Every job uses `--direct=1` (bypasses the page cache, so the numbers reflect the storage backend AosCore
configures, not host RAM caching) and `--ioengine=libaio`.

| Order | Job                | fio `--rw`  | Block size                           | Queue depth  |
| :---- | :----------------- | :---------- | :----------------------------------- | :----------- |
| 1     | `sequential_write` | `write`     | `1M` (fixed)                         | `16` (fixed) |
| 2     | `sequential_read`  | `read`      | `1M` (fixed)                         | `16` (fixed) |
| 3     | `random_write`     | `randwrite` | `4K` (fixed, per the benchmark plan) | `32` (fixed) |
| 4     | `random_read`      | `randread`  | `4K` (fixed, per the benchmark plan) | `32` (fixed) |

Block size and queue depth are fixed per job, not configurable via the environment.

## Environment

| Variable   | Default    | Meaning                                                                                                    |
| :--------- | :--------- | :--------------------------------------------------------------------------------------------------------- |
| `TEST_DIR` | `/storage` | Directory read/written on the storage volume; the file itself is always `TEST_DIR/${AOS_INSTANCE_ID}.dat`. |
| `SIZE`     | `16M`      | Size of the test file.                                                                                     |
| `RUNTIME`  | `60`       | Length of every single job, in seconds.                                                                    |

`config.yaml` overrides these per item; see its `env` list for the values each is actually deployed with.

## Results

Two destinations, on purpose.

The **log** gets the full result of every job, `fio`'s own JSON document included: that is what makes a failed or
surprising run explicable afterwards, and none of it belongs in a time series.

**VictoriaMetrics** gets only what is worth charting, pushed as `benchmark_result` samples bracketed by
`checkpoint_event` Start/Stop, in the same shape as `services/template/py`. In addition, each job pushes its own
`checkpoint_event` (`event=<job name>`, e.g. `sequential_write`) the moment it begins, so Grafana can mark where
each job started within the run:

| Sample name              | From                                  |
| :----------------------- | :------------------------------------ |
| `<job> throughput, MB/s` | `sequential_write`, `sequential_read` |
| `<job> IOPS`             | `random_write`, `random_read`         |
| `<job> latency avg, ms`  | every job                             |
| `<job> latency p99, ms`  | every job                             |

The `source` label is `Instance: <AOS_INSTANCE_ID>`, which is what tells instances apart once this scenario is run
at scale (concurrent I/O across N instances).

Latency avg/p99 are read from fio's `clat_ns` (completion latency) section, requested via `--lat_percentiles=1`;
this has not yet been verified against a real `fio` JSON output on target, see the `NOTE` in
`src/diskio_benchmark.py`'s `latency_stats()`.
