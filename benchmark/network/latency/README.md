# Latency

Round trip time through the container network, measured with
[`sockperf`](https://github.com/Mellanox/sockperf) as a ping-pong request/response test over TCP and UDP, and
reported as percentiles. aos_core_cpp's
[Network / Latency](https://github.com/aosedge/aos_core_cpp/blob/main/doc/benchmark_execution.md#latency)
benchmark execution chapter describes the execution steps this implements.

`config.yaml` holds two items: one server and one client. The same client item covers every scenario - which one runs
is a choice of `TARGET`, not different code - so which scenario is measured is decided when `config.yaml` is
generated.

## Generating config.yaml

`config.yaml` is generated from `config.yaml.in`, which templates the values the item is deployed with:

- `@VERSION@` - `version`
- `@NUM_INSTANCES@` - the server's `NUM_INSTANCES` env var and the client's `minInstances`, kept equal so every
  client instance has a server instance pair to test against
- `@TEST_HOST@` - the client's `TARGET` env var, i.e. which scenario is measured

`config.yaml` itself is gitignored; render it with the shared `create_services.py` from `benchmark/scripts`.
`--test-host` picks the scenario (see "Setting up each scenario" below):

```sh
# service -> service
../scripts/create_services.py --num-instances 1 --version 1.0.0-beta.1 --test-host latency-server

# service -> unit
../scripts/create_services.py --num-instances 1 --version 1.0.0-beta.1 --test-host ${NODE_IP}

# service -> external
../scripts/create_services.py --num-instances 1 --version 1.0.0-beta.1 --test-host ${HOST_IP}
```

## Why percentiles, not an average

Latency distributions are skewed: most round trips sit close to the minimum, and a thin tail of rare ones runs orders
of magnitude longer. An average hides that tail — one round trip of 50 ms among ten thousand of 40 µs moves the
average by five microseconds and disappears. The tail is what real-time and RPC traffic feel: an operation making a
hundred sequential calls has roughly a 63% chance of hitting at least one p99 event.

On this stack the tail comes from scheduling, from conntrack and nftables handling a new flow, and from softirq work
on `veth` competing with neighbouring instances — which is why the figures are expected to change with instance
density even when the median does not.

A percentile is only worth the samples behind it. At the default `MSG_RATE`, a ten second ping-pong run produces
about 100,000 round trips, which rests `p999` on about a hundred samples - enough to trust, though `p9999` (logged
but not charted) rests on about ten and is closer to an indication than a solid number. Raise `MSG_RATE` or
`DURATION` if a run reports far fewer observations than that.

## What the client measures

| Test      | `sockperf` arguments | Protocol |
| --------- | -------------------- | -------- |
| `udp_rtt` | (none)               | UDP      |
| `tcp_rtt` | `--tcp`              | TCP      |

Both run with `--full-rtt`, so every figure is a full round trip. Without that flag `sockperf` halves its numbers and
reports one way latency instead, which is not what the benchmark plan asks for. Both also run with `--mps MSG_RATE`,
capping how fast `sockperf` sends: left uncapped it floods the link at the max rate the CPU can push, which turns the
test into a measurement of self-induced queueing delay rather than round trip latency, and stores one sample per
message sent, which is what pushes the item's memory use toward its `ramLimit`. See "Reading the numbers" for a
before/after.

The first test is retried for up to 60 seconds while `sockperf` fails to reach the server; a run that exits cleanly
but produced no observations counts as a failure too. Later tests are not retried, and tests are spaced 3 seconds
apart so the server has a moment to reset between them.

After the last test the instance stays alive idling, which keeps its logs available instead of having the unit
restart it in a loop.

## Environment

| Variable        | Default                        | Meaning                                                       |
| --------------- | ------------------------------ | ------------------------------------------------------------- |
| `TARGET`        | `--test-host` in `config.yaml` | Server hostname or IP. Required.                              |
| `DURATION`      | `10`                           | Length of every single test, in seconds.                      |
| `PORT`          | `11111`                        | Base `sockperf` port; see "Running multiple instances" below. |
| `MSG_SIZE`      | `64`                           | Payload size in bytes passed to `sockperf -m`.                |
| `MSG_RATE`      | `10000`                        | Messages per second, passed to `sockperf --mps`. See below.   |
| `NUM_INSTANCES` | `1`                            | Server item only: how many server pairs to run. See below.    |

### Running multiple instances

`--num-instances` at generation time sets both the server's `NUM_INSTANCES` and the client's `minInstances` to the
same value, so the two items always scale together.

The server runs `NUM_INSTANCES` pairs of `sockperf` servers (one UDP, one TCP), one pair per port from `PORT` up to
`PORT + NUM_INSTANCES - 1`, restarting any of them independently if it dies. Each client instance dials
`PORT + AOS_INSTANCE_INDEX`, the environment variable AosCore sets to this instance's position among the item's
instances - so the batch of client instances spreads across the batch of server instances one-to-one, each pair on
its own port, instead of every client hammering a single server pair.

`AOS_INSTANCE_INDEX` is read by the client only; the server does not need it; it just opens every port from `PORT`
up front.

## Results

Two destinations, on purpose.

The **log** gets every test in full, `sockperf`'s own report included: all five percentiles it prints, min, max,
average, standard deviation, the observation count and the dropped message count, plus the raw text. The tool has no
machine readable output, so everything is parsed out of that report, and keeping it makes a surprising number
checkable afterwards.

**VictoriaMetrics** gets the three percentiles the benchmark plan names, pushed as `benchmark_result` samples
bracketed by `checkpoint_event` Start/Stop, in the same shape as `services/template/py`. In addition, each test
pushes its own `checkpoint_event` (`event=<test name>`, e.g. `udp_rtt`) the moment it begins, so Grafana can mark
where each test started within the run:

| Sample name       | From       |
| ----------------- | ---------- |
| `<test> p50, us`  | both tests |
| `<test> p99, us`  | both tests |
| `<test> p999, us` | both tests |

The `source` label is `Instance: <AOS_INSTANCE_ID>`, which is what tells instances apart once a scenario is run at
scale.

## Setting up each scenario

`sockperf` listens either on UDP or, with `--tcp`, on TCP, never on both, so every server side is two processes. TCP
and UDP port numbers are independent, so both use the same port.

**service -> service** needs nothing beyond installing both items with `--test-host latency-server`: the server item
runs both processes itself and sets `hostname: latency-server`.

**service -> unit** needs `--test-host` set to the node's address, and both processes on the node, bound to that same
address:

```console
setsid sockperf server -i 10.0.0.100 -p 11111 </dev/null >/tmp/sockperf-udp.log 2>&1 &
setsid sockperf server -i 10.0.0.100 -p 11111 --tcp </dev/null >/tmp/sockperf-tcp.log 2>&1 &
```

**service -> external** needs `--test-host` set to the host's address, and the same on the host, with `10.0.0.1`.
Install `sockperf` there first — Debian and Ubuntu package it:

```console
sudo apt-get install -y sockperf
```

Unlike the server item, these manual servers do not scale themselves: with `--num-instances` greater than 1, start
one more pair per instance, each on its own `-p 11111 + AOS_INSTANCE_INDEX`, or every client past the first one
fails to connect.

The versions on the two sides then differ, since the `meta-aos-vm` recipe pins `3.10+git` while Ubuntu ships `3.7`.
That pairing has been exercised without trouble; a failure during the handshake rather than during the test is the
sign to look here, and the fix is to build the newer one on the host rather than to downgrade the unit.

### Why the bind matters

A node has more than one address on the path to a container, and without an explicit bind the reply is routed back
over the service bridge and carries the bridge address as its source. The client's socket is connected to the address
it dialed, so the kernel drops the mismatch and UDP fails while TCP passes. This was measured directly with `iperf3`
on the same paths.

### Before deploying

The server item's own `sockperf` processes also listen on 11111, but inside the container's own network namespace -
a distinct port space from the node's, per the `veth`/bridge setup "Reading the numbers" describes - so they never
conflict with anything below. What can conflict is a leftover `sockperf server` process from an earlier manual run,
started as above and never stopped. Unlike `iperf3`, `sockperf`'s Debian/Ubuntu package ships no systemd unit, so
there is no distro-started server to account for here - a stale manual process is the only risk.

Check for one before binding a new manual server pair to the same port:

```console
ss -lntu | grep 11111
```

No output means 11111 is free on the node, and the `setsid sockperf server ...` commands above will bind it as
expected. Output means a leftover process already owns it - `sudo ss -lntup | grep 11111` additionally prints its
PID, so it can be killed before starting a fresh pair.

Once both servers are confirmed to be the intended ones, test each protocol independently, not just one of them -
one protocol working is not evidence the other does too, since UDP and TCP fail on entirely different mistakes (see
"Why the bind matters" above):

```console
sockperf ping-pong -i 10.0.0.100 -p 11111 -t 2 --full-rtt
sockperf ping-pong -i 10.0.0.100 -p 11111 -t 2 --full-rtt --tcp
```

Two 2-second runs are enough: each fails immediately if its protocol's bind was wrong or its server is unreachable,
instead of the mistake surfacing later as the deployed benchmark's first test failing to connect.

## Reading the numbers

The CPU quota matters more here than for throughput. A container that exhausts its `cpuLimit` is throttled by the
cgroup, and throttling shows up directly as spikes in the tail: `p999` degrades while `p50` stays flat. If the tail
looks implausible, check the quota before blaming the network.

Between two instances on one node, and between an instance and its node, there is no wire — the traffic goes through
`veth` and a bridge, in RAM — so what is characterised is the node's scheduling and softirq behaviour. Only the
external scenario crosses a real boundary, and its figures sit well above the other two, tail included.

`MSG_RATE` matters for the same reason `UDP_BANDWIDTH` does for the bandwidth benchmark: uncapped, `sockperf` sends
as fast as it can, which on this stack means millions of messages per second, each one buffered as an RTT sample
until the run ends. That drove the client's memory use up toward its `ramLimit`, and it also stopped measuring
latency - at that rate the number reported is dominated by queueing behind the flood, not the per-message round
trip. `MSG_RATE=10000` keeps sample count comfortably within the item's `ramLimit` (a few hundred thousand samples
across both tests, not tens of millions) while still producing enough of them for `p999` to mean something (see "Why
percentiles, not an average").
