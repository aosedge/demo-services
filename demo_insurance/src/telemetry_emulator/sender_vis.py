#
#  Copyright (c) 2018-2021 Renesas Inc.
#  Copyright (c) 2018-2021 EPAM Systems Inc.
#

import os
import sys
import ssl
import json
import logging

from websocket import create_connection

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telemetry_emulator.config import (
    VIS_URL, DRIVER_UUID, VEHICLE_VIN, INSIDE_MESSAGE, OUTSIDE_MESSAGE
)
from telemetry_emulator.services.http_client import HTTPClient, HTTPClientType
from telemetry_emulator.services.vis import VISData, VISSubscription, VISClientNoValueException

# logging setup
logger = logging.getLogger(__name__)


class VISConnection:

    def __init__(self):
        self._ws = None
        self._in_rectangle = None

        self._message_handlers = []
        self._vin = None
        self._board_message = None

        self._http_client = HTTPClient(type=HTTPClientType.VIS)
        self._http_client.start()

    @property
    def connected(self):
        return bool(self._ws)

    def connect(self):
        self._ws = create_connection(VIS_URL, sslopt={"cert_reqs": ssl.CERT_NONE})
        self._initialize()

    def _initialize(self):
        logger.info("Subscribing to get sample updates.")

        self._vin = VISData(ws=self._ws, path="Attribute.Vehicle.VehicleIdentification.VIN")
        self._vin.get()
        self._message_handlers.append(self._vin)

        telemetry_handler = VISSubscription(ws=self._ws, path="Signal.Emulator.telemetry.*")
        telemetry_handler.get()
        self._message_handlers.append(telemetry_handler)

        self._board_message = VISData(ws=self._ws, path="Attribute.Car.Message")
        self._message_handlers.append(self._board_message)

    def handle(self):
        vis_data = self._ws.recv()
        try:
            vis_data = json.loads(vis_data)

            message_id = vis_data.get(VISData.VIS_REQUEST_ID, vis_data.get(VISData.VIS_SUBSCRIPTION_ID))

            handler = None
            for item in self._message_handlers:
                if message_id in item.request_ids:
                    handler = item
                    break

            if not handler:
                logger.warning("Received unexpected message: '%s'.", str(vis_data))
                return

            handler.process(data=vis_data)

            if handler.path == "Signal.Emulator.telemetry.*":
                vin = VEHICLE_VIN or self._vin.value
                try:
                    telemetry = {k.replace("Signal.Emulator.telemetry.", ""): v for k, v in handler.value.items()}
                except VISClientNoValueException:
                    logger.info("Can't send data, subscription {} does not have value yet.".format(handler.path))
                else:
                    # Board message
                    in_rectangle = telemetry.get("in_rectangle")
                    if in_rectangle != self._in_rectangle:
                        self._in_rectangle = in_rectangle
                        self._board_message.set(value=INSIDE_MESSAGE if self._in_rectangle else OUTSIDE_MESSAGE)

                    # Send sample to cloud
                    if not vin:
                        logger.error("No VIN, sample could not be sent.")
                        return
                    self._http_client.add(sample={
                        "driver": DRIVER_UUID,
                        "vin": vin,
                        "telemetry": telemetry
                    })

        except json.JSONDecodeError:
            logger.error("Unable to decode JSON data")

    def disconnect(self):
        try:
            if self._ws:
                self._ws.close()
        except Exception as exc:
            logger.error("Exception occur on closing connection: %s", str(exc))
        finally:
            self._ws = None

    def shutdown(self):
        self.disconnect()
        self._http_client.shutdown()
        self._http_client.join(timeout=5)


if __name__ == "__main__":
    vis = VISConnection()

    while True:
        try:
            if not vis.connected:
                vis.connect()
            vis.handle()
        except KeyboardInterrupt:
            logger.info("Received Keyboard interrupt. shutting down")
            break
        except Exception as exc:
            logger.error("Received unexpected exception: %s", str(exc))
            vis.disconnect()

    vis.shutdown()
