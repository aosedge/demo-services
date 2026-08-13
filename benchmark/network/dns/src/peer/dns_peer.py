import os
import sys
import time

HEARTBEAT_DELAY = 60


def log(message, file=sys.stdout):
    """Print a message prefixed with this instance's AOS_INSTANCE_ID."""
    print(f"[{os.environ.get('AOS_INSTANCE_ID', '')}] {message}", file=file)


# The peer exists only to own a DNS name: the unit registers a running
# instance in the per-bridge dnsmasq, and that registration is what the client
# measures the resolution of. There is nothing to serve, so it just stays up.
def main():
    log("DNS peer running")

    while True:
        time.sleep(HEARTBEAT_DELAY)


if __name__ == "__main__":
    main()
