# RTPS publisher and subscriber

A Fast DDS publisher and two subscribers, used to check that DDS/RTPS traffic
works between services that belong to different service providers and therefore
sit in isolated network segments.

## How discovery works here

The services do not use multicast. They run as Fast DDS *discovery server
clients*
([Discovery Server](https://fast-dds.docs.eprosima.com/en/v3.6.2/fastdds/discovery/discovery_server.html))
and announce themselves over unicast to a server that runs on the node rootfs,
outside the service networks:

```
writer (service provider 1)   ─┐
                               ├─► node discovery server, 10.0.0.100:11811
reader 1 (service provider 2) ─┤
reader 2 (service provider 2) ─┘
```

The discovery server is a node service, not a container. It runs in the node's
own network namespace and listens on the node's address, `10.0.0.100:11811`,
rather than on all addresses. The services reach it at that address
(`DS_ADDRESS`), so the two have to match.

The server advertises the address it listens on to the services. Bound to all
addresses, it would advertise only those present when it started, and if the
node's network was not up yet, that is just `127.0.0.1`, which no service can
reach from its container.

A request from a service leaves its container through its service provider's
bridge. Because it is addressed to the node itself, the node delivers it to the
discovery server locally instead of forwarding it into another service
provider's network, so `allowedConnections` does not apply to it.

Once participants know about each other, user data flows **directly** between
them: from one service provider's bridge, through the node's routing, into the
other service provider's bridge. That forwarded traffic is what
`allowedConnections` governs, which is why the services declare ports for each
other.

No address translation takes place on either path. The node masquerades only
traffic that leaves through its uplink interface; traffic between service
provider bridges, and the discovery server's replies to a service, never do. A
participant is therefore seen by its peers at the same address it advertises in
its locators, which is what RTPS relies on.

## Requirements

**Two registered service providers.** The writer and the readers are published
under different service providers, so the AosEdge cloud needs two registered
service providers, each with its own user certificate (`aos-user-sp.p12`) to
sign its units with. Creating service provider accounts is covered in
[Register your users](https://docs.aosedge.tech/docs/how-to/register-your-users/).

Fast DDS has to be present on the node: the services link against its
libraries there, and the discovery server itself is a node service. Both come
from the `fastdds` and `aos-dds-discovery` recipes in
[meta-aos](https://github.com/aosedge/meta-aos), which are installed on the main
node when the `fastdds` distro feature is enabled.
[meta-aos-vm](https://github.com/aosedge/meta-aos-vm) and
[meta-aos-rpi](https://github.com/aosedge/meta-aos-rpi) turn that feature on
with their `WITH_FASTDDS` parameter:

```console
moulin aos-vm.yaml --WITH_FASTDDS=yes
moulin aos-rpi.yaml --WITH_FASTDDS=yes
```

The same applies when building: the SDK must come from an image built with that
feature, for each architecture you build for, and its Fast DDS version has to
match the node's; rebuild the services when Fast DDS changes in the image. The
upstream release toolchain does not ship Fast DDS.

## Layout

The writer and the readers are published under **different service providers**,
so each is a separate signing unit with its own configuration:

```
rtps_pubsub/
├── src/              shared sources and the common CMake fragment
├── writer/
│   ├── CMakeLists.txt
│   ├── config.yaml   service provider 1
│   └── output/
│       ├── amd64/rtps-writer
│       └── arm64/rtps-writer
└── reader/
    ├── CMakeLists.txt
    ├── config.yaml   service provider 2, both reader items
    └── output/
        ├── amd64/rtps-reader
        └── arm64/rtps-reader
```

Both configurations declare an `amd64` and an `arm64` image, so each unit has
to be built for both architectures before it is signed.

## Building

Each unit is a CMake project of its own, so it is built with the shared
`build.sh` at the repo root, once per unit, from this directory:

```console
../build.sh writer --toolchain=/path/to/environment-setup-core2-64-aos-linux
../build.sh reader --toolchain=/path/to/environment-setup-core2-64-aos-linux
```

For `arm64` (Raspberry Pi 5), run the same commands with the arm64 SDK and
`--arch=arm64`:

```console
../build.sh writer --toolchain=/path/to/environment-setup-cortexa76-aos-linux --arch=arm64
../build.sh reader --toolchain=/path/to/environment-setup-cortexa76-aos-linux --arch=arm64
```

- `--toolchain` is optional; if omitted, the current environment's toolchain is
  used;
- `--arch` defaults to `amd64` and names the image folder the binary is copied
  to, so it has to match the toolchain.

Each binary lands in `output/<arch>` of its own unit.

### Regenerating the type support

DDS needs C++ code for every message type it sends: the type itself and the
functions that serialize it into RTPS. This code is generated from an IDL
description of the type. Here the type is `HelloWorld` — an index and a message
string — described in `src/HelloWorld.idl`, and the generated files are the
`HelloWorld*` sources next to it in `src/`.

They are committed rather than generated during the build, the same way
eProsima ships them in its Fast DDS examples. The generator, `fastddsgen`, is a
Java application, so committing its output keeps Java out of the build and out
of the SDK.

Regenerate them only when `HelloWorld.idl` changes or Fast DDS moves to a new
major version, since the generated code depends on its API. Each file records
the generator version in its header, currently `fastddsgen (version: 4.3.0)`:

```console
cd src && fastddsgen -replace HelloWorld.idl
```

## Publishing

The writer and the readers are published under **different service providers**:
the writer under service provider 1 (SP1), both readers under service provider 2
(SP2). Sign each unit with the key of its own service provider, passing it to
`aos-signer` with `-p`:

| Unit | Service provider | Key |
|---|---|---|
| `writer` | SP1 | `/path/to/aos-user-sp1.p12` |
| `reader` | SP2 | `/path/to/aos-user-sp2.p12` |

```sh
cd writer && aos-signer go -p /path/to/aos-user-sp1.p12
cd reader && aos-signer go -p /path/to/aos-user-sp2.p12
```

Separate service providers are what this demo is about: each service provider
gets its own network segment, so the traffic between the writer and the readers
crosses from one network into the other. The demo also runs with all three
under one service provider, but then they share a segment and it exercises a
different network scenario: communication within one network rather than
between two.

The signing and upload flow itself is covered by the
[aos-signer reference](https://docs.aosedge.tech/docs/how-to/tutorials/setup-tools/aos-tools/aos-signer/)
and the
[hello-world tutorial](https://docs.aosedge.tech/docs/how-to/tutorials/service-management/develop-your-service/hello-world/).

### `allowedConnections` addressing

`allowedConnections` names the peer by **hostname**. Each item sets a fixed `hostname` (matching its `PARTICIPANT_NAME`), and the
peer's `allowedConnections` entry references that same name directly:

| Item | `hostname` / `PARTICIPANT_NAME` |
|---|---|
| `demo-rtps-writer` | `VehicleStatePublisher` |
| `demo-rtps-reader-1` | `AnalyticsConsumer` |
| `demo-rtps-reader-2` | `DiagnosticsConsumer` |

Because the names are fixed in the configuration instead of assigned at
publish time, there's no bootstrap step: publish all three items once and the
allow lists already match.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DS_ADDRESS` | `10.0.0.100` | address of the discovery server |
| `DS_PORT` | `11811` | port of the discovery server |
| `TOPIC` | `RtpsProbe` | topic name |
| `RATE_HZ` | `1` | publication rate, writer only |
| `PARTICIPANT_NAME` | hostname | name reported during discovery |

### Port range

RTPS derives a participant's unicast ports from its participant ID. For domain 0
with the default port parameters, participant `p` uses `7410 + 2p` for
metatraffic and `7411 + 2p` for user data. The `7410-7449/udp` range in
`exposedPorts` and `allowedConnections` therefore covers participant IDs 0 to
19 within one container. Each service here runs a single participant, so it
uses 7410 and 7411; widen the range only if a container runs more participants
or a different domain.

## Verify the result

Discovery on its own does not show that data arrives, so check both.

The services print to stdout, which ends up in the system journal of the main
node under the `aos_sm_app` identifier, next to the Service Manager's own
messages. Follow it on the node, dropping the Service Manager lines (they start
with a module name in parentheses):

```sh
journalctl -t aos_sm_app -f | grep -v ']: ('
```

Each service writes under its own PID, so its lines are easy to tell apart.
The checks below look for these lines.

**1. Discovery.** Each service logs every participant it discovers together
with the locators that participant advertises
([Listening Locators](https://fast-dds.docs.eprosima.com/en/v3.6.2/fastdds/transport/listening_locators.html)).
The writer has to discover both readers, and each reader the writer:

```
discovered participant: AnalyticsConsumer
    metatraffic unicast   UDPv4:[172.20.0.3]:7410
    user data   unicast   UDPv4:[172.20.0.3]:7411
```

Peers send to those advertised addresses rather than to the source address of
the packets they received. Comparing them with what a capture on the bridges
shows tells you whether anything is rewriting addresses in between.

**2. Matching.** The writer reports `matched readers: 2`, each reader
`matched writers: 1`.

**3. Data delivery.** Every sample the writer sends has to reach both readers:

```
VehicleStatePublisher: sent 42
AnalyticsConsumer:     received 42 from VehicleStatePublisher
DiagnosticsConsumer:   received 42 from VehicleStatePublisher
```

**4. Policy.** Remove one reader, for example `DiagnosticsConsumer`, from the
writer's `allowedConnections` and publish the writer again. That reader stops
receiving samples while the other one carries on; restoring the entry restores
delivery. This shows that the traffic between the service providers passes only
because the policy allows it.

## Troubleshooting

| Symptom | First checks |
|---|---|
| No participants discovered | `aos-dds-discovery` is running on the node; `DS_ADDRESS` and `DS_PORT` |
| Discovery server found at `127.0.0.1`, then `participant left` | the server is not listening on the node's address; it has to listen on `10.0.0.100`, the same as `DS_ADDRESS` |
| Participants discovered, no samples received | `allowedConnections` and `exposedPorts` on both sides; the port range covers the advertised locators |
| Only one reader receives samples | that reader is missing from the writer's `allowedConnections` |
