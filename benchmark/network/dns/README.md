# DNS resolve time

How long a service takes to resolve a name through the `dnsmasq` an AosEdge unit runs for its instances, reported as
percentiles.

`config.yaml` holds four items: an idle peer that exists only to own a name, and one client per scenario. The client
code is the same for all three — a scenario is a different value of one environment variable, not different code.

| Item                                    | Scenario           | `NAME`           | Where the name lives                 |
| --------------------------------------- | ------------------ | ---------------- | ------------------------------------ |
| `benchmark-network-dns-peer`             | —                  | —                | registered by the unit while it runs |
| `benchmark-network-dns-client-service`   | service -> service | `dns-peer`       | the peer item above                  |
| `benchmark-network-dns-client-unit`      | service -> unit    | `main`           | `/etc/aos/addnhosts` on the node     |
| `benchmark-network-dns-client-external`  | service -> external| `dns-probe.test` | a DNS server on the host             |

Install only the items a run needs.

## Why there is no dig

The benchmark plan names `dig` as the tool, but `dig` reports its query time in whole milliseconds
(`;; Query time: 0 msec`), and a local `dnsmasq` answers in hundreds of microseconds, so every sample would read as
zero. Timing the `dig` process from outside is worse: starting it costs more than the query it is supposed to
measure.

So the client sends the query itself — a UDP socket, a hand built query packet, and `time.perf_counter()` around it.
That gives microsecond resolution, no process startup inside the measurement, and enough samples for percentiles. It
also keeps `dig`, and the `bind-utils` package behind it, out of the image: `python3` is already in the container.

## Which resolver is measured

With `RESOLVER` unset the client reads every `nameserver` from the container's `/etc/resolv.conf` and measures
against the first one that answers, reporting which in the log.

That fallback is not decoration. The container is handed two nameservers, the service bridge address first and the
node address second, but `dnsmasq` is configured with `listen-address` set to the node address only — so nothing is
listening on the first one and a query there just times out. A libc resolver walks the list until something replies,
which is why service names resolve normally, and the client does the same. Measuring the first entry blindly would
report timeouts instead of resolve times.

## Environment

| Variable       | Default  | Meaning                                                                  |
| -------------- | -------- | ------------------------------------------------------------------------ |
| `NAME`         | per item | Name to resolve. Required.                                                |
| `QUERIES`      | `2000`   | How many queries to send.                                                 |
| `RANDOM_LABEL` | per item | Prepend a random label to `NAME` on every query, to defeat the DNS cache.  |
| `RESOLVER`     | unset    | DNS server to ask, `host` or `host:port`. Unset means `/etc/resolv.conf`.  |

## Results

Two destinations, on purpose.

The **log** gets the whole result, every individual sample included, so percentiles can be recomputed later or several
instances pooled into one distribution without re-running anything. It also carries the failure breakdown: a timeout,
a non-zero RCODE, or an answer with no records, counted by reason.

**VictoriaMetrics** gets the three percentiles the benchmark plan names, pushed as `benchmark_result` samples
bracketed by `checkpoint_event` Start/Stop, in the same shape as `services/template/py`:

| Sample name        |
| ------------------ |
| `resolve p50, us`  |
| `resolve p99, us`  |
| `resolve p999, us` |

At `QUERIES=2000`, `p99` rests on 20 samples and `p999` on 2, so treat `p999` as an indication and raise `QUERIES` if
it matters. The `source` label is the instance's `AOS_INSTANCE_ID`, which tells instances apart once a scenario is
run at scale.

## Setting up each scenario

**service -> service** needs nothing: install the peer alongside the client, and the unit registers `dns-peer` in
`dnsmasq` for as long as the peer instance runs. `RANDOM_LABEL` stays off — the name is answered out of a local file
every time, so no cache sits in the way.

**service -> unit** needs nothing either on a stock unit. The unit's `dnsmasq` reads two hosts files:

```
addn-hosts=/var/aos/dns/addnhosts   # written by Aos, rewritten on every deployment
addn-hosts=/etc/aos/addnhosts       # static, for names the unit owner adds
```

and the second already carries `10.0.0.100 main`, which is why `NAME` defaults to `main`. To measure a different
name, add it there and make `dnsmasq` re-read the file:

```console
echo "10.0.0.100 dns-probe-unit" >> /etc/aos/addnhosts
kill -HUP $(cat /var/aos/dns/pidfile)
```

Not `/var/aos/dns/addnhosts` — Aos rewrites that file whenever instances change.

**service -> external** needs a DNS server on the host holding the name, and a unit that forwards to it. The
forwarding is usually already there: check the node's `/etc/resolv.conf` for `nameserver 10.0.0.1`, and if it is
listed, nothing on the unit has to change.

The host normally already runs a `dnsmasq` on the bridge, serving DHCP for it:

```console
ps -eo pid,args | grep [d]nsmasq
ss -lnup | grep 10.0.0.1:53
```

Add the record to `/etc/dnsmasq.conf`:

```
address=/dns-probe.test/10.0.0.1
```

`dnsmasq` reads that file at startup unless given `-C`, so it is the right place even for an instance launched by
hand. `/etc/dnsmasq.d/` is not: on a stock Ubuntu the `conf-dir` line is commented out, so the directory is never
read and the record is silently ignored. `/etc/hosts` cannot express a wildcard, so it is not an option either.

The wildcard form matters. `address=/dns-probe.test/10.0.0.1` answers the domain and everything under it, which is
what makes `RANDOM_LABEL=1` work — and it is on for this scenario for a reason: `dnsmasq` on the unit caches
answers, so a repeated name would be served from cache after the first query and the scenario would stop describing
resolution.

`address=` is only read at startup — a `SIGHUP` re-reads hosts files, not the configuration — so restart it exactly
as it was running:

```console
sudo kill <pid>
sudo dnsmasq --interface=aos-br0 --dhcp-range=10.0.0.101,10.0.0.254,12h \
             --dhcp-option=3,10.0.0.1 --dhcp-option=6,10.0.0.1 --bind-interfaces
```

That process usually serves DHCP for the bridge, so anything holding a lease will notice the second it is down; a
unit with a static address does not. Verify before deploying:

```console
nslookup probe123.dns-probe.test 10.0.0.100
```

## Requirements

`python3` in the container rootfs, which is already there. Nothing else — no `dig`, no layer, no image change.

## Reading the numbers

The service -> service and service -> unit scenarios measure the same thing on this stack and come out equal: both
names live in files read by the same `dnsmasq` on the node, and the packets travel the same bridge either way. Only
the external scenario leaves the node, and it costs several times more — that gap is the price of forwarding.

Comparing a resolve time against the latency benchmark's round trip time on the same path is worth doing: locally the
two come out nearly identical, which says the lookup inside `dnsmasq` is almost free and what is being measured is
the network round trip.
