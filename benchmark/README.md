# Benchmark

This folder contains AosEdge services for benchmark tests.

Every item follows the same shape, which `services/template/py` and `services/template/cpp` define: a `config.yaml`
and a `src/`, one image for any architecture, and results reported to VictoriaMetrics as `benchmark_result` samples
bracketed by `checkpoint_event` Start/Stop, so that everything lands in the same Grafana tables regardless of which
benchmark produced it.

## Network performance

`services/network/` covers the network chapter of the benchmark plan: what a service actually gets out of the
container network AosCore builds for it — `veth`, a bridge, nftables, `tc` and `dnsmasq`.

Three groups, one folder each:

| Group                     | Measures                                                | Tool       |
| ------------------------- | ------------------------------------------------------- | ---------- |
| [`bandwidth`](services/network/bandwidth) | Throughput, TCP and UDP, both directions, plus UDP jitter and loss | `iperf3`   |
| [`latency`](services/network/latency)     | Round trip time as percentiles, TCP and UDP             | `sockperf` |
| [`dns`](services/network/dns)             | Name resolution time as percentiles                     | built in   |

### Scenarios

Each group is exercised in the same three scenarios. A group is one folder with one `config.yaml`, and the scenarios
are items inside it — the client code is written once and pointed at different things:

```
services/network/<group>/src/client/     the client, one copy
services/network/<group>/src/server/     the server, where the scenario needs one in a container
services/network/<group>/config.yaml     one item per scenario, plus the server
```

The container is always the client; only the server side moves. A scenario is a different value of one environment
variable, not different code, which is why the items share the sources rather than duplicating them.

Install only the items a run needs. Every installed client generates traffic, so leaving all three in place would
have them measure each other's interference.

| Scenario           | Server side                                         | Measures                                     |
| ------------------ | --------------------------------------------------- | -------------------------------------------- |
| service -> service | a second item in the same group                     | two containers on one node and one bridge     |
| service -> unit    | a plain process on the node                         | the container to node/gateway path            |
| service -> external| a process on a machine outside the unit             | egress through masquerade to a LAN host        |

The two scenarios whose server lives outside the container need it started by hand — each group's README gives the
exact commands. One rule spans all of them: **bind the server to the address the client dials**. A node has more than
one address on the path to a container, and without an explicit bind the reply is routed back over the service bridge
and carries the bridge address as its source. Both `iperf3` and `sockperf` connect their sockets, so the kernel drops
datagrams arriving from another address, and the symptom is characteristic — UDP fails while TCP passes.

### Results

Every client reports twice, and the split is deliberate.

The **service log** gets the full result of each test: the tool's own output, error text, counters and per-interval
detail. That is what makes a failed or surprising run explicable afterwards, and none of it belongs in a time series.

**VictoriaMetrics** gets only what is worth charting — one `benchmark_result` sample per measured value, labeled by
`name`, with `source` set to the instance's `AOS_INSTANCE_ID` so instances are told apart once a scenario is run at
scale.

| Group     | Samples pushed                                                         |
| --------- | ---------------------------------------------------------------------- |
| bandwidth | `<test> throughput, Mbps`, and for UDP `<test> loss, %` and `<test> jitter, ms` |
| latency   | `<test> p50, us`, `<test> p99, us`, `<test> p999, us`                  |
| dns       | `resolve p50, us`, `resolve p99, us`, `resolve p999, us`               |

Latency and DNS report percentiles rather than averages on purpose. Their distributions are skewed: most samples sit
near the minimum and a thin tail runs orders of magnitude longer, so an average hides exactly the behaviour that
real-time and RPC traffic feel. A percentile is only worth the samples behind it, though — `p999` needs thousands of
samples before it means anything.

### Interpreting the figures

On a unit that runs as a VM, none of the three scenarios crosses a physical wire: traffic moves through `veth`, a
bridge and `tap`, in RAM. Throughput figures there are bounded by how fast one core can move data rather than by any
link, which the `cpu_utilization_percent` section in `iperf3`'s output shows directly, so they should be read as a
floor. Latency and DNS figures on the same paths mostly characterise the node's scheduling behaviour. The numbers
start describing a network once the external scenario points at a host reached over a real interface.

### Running at scale

The benchmark plan repeats every measurement at 1, 16, 256 and 1024 instances. `config.yaml` keeps `minInstances: 1`
throughout — instance count is a deploy time decision, not a property of the item — and every client tags its samples
with `source`, so results from many instances stay separable.

One caveat worth knowing before scaling up: every deployed client generates traffic, so a large deployment is the
plan's *active-load* mode. The *idle-density* mode, where one pair measures while the rest merely exist, needs
something the current items do not have — a way to keep most instances silent.
