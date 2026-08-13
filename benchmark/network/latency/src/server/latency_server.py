import os
import subprocess
import sys
import time

PORT = os.environ.get("PORT", "11111")

RESTART_DELAY = 5

# One server per protocol: sockperf listens either on UDP or, with --tcp, on
# TCP, never on both. TCP and UDP port numbers are independent, so the two can
# share PORT.
SERVERS = [
    ("udp", ["sockperf", "server", "-i", "0.0.0.0", "-p", PORT]),
    ("tcp", ["sockperf", "server", "-i", "0.0.0.0", "-p", PORT, "--tcp"]),
]


def log(message, file=sys.stdout):
    """Print a message prefixed with this instance's AOS_INSTANCE_ID."""
    print(f"[{os.environ.get('AOS_INSTANCE_ID', '')}] {message}", file=file)


def main():
    processes = {}

    # sockperf servers never exit on their own, so this loop only matters if
    # one of them dies: the service instance stays alive and it comes back.
    while True:
        for name, cmd in SERVERS:
            process = processes.get(name)

            if process is not None and process.poll() is None:
                continue

            if process is not None:
                log(f"sockperf {name} server exited with code {process.returncode}, restarting")

            log(f"Starting sockperf {name} server: {' '.join(cmd)}")

            processes[name] = subprocess.Popen(cmd)

        time.sleep(RESTART_DELAY)


if __name__ == "__main__":
    main()
