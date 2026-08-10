import os
import subprocess
import time

PORT = os.environ.get("PORT", "5201")

RESTART_DELAY = 5


def main():
    cmd = ["iperf3", "-s", "-p", PORT]

    # iperf3 -s never exits on its own, so this loop only matters if it dies:
    # the service instance stays alive and the server comes back.
    while True:
        print(f"Starting iperf3 server: {' '.join(cmd)}")
        code = subprocess.call(cmd)
        print(f"iperf3 server exited with code {code}, restarting in {RESTART_DELAY}s")
        time.sleep(RESTART_DELAY)


if __name__ == "__main__":
    main()
