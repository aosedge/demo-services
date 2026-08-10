# Load test

Demo services used to evaluate how an AosEdge unit starts and stops a large number of service instances at once —
i.e. how well the unit handles bulk instance start/stop rather than any single instance's own workload.

## What it does

Each instance runs a minimal C binary that:

- on start, prints its Aos identity (item ID, subject ID, instance index, instance ID) read from environment variables,
  together with a timestamp — useful for measuring instance startup delay;
- every 10 seconds, logs a heartbeat line so a running instance can be told apart from one that crashed or never
  started.

`config.yaml` deploys 16 separate service items (`demo-load-test-service-1` … `demo-load-test-service-16`), each
requesting 16 instances. The last one requests only 12, for a total of 252 instances — AosCore allows up to 256
instances by default, and a two-node multinode setup already has 4 preinstalled instances, so 252 is the most that
fits. Each item still sets `quotas` (`cpuLimit: 200`, `ramLimit: 2MiB`) to keep every instance small, but also sets
`skipResourceLimits: true` so the scheduler doesn't check whether each instance fits by RAM/CPU before starting it -
this flag only takes effect for a preinstalled version, which is why every item is published as a beta version
(`1.0.0-beta.1`).

## Build

The service binary must be built before deploying, using the shared `build.sh` at the repo root. It can be built for
a specific Yocto image or built natively:

- For a Yocto image, point `--toolchain` at that image's Yocto SDK environment-setup script:

  ```console
  ../build.sh . --toolchain=/path/to/environment-setup-core2-64-aos-linux
  ```

- For a native build, omit `--toolchain` and the toolchain already available in the current environment is used:

  ```console
  ../build.sh .
  ```

- `--arch=<name>` is optional (defaults to `amd64`, matching `config.yaml`'s `sourceFolder: amd64`).

Build output: `output/<arch>`.

## Deploy

1. Build the binary for every architecture referenced in `config.yaml` (`amd64` by default) — all 16 service items
   reuse the same `output/<arch>` build.
2. Publish the bundle described by `config.yaml` to AosCloud:

   ```console
   aos-signer go
   ```

3. Install the services on the target unit: see
   [Install Service on the Unit](https://docs.aosedge.tech/docs/quick-start/create-subject).

## Test

Set the AosCore components' log level to `info` before testing. At `debug` level, the volume of log output itself
adds delay to instance start/stop, which would skew the timings you are trying to measure.

1. Stop AosCore:

   ```console
   systemctl stop aos.target
   ```

2. Edit the systemd service files for `aos-cm.service`, `aos-sm.service`, and `aos-iam.service` — usually located in
   `/usr/lib/systemd/system`, or in `/usr/local/lib/systemd/system` for a standalone installation. In each file's
   `ExecStart` line, change `-v debug` to `-v info`, for example:

   ```console
   ExecStart=/usr/local/bin/aos_iam_app -c /usr/local/etc/aos/iam.cfg -v info -j
   ```

3. Reload the systemd daemon:

   ```console
   systemctl daemon-reload
   ```

4. AosCore tags the log lines it uses to measure instance start/stop time with `[profiling]`. In a separate
   terminal, follow that tag:

   ```console
   journalctl -f -o short-monotonic | grep "\[profiling\]"
   ```

5. Start AosCore:

   ```console
   systemctl start aos.target
   ```

   With the load test services installed, the terminal running `journalctl` should then print output similar to:

   ```console
   [ 6495.745599] ip-10-231-100-145 aos_sm_app[9281]: (launcher) [profiling] Update instances begin: stopCount=0, startCount=16
   [ 6495.746211] ip-10-231-100-145 aos_sm_app[9281]: (launcher) [profiling] Stop instances begin: count=0
   [ 6495.746314] ip-10-231-100-145 aos_sm_app[9281]: (launcher) [profiling] Stop instances end
   [ 6495.746372] ip-10-231-100-145 aos_sm_app[9281]: (launcher) [profiling] Stop networks begin: count=0
   [ 6495.746417] ip-10-231-100-145 aos_sm_app[9281]: (launcher) [profiling] Stop networks end
   [ 6495.746460] ip-10-231-100-145 aos_sm_app[9281]: (launcher) [profiling] Start networks begin: count=16
   [ 6496.104450] ip-10-231-100-145 aos_sm_app[9281]: (launcher) [profiling] Start networks end
   [ 6496.106487] ip-10-231-100-145 aos_sm_app[9281]: (launcher) [profiling] Start instances begin: count=16
   [ 6496.575789] ip-10-231-100-145 aos_sm_app[9281]: (launcher) [profiling] Start instances end
   [ 6496.576187] ip-10-231-100-145 aos_sm_app[9281]: (launcher) [profiling] Update instances end
   ```

   The monotonic timestamps (first column) let you measure two intervals for the load:

   - `Start networks begin` → `Start networks end` — time spent creating the network for the Aos service instances.
   - `Start instances begin` → `Start instances end` — time spent starting the service instances in their
     containers.

6. Stop AosCore:

   ```console
   systemctl stop aos.target
   ```

   The terminal running `journalctl` should then print output similar to:

   ```console
   [ 6695.371796] ip-10-231-100-145 aos_sm_app[9281]: (launcher) [profiling] Stop all instances begin: count=16
   [ 6695.410052] ip-10-231-100-145 aos_sm_app[9281]: (launcher) [profiling] Stop all instances end
   [ 6695.410112] ip-10-231-100-145 aos_sm_app[9281]: (launcher) [profiling] Stop all networks begin: count=16
   [ 6695.411378] ip-10-231-100-145 aos_sm_app[9281]: (launcher) [profiling] Stop all networks end
   ```

   - `Stop all instances begin` → `Stop all instances end` — time spent stopping all instances.
   - `Stop all networks begin` → `Stop all networks end` — time spent releasing the instances' networks.
