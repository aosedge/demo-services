#!/bin/sh
# Podman/podman-compose don't template env vars per replica (unlike AosCore's
# per-instance config.yaml), so when scaled via `deploy.replicas` every
# container starts identical. Fall back to a per-container-unique id (the
# hostname, which podman sets to the container id) so instances never
# collide on the same TEST_DIR/${AOS_INSTANCE_ID}.dat file.
export AOS_INSTANCE_ID="${AOS_INSTANCE_ID:-diskio-$(hostname)}"
exec python3 -u /diskio_benchmark.py "$@"
