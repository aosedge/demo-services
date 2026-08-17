import os
import subprocess
import sys
import time

PORT = int(os.environ.get("PORT", "11111"))
NUM_INSTANCES = int(os.environ.get("NUM_INSTANCES", "1"))

RESTART_DELAY = 5

# sockperf listens either on UDP or, with --tcp, on TCP, never on both, so
# every instance needs two server processes. TCP and UDP port numbers are
# independent, so a pair shares one port; each instance gets its own pair, at
# PORT + index, so a single deployment can serve NUM_INSTANCES clients at once
# without them contending for the same server.
SERVERS = [
    ((index, protocol), ["sockperf", "server", "-i", "0.0.0.0", "-p", str(PORT + index)] + extra_args)
    for index in range(NUM_INSTANCES)
    for protocol, extra_args in (("udp", []), ("tcp", ["--tcp"]))
]


def log(message, file=sys.stdout):
    """Print a message prefixed with this instance's AOS_INSTANCE_ID."""
    print(f"[{os.environ.get('AOS_INSTANCE_ID', '')}] {message}", file=file)


def main():
    processes = {}

    # sockperf servers never exit on their own, so this loop only matters if
    # one of them dies: the service instance stays alive and it comes back.
    while True:
        for key, cmd in SERVERS:
            index, protocol = key
            process = processes.get(key)

            if process is not None and process.poll() is None:
                continue

            if process is not None:
                log(f"sockperf {protocol} server {index} exited with code {process.returncode}, restarting")

            log(f"Starting sockperf {protocol} server {index}: {' '.join(cmd)}")

            processes[key] = subprocess.Popen(cmd)

        time.sleep(RESTART_DELAY)


if __name__ == "__main__":
    main()
