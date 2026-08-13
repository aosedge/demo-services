import os
import subprocess
import sys
import time

PORT = int(os.environ.get("PORT", "5201"))
NUM_INSTANCES = int(os.environ.get("NUM_INSTANCES", "1"))

RESTART_DELAY = 5

# One iperf3 server per instance, each on its own port (PORT + index), so a
# single deployment can serve NUM_INSTANCES clients at once without them
# contending for the same server.
SERVERS = [(index, ["iperf3", "-s", "-p", str(PORT + index)]) for index in range(NUM_INSTANCES)]


def log(message, file=sys.stdout):
    """Print a message prefixed with this instance's AOS_INSTANCE_ID."""
    print(f"[{os.environ.get('AOS_INSTANCE_ID', '')}] {message}", file=file)


def main():
    processes = {}

    # iperf3 -s never exits on its own, so this loop only matters if one of
    # them dies: the service instance stays alive and it comes back.
    while True:
        for index, cmd in SERVERS:
            process = processes.get(index)

            if process is not None and process.poll() is None:
                continue

            if process is not None:
                log(f"iperf3 server {index} exited with code {process.returncode}, restarting")

            log(f"Starting iperf3 server {index}: {' '.join(cmd)}")

            processes[index] = subprocess.Popen(cmd)

        time.sleep(RESTART_DELAY)


if __name__ == "__main__":
    main()
