#
#  Copyright (c) 2018-2021 Renesas Inc.
#  Copyright (c) 2018-2021 EPAM Systems Inc.
#

import json
import sys
import time
import logging

from enum import Enum
from copy import copy
from threading import Thread
from collections import deque
from urllib import request, error

from telemetry_emulator.config import TELEMETRY_REST_URL, SENDING_TIMEOUT_SECONDS, DISABLED_PARAMS
from telemetry_emulator.services.emulator_updaters import RESTUpdater, VISUpdater

# logging setup
logging.basicConfig(stream=sys.stdout, level=logging.DEBUG)
logger = logging.getLogger(__name__)

class HTTPClientType(Enum):
    VIS = VISUpdater
    REST = RESTUpdater


class HTTPClient(Thread):
    TELEMETRY = "telemetry"
    TIMESTAMP = "timestamp"
    IN_RECTANGLE = "in_rectangle"

    SENDING_INTERVAL = 0.1

    def __init__(self, type, emulator=None, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._emulator = emulator

        updater_type = HTTPClientType(type)
        self._updater = updater_type.value(emulator=self._emulator)

        self._samples = deque()
        self._alerts = deque()
        self._alert__in_rectangle = True
        self._process = True

    def add(self, sample: dict) -> None:
        self._samples.append(sample)

    def shutdown(self):
        self._process = False

    def _send(self, data: dict) -> None:
        try:
            if DISABLED_PARAMS:
                data = copy(data)
                telemetry = data[self.TELEMETRY]
                for param in DISABLED_PARAMS:
                    telemetry[param] = None
            logger.debug("Sending sample: '%s'", str(data))
            http_request = request.Request(
                url=TELEMETRY_REST_URL,
                data=json.dumps(data).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            try:
                with request.urlopen(http_request, timeout=SENDING_TIMEOUT_SECONDS) as response:
                    if response.status >= 400:
                        logger.error("Telemetry submitting failed, {}".format(response.status))
                        return
                    emulator_properties = json.loads(response.read().decode("utf-8"))
                    logger.debug("Received emulator properties: '%s'", str(emulator_properties))
                    self._updater.update(**emulator_properties)
            except error.HTTPError as err:
                logger.error('error sending telemetry: %s', err.fp.read())
                raise
        except json.JSONDecodeError:
            logger.error("Unable to parse cloud response.")

    def run(self):
        while self._process:

            # Sending new samples
            if self._samples:
                logger.info("Sending new samples, %i", len(self._samples))
                try:
                    sample = self._samples.pop()
                    self._samples.clear()

                    sample_in_rectangle = sample.get(self.TELEMETRY, {}).get(self.IN_RECTANGLE)

                    self._send(data=sample)

                    self._alert__in_rectangle = sample_in_rectangle

                except IndexError:
                    pass

                except KeyError:
                    logger.error("Broken sample, '%s'.", str(sample))

                except (error.URLError, Exception) as ex:
                    if sample_in_rectangle is not None and sample_in_rectangle != self._alert__in_rectangle:
                        logger.info("Saving alert sample, %i", len(self._alerts))
                        sample[self.TELEMETRY][self.TIMESTAMP] = time.time()
                        self._alerts.append(sample)
                    self._alert__in_rectangle = sample_in_rectangle

                    logger.error(
                        "Can't submit vehicle telemetry data from {}. Exception: {}".format(TELEMETRY_REST_URL, ex)
                    )

            # Sending alerts
            if "in_rectangle" in DISABLED_PARAMS:
                self._alerts.clear()

            while self._alerts:
                logger.info("Sending alert samples, %i", len(self._alerts))
                try:
                    sample = self._alerts.popleft()
                    self._send(data=sample)
                except IndexError:
                    break
                except (error.URLError, Exception) as ex:
                    self._alerts.appendleft(sample)
                    logger.error(
                        "Can't submit vehicle alert telemetry data from {}. Exception: {}"
                            .format(TELEMETRY_REST_URL, ex)
                    )
                    break

                # Stop alerts sending if we have new sample
                if self._samples:
                    break

            # Waiting
            time.sleep(self.SENDING_INTERVAL)
