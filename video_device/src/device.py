import os
import subprocess
import re

def list_v4l2_devices():
    try:
        print("\nRunning: v4l2-ctl --list-devices\n")

        result = subprocess.run(
            ['v4l2-ctl', '--list-devices'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )

        print("V4L2 Devices (raw output):")
        print(result.stdout)
        
    except subprocess.CalledProcessError as e:
        print("Error running the command:")
        print(e.stderr)


if __name__ == "__main__":
    while True:
        list_v4l2_devices()
        time.sleep(5)
