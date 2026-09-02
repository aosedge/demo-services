# RTPS publisher and subscriber

A Fast DDS publisher and two subscribers, used to check that DDS/RTPS traffic
works between services that belong to different providers and therefore sit in
isolated network segments.

## How discovery works here

The services do not use multicast. They run as Fast DDS *discovery server
clients* and announce themselves over unicast to a server that runs on the node
rootfs, outside the service networks:

```
writer (provider A)  ─┐
                      ├─► discovery server on the node, 10.0.0.100:11811
reader 1 (provider B) ─┤
reader 2 (provider B) ─┘
```

Once participants know about each other, user data flows **directly** between
them. That direct traffic is what the network policy governs, which is why the
services declare ports for each other.

## Requirements

Fast DDS has to be present on the node: the services take it from the node
rootfs through `baseLayerIsNodeRootfs`, and the discovery server itself is a
node service. Both come from the `aos-dds-discovery` and `fastdds` recipes in
meta-aos-vm.

The same applies when building: the SDK must come from an image that includes
Fast DDS. The upstream release toolchain does not ship it.

## Layout

The writer and the readers are published under **different providers**, so each
is a separate signing unit with its own configuration:

```
rtps_pubsub/
├── src/              shared sources and the common CMake fragment
├── writer/
│   ├── CMakeLists.txt
│   ├── config.yaml   provider A
│   └── output/amd64/rtps-writer
└── reader/
    ├── CMakeLists.txt
    ├── config.yaml   provider B, both reader items
    └── output/amd64/rtps-reader
```

## Building

Each unit is a CMake project of its own, so it is built with the shared
`build.sh` at the repo root, once per unit, from this directory:

```console
../build.sh writer --toolchain=/path/to/environment-setup-core2-64-aos-linux
../build.sh reader --toolchain=/path/to/environment-setup-core2-64-aos-linux
```

- `--toolchain` is optional; if omitted, the current environment's toolchain is
  used;
- `--arch` defaults to `amd64`, which is what both configurations expect.

Each binary lands in the image folder of its own unit.

### Regenerating the type support

The type support in `src/` is generated from `src/HelloWorld.idl` and committed
alongside it, the way eProsima keeps it in the Fast DDS examples these files
come from. Building therefore needs no code generator, and the SDK does not
have to carry one: `fastddsgen` is a Java application.

It only has to be regenerated when Fast DDS changes major version, since the
generated API is tied to it. Each file records the version it came from in its
header, currently `fastddsgen (version: 4.3.0)`:

```console
cd src && fastddsgen -replace HelloWorld.idl
```

## Publishing

Sign each unit separately, from its own directory and with that provider's key:

```sh
cd writer && aos-signer go
cd reader && aos-signer go
```

Putting the writer and the readers under the same provider places them in one
network segment, and the setup stops exercising anything.

### Filling in the UUID placeholders

`allowedConnections` names the peer by the **item UUID** the peer was published
under, and that UUID only exists once the item has been published. Both
configurations therefore ship with placeholders that have to be replaced by
hand:

| Placeholder | In | Replace with the item UUID of |
|---|---|---|
| `<demo-rtps-writer-uuid>` | `reader/config.yaml`, both items | `demo-rtps-writer` |
| `<demo-rtps-reader-1-uuid>` | `writer/config.yaml` | `demo-rtps-reader-1` |
| `<demo-rtps-reader-2-uuid>` | `writer/config.yaml` | `demo-rtps-reader-2` |

So the first publish is a bootstrap: publish all three items with the
placeholders still in place, read the item UUIDs off the service page in the
cloud, substitute them, and publish again. The placeholders are intentionally
not valid UUIDs so that an entry nobody has substituted yet stands out.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DS_ADDRESS` | `10.0.0.100` | address of the discovery server |
| `DS_PORT` | `11811` | port of the discovery server |
| `TOPIC` | `RtpsProbe` | topic name |
| `RATE_HZ` | `1` | publication rate, writer only |
| `PARTICIPANT_NAME` | hostname | name reported during discovery |

## What to look for

Each service logs every participant it discovers together with the locators
that participant advertises:

```
discovered participant: rtps-reader-1
    metatraffic unicast   UDPv4:[172.20.0.3]:7412
    user data   unicast   UDPv4:[172.20.0.3]:7413
```

Peers send to those advertised addresses rather than to the source address of
the packets they received. Comparing them with what a capture on the bridges
shows tells you whether anything is rewriting addresses in between.
