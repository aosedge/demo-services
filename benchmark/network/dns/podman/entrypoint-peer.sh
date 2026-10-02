#!/bin/sh
# Podman/podman-compose don't assign a per-replica AOS_INSTANCE_ID the way AosCore does, so fall
# back to a per-container-unique id (the hostname, which podman sets to the container id) so this
# instance's log lines are still told apart, matching diskio/timing's podman entrypoints.
export AOS_INSTANCE_ID="${AOS_INSTANCE_ID:-dns-peer-$(hostname)}"
exec python3 -u /dns_peer.py "$@"
