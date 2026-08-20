# DNS resolve time

How long a service takes to resolve a name through the `dnsmasq` an AosEdge unit runs for its instances, reported as
percentiles.

`config.yaml` holds two items: an idle peer that exists only to own a name, and one client. The same client item
covers all three scenarios - which one runs is a choice of `NAME` and `RANDOM_LABEL`, not different code - so which
scenario is measured is decided when `config.yaml` is generated.

## Generating config.yaml

`config.yaml` is generated from `config.yaml.in`, which templates the values both items are deployed with:

- `@VERSION@` - `version`, for both items
- `@NUM_INSTANCES@` - the client's `minInstances`; the peer stays at `1` regardless, since one instance is enough to
  own its name no matter how many client instances resolve it
- `@TEST_HOST@` - the client's `NAME` env var, i.e. which scenario is measured
- `@RANDOM_LABEL@` - the client's `RANDOM_LABEL` env var; `1` only for the external scenario, see below

`config.yaml` itself is gitignored; render it with the shared `create_services.py` from `benchmark/scripts`.
`--test-host` picks the scenario (see "Setting up each scenario" below):

```sh
# service -> service
../scripts/create_services.py --num-instances 4 --version 1.0.0-beta.1 --test-host dns-peer

# service -> unit
../scripts/create_services.py --num-instances 4 --version 1.0.0-beta.1 --test-host main

# service -> external
../scripts/create_services.py --num-instances 4 --version 1.0.0-beta.1 --test-host dns-probe.test --random-label 1
```

`--num-services` is not needed here - it defaults to 1, rendering `config.yaml.in`'s fixed set of two items once
(`config.yaml.in` has no `@SERVICE_ID@` placeholder to clone, unlike `benchmark/timing`'s template).

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

| Variable        | Default                                 | Meaning                                                                   |
| --------------- | --------------------------------------- | ------------------------------------------------------------------------- |
| `NAME`          | `--test-host` in `config.yaml`          | Name to resolve. Required.                                                |
| `QUERIES`       | `2000`                                  | How many queries to send.                                                 |
| `RANDOM_LABEL`  | `--random-label` in `config.yaml` (`0`) | Prepend a random label to `NAME` on every query, to defeat the DNS cache. |
| `RESOLVER`      | unset                                   | DNS server to ask, `host` or `host:port`. Unset means `/etc/resolv.conf`. |
| `NUM_INSTANCES` | `1`                                     | Client item only: how many instances resolve concurrently. See below.     |

### Running multiple instances

Unlike the bandwidth and latency benchmarks, a DNS client instance owns no listening port and dials no fixed peer
address, so there is nothing to pair one-to-one and no `AOS_INSTANCE_INDEX`-based port offset is needed: every
instance just opens its own UDP socket, queries the same resolver, and reports its own percentiles under its own
`source` label. `--num-instances` at generation time sets the client's `minInstances`; the peer item is unaffected,
since a single instance already answers for as many concurrent resolvers as query it.

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

**service -> service** needs `--test-host dns-peer` at generation time, and the peer item installed alongside the
client so the unit registers `dns-peer` in `dnsmasq` for as long as the peer instance runs. `RANDOM_LABEL` stays
off — the name is answered out of a local file every time, so no cache sits in the way.

**service -> unit** needs `--test-host main` and nothing else on a stock unit. The unit's `dnsmasq` reads two hosts
files:

```
addn-hosts=/var/aos/dns/addnhosts   # written by Aos, rewritten on every deployment
addn-hosts=/etc/aos/addnhosts       # static, for names the unit owner adds
```

and the second already carries `10.0.0.100 main`, which is why the example above uses `--test-host main`. To measure
a different name, add it there, make `dnsmasq` re-read the file, and generate `config.yaml` with that name instead:

```console
echo "10.0.0.100 dns-probe-unit" >> /etc/aos/addnhosts
kill -HUP $(cat /var/aos/dns/pidfile)
```

Not `/var/aos/dns/addnhosts` — Aos rewrites that file whenever instances change.

**service -> external** needs `--test-host dns-probe.test --random-label 1` at generation time, a DNS server on the
host holding the name, and a unit that forwards to it. The forwarding is usually already there: check the node's
`/etc/resolv.conf` for `nameserver 10.0.0.1`, and if it is listed, nothing on the unit has to change.

The host normally already runs a `dnsmasq` on the bridge, serving DHCP for it:

Add the record to `/etc/dnsmasq.conf`:

```console
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

If the unit and the external host are two separate machines rather than a unit sitting behind a bridge gateway
(for example, two independent cloud instances), the forwarding described above is not already there, and the
unit's own `dnsmasq` has to be pointed at the external host explicitly instead of relying on `/etc/resolv.conf`.

On the external host, bind `dnsmasq` to its own address rather than leaving it to grab every interface - a stock
image usually already has `systemd-resolved` holding port 53 on `127.0.0.53`/`127.0.0.54`, and an unqualified
`dnsmasq` start fails with "Address already in use" trying to bind `0.0.0.0`:

```console
# /etc/dnsmasq.conf
listen-address=<external host's address>
bind-interfaces
except-interface=lo
address=/dns-probe.test/<external host's address>
```

```console
sudo systemctl restart dnsmasq
```

If the external host has no `dnsmasq` installed as a system service yet, run it standalone instead of creating
`/etc/dnsmasq.conf` - `-C /dev/null` makes it skip config files entirely and take everything from the command
line, so nothing on the host's default configuration is touched:

```console
sudo dnsmasq -C /dev/null --no-daemon \
             --listen-address=<external host's address> --bind-interfaces --except-interface=lo \
             --address=/dns-probe.test/<external host's address>
```

`--no-daemon` keeps it in the foreground so startup errors are visible and `Ctrl-C` stops it; drop it to run
detached instead, and stop it later with `sudo pkill -f 'dnsmasq -C /dev/null'`. Binding to the host's own
address rather than `0.0.0.0` means a stock `systemd-resolved` already holding `127.0.0.53`/`127.0.0.54` is not a
conflict.

On the unit, add the external host as an explicit upstream in `/var/aos/dns/dnsmasq.conf` alongside the existing
directives, rather than editing `/etc/resolv.conf` (which the unit's `dnsmasq` does not necessarily read from, and
which may be managed elsewhere, e.g. `systemd-resolved`):

```console
# appended to /var/aos/dns/dnsmasq.conf
no-resolv
server=<external host's address>
```

`no-resolv` matters here: without it, `all-servers` (already set) still queries whatever `/etc/resolv.conf` lists
in addition to the new `server=` line, and a real upstream may return a different (or no) answer for the test
domain, muddying which server's latency the run is actually measuring.

Unlike `address=`, `server=` and `no-resolv` are picked up on process restart, not `SIGHUP` - if the unit's
`dnsmasq` runs under `systemd` (check with `systemctl status dnsmasq`), `sudo systemctl restart dnsmasq` reloads
the full config; otherwise restart it the same way the process was originally started. Verify from the unit
before deploying:

```console
nslookup probe123.dns-probe.test 10.0.0.100
```

## Reading the numbers

The service -> service and service -> unit scenarios measure the same thing on this stack and come out equal: both
names live in files read by the same `dnsmasq` on the node, and the packets travel the same bridge either way. Only
the external scenario leaves the node, and it costs several times more — that gap is the price of forwarding.

Comparing a resolve time against the latency benchmark's round trip time on the same path is worth doing: locally the
two come out nearly identical, which says the lookup inside `dnsmasq` is almost free and what is being measured is
the network round trip.
