#
#  Copyright (c) 2025 EPAM Systems Inc.
#

import time
import gpiod
import logging
from gpiod.line import Direction, Value


# logging setup
logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.DEBUG)

CHIP = "/dev/gpiochip0"
LINE = 4
DELAY = 1.0  # seconds

def blink_line(chip_path: str, line: int, delay: float = 1.0):
    try:
        with gpiod.request_lines(
            chip_path,
            consumer="blink-example",
            config={
                line: gpiod.LineSettings(
                    direction=Direction.OUTPUT,
                    output_value=Value.INACTIVE,
                )
            },
        ) as request:
            logger.info(f"Blinking GPIO line {line} on {chip_path}")

            while True:
                request.set_value(line, Value.ACTIVE)
                time.sleep(delay)
                request.set_value(line, Value.INACTIVE)
                time.sleep(delay)
    except KeyboardInterrupt:
        logger.debug("Blinking stopped by user")
    except Exception as e:
        logger.error(f"Error occurred: {e}")

if __name__ == "__main__":
    blink_line(CHIP, LINE, DELAY)
