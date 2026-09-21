#!/bin/sh
# Podman/podman-compose don't template env vars per replica (unlike AosCore's per-instance
# config.yaml), so when scaled via `deploy.replicas` every container starts identical. Fall back
# to a per-container-unique id (the hostname, which podman sets to the container id) so each
# instance's checkpoint_event/log output is still told apart, matching diskio's podman entrypoint.
export AOS_INSTANCE_ID="${AOS_INSTANCE_ID:-timing-$(hostname)}"
exec /usr/local/bin/benchmark-timing "$@"
