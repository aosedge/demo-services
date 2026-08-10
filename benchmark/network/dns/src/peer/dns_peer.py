import os
import time

HEARTBEAT_DELAY = 60

# The peer exists only to own a DNS name: the unit registers a running
# instance in the per-bridge dnsmasq, and that registration is what the client
# measures the resolution of. There is nothing to serve, so it just stays up.
def main():
    print(f"DNS peer running as {os.environ.get('AOS_INSTANCE_ID', 'unknown instance')}")

    while True:
        time.sleep(HEARTBEAT_DELAY)


if __name__ == "__main__":
    main()
