# Bandwidth

Throughput through the container network, measured with [`iperf3`](https://iperf.fr/) for TCP and UDP in both
directions, together with UDP jitter and packet loss.

`config.yaml` holds four items: one server, and one client per scenario. The client code is the same for all three —
a scenario is a different value of one environment variable, not different code.

| Item                                          | Scenario           | `TARGET`           | Server side           |
| --------------------------------------------- | ------------------ | ------------------ | --------------------- |
| `benchmark-network-bandwidth-server`           | —                  | —                  | this bundle           |
| `benchmark-network-bandwidth-client-service`   | service -> service | `bandwidth-server` | the server item above |
| `benchmark-network-bandwidth-client-unit`      | service -> unit    | `10.0.0.100`       | `iperf3` on the node  |
| `benchmark-network-bandwidth-client-external`  | service -> external| `10.0.0.1`         | `iperf3` on the host  |

Install only the items a run needs: three clients installed at once would measure each other's interference.

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

| Variable        | Default  | Meaning                                          |
| --------------- | -------- | ------------------------------------------------ |
| `TARGET`        | per item | Server hostname or IP. Required.                  |
| `DURATION`      | `5`      | Length of every single test, in seconds.          |
| `PORT`          | `5201`   | `iperf3` port, the same for client and server.    |
| `UDP_BANDWIDTH` | `0`      | Target rate for the UDP tests (`0` is unlimited). |

## Results

Two destinations, on purpose.

The **log** gets every test in full, `iperf3`'s own JSON document included, as one line per test: the error text, the
retransmit counts, the per-second intervals and the CPU utilisation of both sides. That is what makes a failed or
surprising run explicable afterwards, and none of it belongs in a time series.

**VictoriaMetrics** gets only what is worth charting, pushed as `benchmark_result` samples bracketed by
`checkpoint_event` Start/Stop, in the same shape as `services/template/py`:

| Sample name                | From          |
| -------------------------- | ------------- |
| `<test> throughput, Mbps`  | every test    |
| `<test> loss, %`           | the UDP tests |
| `<test> jitter, ms`        | the UDP tests |

The `source` label is the instance's `AOS_INSTANCE_ID`, which is what tells instances apart once a scenario is run at
scale.

## Setting up each scenario

**service -> service** needs nothing beyond installing both items: the server sets `hostname: bandwidth-server` and
the client reaches it by that name.

**service -> unit** needs an `iperf3` server on the node, bound to the address the client dials:

```console
setsid iperf3 -s -p 5201 -B 10.0.0.100 </dev/null >/tmp/iperf3-unit.log 2>&1 &
```

**service -> external** needs the same on the host outside the unit:

```console
setsid iperf3 -s -p 5201 -B 10.0.0.1 </dev/null >/tmp/iperf3-external.log 2>&1 &
```

`10.0.0.1` is the host's address on the bridge carrying the unit's network — the address that faces the unit, and a
stable one, unlike the Aos service bridge that is recreated with a new subnet on every deployment.

### Why the bind matters

A node has more than one address on the path to a container, and without an explicit bind the reply is routed back
over the service bridge and carries the bridge address as its source. `iperf3` connects its UDP socket, so the kernel
drops datagrams arriving from another address — and the symptom is characteristic: the UDP tests fail while the TCP
ones pass. Verified on a unit, where UDP failed against `10.0.0.100` without `-B` and worked with it.

Binding the bridge address instead would also work, but it is a worse choice: that bridge does not exist at boot and
changes between deployments.

`setsid` matters when starting a server over SSH: a plain `&` leaves it attached to the session and it dies on
logout, which then looks like the benchmark failing to reach the far side.

### Before deploying

The server item's own `iperf3` listens on 5201 too, but inside a container namespace, so it does not occupy the
node's port; a leftover from an earlier run does. On Debian and Ubuntu the `iperf3` package also ships an enabled
`iperf3.service` on `*:5201` — either use it as is, since it answers on every address, or take the port over with
`sudo systemctl disable --now iperf3`.

```console
ss -lntu | grep 5201
iperf3 -c 10.0.0.100 -p 5201 -t 2
```

## Requirements

`iperf3` and `python3` must be in the container rootfs, which they are: service containers run on the node rootfs,
and `aos-image-vm` installs both.

`tmpLimit` is required rather than decorative. `iperf3` creates a temporary buffer file under `/tmp` for every
stream, and the container rootfs is read-only, so without that quota every test fails on both sides with
`unable to create a new stream: Read-only file system`.

## Reading the numbers

Between two instances on one node there is no wire: the traffic goes through `veth` and a bridge, in RAM. TCP lands
in the tens of Gbit/s with the sending side pinned at ~100% of a core, which makes the figure a floor set by CPU
rather than the capacity of a link. Raising `cpuLimit` raises the number, which is the clearest sign of what is
actually being measured.

UDP with `UDP_BANDWIDTH=0` measures something narrower still: `iperf3` sends 1448 byte datagrams, so the test becomes
a syscall rate benchmark and reports several times less than TCP on the same path. That is not UDP being slower; it
is the datagram size. Loss reported under those conditions is the receiver failing to keep up, not the network
dropping traffic.
