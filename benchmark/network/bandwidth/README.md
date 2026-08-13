# Bandwidth

Throughput through the container network, measured with [`iperf3`](https://iperf.fr/) for TCP and UDP in both
directions, together with UDP jitter and packet loss. aos_core_cpp's
[Network / Bandwidth](https://github.com/aosedge/aos_core_cpp/blob/main/doc/benchmark_execution.md#bandwidth)
benchmark execution chapter describes the execution steps this implements.

`config.yaml` holds two items: one server and one client. The same client item covers every scenario - which one
runs is a choice of `TARGET`, not different code - so which scenario is measured is decided when `config.yaml` is
generated.

## Generating config.yaml

`config.yaml` is generated from `config.yaml.in`, which templates the values the item is deployed with:

- `@VERSION@` - `version`
- `@NUM_INSTANCES@` - the server's `NUM_INSTANCES` env var and the client's `minInstances`, kept equal so every
  client instance has a server instance to pair with
- `@TEST_HOST@` - the client's `TARGET` env var, i.e. which scenario is measured
- `@UDP_BANDWIDTH@` - the client's `UDP_BANDWIDTH` env var, i.e. the target rate for the UDP tests

`config.yaml` itself is gitignored; render it with the shared `create_services.py` from `benchmark/scripts`.
`--test-host` picks the scenario (see "Setting up each scenario" below); `--udp-bandwidth` defaults to `80M`,
matching `bandwidth_client.py`'s own default (see "Reading the numbers" for why `0`, `iperf3`'s own "unlimited", is
not a good choice here):

```sh
# service -> service
../scripts/create_services.py --num-instances 1 --version 1.0.0-beta.1 --test-host bandwidth-server

# service -> unit
../scripts/create_services.py --num-instances 1 --version 1.0.0-beta.1 --test-host ${NODE_IP}

# service -> external
../scripts/create_services.py --num-instances 1 --version 1.0.0-beta.1 --test-host ${HOST_IP}
```

## What the client measures

Four tests in a row against `TARGET`, each for `DURATION` seconds:

| Test       | `iperf3` arguments        | Direction        |
| ---------- | ------------------------- | ---------------- |
| `tcp_up`   | (none)                    | client -> server |
| `tcp_down` | `-R`                      | server -> client |
| `udp_up`   | `-u -b $UDP_BANDWIDTH`    | client -> server |
| `udp_down` | `-u -b $UDP_BANDWIDTH -R` | server -> client |

`up` is the container sending, `down` is it receiving. Both are worth measuring because the stack shapes the two
directions separately, and because the sending side is the one that saturates a core, which the
`cpu_utilization_percent` section of the log shows directly.

The first test is retried for up to 60 seconds while `iperf3` reports a connection or name resolution failure, so the
run survives being started before its server. Later tests are not retried.

Tests are spaced 3 seconds apart. An `iperf3` server runs one test at a time and needs a moment to reset between
them: starting the next test the instant the previous one ends makes it fail on the control connection, most often
right after an unlimited UDP test, which leaves the server draining its buffers. Measured on a unit, back to back
tests failed in two runs out of three, and none failed with the gap.

After the last test the instance stays alive idling, which keeps its logs available instead of having the unit
restart it in a loop.

## Environment

| Variable        | Default                        | Meaning                                                                                                                       |
| --------------- | ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| `TARGET`        | `--test-host` in `config.yaml` | Server hostname or IP. Required.                                                                                              |
| `DURATION`      | `60`                           | Length of every single test, in seconds.                                                                                      |
| `PORT`          | `5201`                         | Base `iperf3` port; see "Running multiple instances" below.                                                                   |
| `UDP_BANDWIDTH` | `80M`                          | Target rate for the UDP tests (`0` is unlimited, see "Reading the numbers"); `--udp-bandwidth` overrides it in `config.yaml`. |
| `NUM_INSTANCES` | `1`                            | Server item only: how many `iperf3` servers to run. See below.                                                                |

### Running multiple instances

`--num-instances` at generation time sets both the server's `NUM_INSTANCES` and the client's `minInstances` to the
same value, so the two items always scale together.

The server runs `NUM_INSTANCES` `iperf3` servers, one per port from `PORT` up to `PORT + NUM_INSTANCES - 1`,
restarting any of them independently if it dies. Each client instance dials `PORT + AOS_INSTANCE_INDEX`, the
environment variable AosCore sets to this instance's position among the item's instances - so the batch of client
instances spreads across the batch of server instances one-to-one, each pair on its own port, instead of every
client hammering a single server.

`AOS_INSTANCE_INDEX` is read by the client only; the server does not need it; it just opens every port from `PORT`
up front.

## Results

Two destinations, on purpose.

The **log** gets every test in full, `iperf3`'s own JSON document included, as one line per test: the error text, the
retransmit counts, the per-second intervals and the CPU utilisation of both sides. That is what makes a failed or
surprising run explicable afterwards, and none of it belongs in a time series.

**VictoriaMetrics** gets only what is worth charting, pushed as `benchmark_result` samples bracketed by
`checkpoint_event` Start/Stop, in the same shape as `services/template/py`. In addition, each test pushes its own
`checkpoint_event` (`event=<test name>`, e.g. `tcp_up`) the moment it begins, so Grafana can mark where each test
started within the run:

| Sample name               | From          |
| ------------------------- | ------------- |
| `<test> throughput, Mbps` | every test    |
| `<test> loss, %`          | the UDP tests |
| `<test> jitter, ms`       | the UDP tests |

The `source` label is `Instance: <AOS_INSTANCE_ID>`, which is what tells instances apart once a scenario is run at
scale.

## Setting up each scenario

**service -> service** needs nothing beyond installing both items with `--test-host bandwidth-server`: the server
sets `hostname: bandwidth-server` and the client reaches it by that name.

**service -> unit** needs `--test-host` set to the node's address, and an `iperf3` server on the node, bound to that
same address:

```console
setsid iperf3 -s -p 5201 -B 10.0.0.100 </dev/null >/tmp/iperf3-unit.log 2>&1 &
```

**service -> external** needs `--test-host` set to the host's address, and the same on the host outside the unit:

```console
setsid iperf3 -s -p 5201 -B 10.0.0.1 </dev/null >/tmp/iperf3-external.log 2>&1 &
```

Unlike the server item, these manual servers do not scale themselves: with `--num-instances` greater than 1, start
one more per instance, each on its own `-p 5201 + AOS_INSTANCE_INDEX`, or every client past the first one fails to
connect.

`10.0.0.1` is the host's address on the bridge carrying the unit's network — the address that faces the unit, and a
stable one, unlike the Aos service bridge that is recreated with a new subnet on every deployment.

### Why the bind matters

A node has more than one address on the path to a container, and without an explicit bind the reply is routed back
over the service bridge and carries the bridge address as its source. `iperf3` connects its UDP socket, so the kernel
drops datagrams arriving from another address — and the symptom is characteristic: the UDP tests fail while the TCP
ones pass. Verified on a unit, where UDP failed against `10.0.0.100` without `-B` and worked with it.

Binding the bridge address instead would also work, but it is a worse choice: that bridge does not exist at boot and
changes between deployments.

### Before deploying

The server item's own `iperf3` also listens on 5201, but inside the container's own network namespace - a distinct
port space from the node's, per the `veth`/bridge setup "Reading the numbers" describes - so it never conflicts with
anything below. What can conflict is anything already bound to 5201 in the node's own namespace:

- a leftover `iperf3 -s` process from an earlier manual run, started as above and never stopped
- on Debian and Ubuntu, the `iperf3` package ships a systemd unit, `iperf3.service`, enabled by default and bound to
  `*:5201` - installing the package is enough to start it, with no explicit `iperf3 -s` needed

Check for either before binding a new manual server to the same port:

```console
ss -lntu | grep 5201
```

No output means 5201 is free on the node, and the `setsid iperf3 -s ...` command above will bind it as expected.
Output means something already owns it - identify which of the two it is (`systemctl status iperf3` for the
packaged unit) and either reuse it, since it already answers on every address the same way a manually bound one
would, or free the port: kill the leftover process, or `sudo systemctl disable --now iperf3` for the unit.

Once a server is bound intentionally, confirm the address the client will actually dial, not merely that some
server exists on 5201 - the bind checks above only rule out the wrong process owning the port, not the wrong
address:

```console
iperf3 -c 10.0.0.100 -p 5201 -t 2
```

A 2-second test run against `--test-host`'s address is enough: it fails immediately if the bind was wrong or the
server is unreachable, instead of the mistake surfacing later as the deployed benchmark's first test failing to
connect.

## Reading the numbers

Between two instances on one node there is no wire: the traffic goes through `veth` and a bridge, in RAM. TCP lands
in the tens of Gbit/s with the sending side pinned at ~100% of a core, which makes the figure a floor set by CPU
rather than the capacity of a link. Raising `cpuLimit` raises the number, which is the clearest sign of what is
actually being measured.

UDP measures something narrower still, and needs a bound target rate to say anything useful: with `UDP_BANDWIDTH=0`,
`iperf3`'s own "unlimited" setting, the client sends 1448 byte datagrams as fast as the CPU allows, which turns the
test into a syscall rate benchmark that reports several times less than TCP on the same path, with most of it counted
as loss - not the network dropping traffic, but the receiver failing to keep up with a rate nothing was aiming for.
`80M`, `create_services.py`'s default, targets a rate the same veth/bridge path comfortably sustains, so loss and
jitter measure the path's behavior at a realistic rate instead of a CPU's datagram-processing ceiling.
