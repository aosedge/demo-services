#
#  Copyright (c) 2018-2021 Renesas Inc.
#  Copyright (c) 2018-2021 EPAM Systems Inc.
#

import os
import sys
import time
import signal
import logging
from threading import Thread

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telemetry_emulator.config import (
    EMULATOR_UPDATE_TIME, SENDING_INTERVAL, CONTROL_API_ADDRESS, DRIVER_UUID, VEHICLE_VIN
)
from telemetry_emulator.control_api import ControlApiSever
from telemetry_emulator.emulator import VertexPool, Emulator
from telemetry_emulator.services.http_client import HTTPClient, HTTPClientType

# logging setup
logger = logging.getLogger(__name__)


class TelemetrySendException(Exception):
    pass


class SenderREST:

    def __init__(self):

        self._serve = True
        self._vp = VertexPool(os.path.join(os.path.dirname(__file__), "m'ap.json"))
        self._emulator = Emulator(self._vp)

        self._control_server = ControlApiSever(CONTROL_API_ADDRESS, self._emulator)
        self._server_thread = Thread(target=self._control_server.serve_forever, daemon=False)
        self._server_thread.start()

        self._http_client = HTTPClient(type=HTTPClientType.REST, emulator=self._emulator)
        self._http_client.start()

        signal.signal(signal.SIGTERM, self.signal_handler)

    def signal_handler(self, signum, frame):
        if signum == 15:
            print("got SIGTERM")
            self.stop()

    def stop(self):
        self._serve = False
        self._control_server.shutdown()
        self._http_client.shutdown()
        self._server_thread.join(timeout=5)
        self._http_client.join(timeout=5)

    def sending_loop(self):
        while self._serve:
            self._emulator.update(EMULATOR_UPDATE_TIME)

            data = self._emulator.get_data()
            logger.debug("Emulator data: '%s'", str(data))

            self._http_client.add(sample={
                "driver": DRIVER_UUID,
                "vin": VEHICLE_VIN,
                "telemetry": data
            })

            time.sleep(SENDING_INTERVAL)


if __name__ == "__main__":
    sender_rest = SenderREST()

    try:
        sender_rest.sending_loop()
    except KeyboardInterrupt:
        logger.info("received Keyboard interrupt. shutting down")
        sender_rest.stop()
