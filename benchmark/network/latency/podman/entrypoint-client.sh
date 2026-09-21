#!/bin/sh
# Podman/podman-compose don't assign a per-replica AOS_INSTANCE_ID the way AosCore does, so fall
# back to a per-container-unique id (the hostname, which podman sets to the container id) so this
# instance's log lines are still told apart, matching diskio/timing's podman entrypoints.
export AOS_INSTANCE_ID="${AOS_INSTANCE_ID:-latency-client-$(hostname)}"

# AOS_INSTANCE_INDEX has to be a small sequential integer matching which of latency_server.py's
# NUM_INSTANCES port pairs this instance dials. Podman's `deploy.replicas` has no per-replica
# ordinal to derive that from (confirmed: every replica of one service is identical, with no
# numbering scheme at all), so this compose file clones a separate latency-client-@SERVICE_ID@
# service per instance instead (see compose.yaml.in) - SERVICE_ID is create_services.py's own
# 1-based numbering baked in at render time; latency_client.py/latency_server.py's shared,
# AosCore-owned port numbering is 0-based, so it's shifted by one here rather than changed at the
# source.
export AOS_INSTANCE_INDEX=$((SERVICE_ID - 1))

exec python3 -u /latency_client.py "$@"
