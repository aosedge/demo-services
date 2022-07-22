#
#  Copyright (c) 2018-2021 Renesas Inc.
#  Copyright (c) 2018-2021 EPAM Systems Inc.
#

import ssl
import json
import logging

from uuid import uuid4
from websocket import create_connection

from telemetry_emulator.config import VIS_URL
from telemetry_emulator.services.emulator_updaters.base import EmulatorUpdaterBase

# logging setup
logger = logging.getLogger(__name__)


class VISUpdater(EmulatorUpdaterBase):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ws = None
        self._attributes_sample = None

    def _connect(self):
        self._ws = create_connection(VIS_URL, sslopt={"cert_reqs": ssl.CERT_NONE})

    def _vis_set(self, key, value):
        request_id = str(uuid4())

        if self._ws is None:
            self._connect()

        try:
            self._ws.send(json.dumps({
                "action": "set",
                "path": key,
                "value": value,
                "requestId": request_id
            }))
        except Exception:
            self._ws = None
        return request_id

    def update(self, rectangle_long0=None, rectangle_lat0=None, rectangle_long1=None,
               rectangle_lat1=None, to_rectangle=None, stop=None, tire_break=False, *args, **kwargs):
        # Compose structure
        attributes_sample = {
            "to_rectangle": to_rectangle,
            "stop": stop,
            "tire_break": tire_break,
        }

        # Process rectangle
        rectangle = {
            "rectangle_long0": rectangle_long0,
            "rectangle_lat0": rectangle_lat0,
            "rectangle_long1": rectangle_long1,
            "rectangle_lat1": rectangle_lat1
        }

        if all(rectangle.values()):
            rectangle_float = {}
            try:
                for key, value in rectangle.items():
                    rectangle_float[key] = float(value)
                rectangle = rectangle_float
            except Exception:
                logger.error("Attributes sample skipped. One of the rectangle value is not float number.", rectangle)
                return

        # Adding rectangle properties
        attributes_sample.update(rectangle)

        if attributes_sample != self._attributes_sample:
            logger.info("Emulator attributes has been changed, sending '%s'", str(attributes_sample))
            data = [{k: v} for k, v in attributes_sample.items()]
            self._vis_set(
                key="Attribute.Emulator.*",
                value=data
            )
            self._attributes_sample = attributes_sample
