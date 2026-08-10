# Latency

Round trip time through the container network, measured with
[`sockperf`](https://github.com/Mellanox/sockperf) as a ping-pong request/response test over TCP and UDP, and
reported as percentiles.

`config.yaml` holds four items: one server, and one client per scenario. The client code is the same for all three —
a scenario is a different value of one environment variable, not different code.

| Item                                        | Scenario           | `TARGET`         | Server side            |
| ------------------------------------------- | ------------------ | ---------------- | ---------------------- |
| `benchmark-network-latency-server`           | —                  | —                | this bundle            |
| `benchmark-network-latency-client-service`   | service -> service | `latency-server` | the server item above  |
| `benchmark-network-latency-client-unit`      | service -> unit    | `10.0.0.100`     | `sockperf` on the node |
| `benchmark-network-latency-client-external`  | service -> external| `10.0.0.1`       | `sockperf` on the host |

Install only the items a run needs: three clients installed at once would measure each other's interference.

## Why percentiles, not an average

Latency distributions are skewed: most round trips sit close to the minimum, and a thin tail of rare ones runs orders
of magnitude longer. An average hides that tail — one round trip of 50 ms among ten thousand of 40 µs moves the
average by five microseconds and disappears. The tail is what real-time and RPC traffic feel: an operation making a
hundred sequential calls has roughly a 63% chance of hitting at least one p99 event.

On this stack the tail comes from scheduling, from conntrack and nftables handling a new flow, and from softirq work
on `veth` competing with neighbouring instances — which is why the figures are expected to change with instance
density even when the median does not.

A percentile is only worth the samples behind it. A five second ping-pong run normally produces a few hundred
thousand round trips, which is enough for `p999`; if a run reports only a few hundred observations, treat `p999` as
noise.

## What the client measures

| Test      | `sockperf` arguments | Protocol |
| --------- | -------------------- | -------- |
| `udp_rtt` | (none)               | UDP      |
| `tcp_rtt` | `--tcp`              | TCP      |

Both run with `--full-rtt`, so every figure is a full round trip. Without that flag `sockperf` halves its numbers and
reports one way latency instead, which is not what the benchmark plan asks for.

The first test is retried for up to 60 seconds while `sockperf` fails to reach the server; a run that exits cleanly
but produced no observations counts as a failure too. Later tests are not retried, and tests are spaced 3 seconds
apart so the server has a moment to reset between them.

After the last test the instance stays alive idling, which keeps its logs available instead of having the unit
restart it in a loop.

## Environment

| Variable   | Default  | Meaning                                          |
| ---------- | -------- | ------------------------------------------------ |
| `TARGET`   | per item | Server hostname or IP. Required.                  |
| `DURATION` | `5`      | Length of every single test, in seconds.          |
| `PORT`     | `11111`  | `sockperf` port, the same for client and server.  |
| `MSG_SIZE` | `64`     | Payload size in bytes passed to `sockperf -m`.    |

## Results

Two destinations, on purpose.

The **log** gets every test in full, `sockperf`'s own report included: all five percentiles it prints, min, max,
average, standard deviation, the observation count and the dropped message count, plus the raw text. The tool has no
machine readable output, so everything is parsed out of that report, and keeping it makes a surprising number
checkable afterwards.

**VictoriaMetrics** gets the three percentiles the benchmark plan names, pushed as `benchmark_result` samples
bracketed by `checkpoint_event` Start/Stop, in the same shape as `services/template/py`:

| Sample name        | From       |
| ------------------ | ---------- |
| `<test> p50, us`   | both tests |
| `<test> p99, us`   | both tests |
| `<test> p999, us`  | both tests |

The `source` label is the instance's `AOS_INSTANCE_ID`, which is what tells instances apart once a scenario is run at
scale.

## Setting up each scenario

`sockperf` listens either on UDP or, with `--tcp`, on TCP, never on both, so every server side is two processes. TCP
and UDP port numbers are independent, so both use the same port.

**service -> service** needs nothing beyond installing both items: the server item runs both processes itself and
sets `hostname: latency-server`.

**service -> unit** needs them on the node, bound to the address the client dials:

```console
setsid sockperf server -i 10.0.0.100 -p 11111 </dev/null >/tmp/sockperf-udp.log 2>&1 &
setsid sockperf server -i 10.0.0.100 -p 11111 --tcp </dev/null >/tmp/sockperf-tcp.log 2>&1 &
```

**service -> external** needs the same on the host, with `10.0.0.1`. Install `sockperf` there first — Debian and
Ubuntu package it:

```console
sudo apt-get install -y sockperf
```

The versions on the two sides then differ, since the `meta-aos-vm` recipe pins `3.10+git` while Ubuntu ships `3.7`.
That pairing has been exercised without trouble; a failure during the handshake rather than during the test is the
sign to look here, and the fix is to build the newer one on the host rather than to downgrade the unit.

### Why the bind matters

A node has more than one address on the path to a container, and without an explicit bind the reply is routed back
over the service bridge and carries the bridge address as its source. The client's socket is connected to the address
it dialed, so the kernel drops the mismatch and UDP fails while TCP passes. This was measured directly with `iperf3`
on the same paths.

`setsid` matters when starting servers over SSH: a plain `&` leaves them attached to the session and they die on
logout, which then looks like the benchmark failing to reach the far side.

### Before deploying

The server item's own `sockperf` listens on 11111 too, but inside a container namespace, so it does not occupy the
node's port; a leftover from an earlier run does.

```console
ss -lntu | grep 11111
sockperf ping-pong -i 10.0.0.100 -p 11111 -t 2 --full-rtt
sockperf ping-pong -i 10.0.0.100 -p 11111 -t 2 --full-rtt --tcp
```

## Requirements

`sockperf` and `python3` must be in the container rootfs. `sockperf` is not in a stock image: it comes from the
`sockperf` recipe in `meta-aos-vm`, installed into `aos-image-vm`, which covers both the node and the service
containers that run on the node rootfs. The external host needs its own copy, from its distribution.

## Reading the numbers

The CPU quota matters more here than for throughput. A container that exhausts its `cpuLimit` is throttled by the
cgroup, and throttling shows up directly as spikes in the tail: `p999` degrades while `p50` stays flat. If the tail
looks implausible, check the quota before blaming the network.

Between two instances on one node, and between an instance and its node, there is no wire — the traffic goes through
`veth` and a bridge, in RAM — so what is characterised is the node's scheduling and softirq behaviour. Only the
external scenario crosses a real boundary, and its figures sit well above the other two, tail included.
