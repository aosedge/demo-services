#
#  Copyright (c) 2018-2021 Renesas Inc.
#  Copyright (c) 2018-2021 EPAM Systems Inc.
#

import json
import logging

from collections import namedtuple
from abc import ABCMeta, abstractmethod

from uuid import uuid4

from telemetry_emulator.config import VIS_REQUEST_TIMEOUT_SECONDS

# logging setup
logger = logging.getLogger(__name__)

RequestInfo = namedtuple("RequestInfo", ["id", "timeout"])


class VISClientBaseException(Exception):
    pass


class VISClientNoValueException(VISClientBaseException):
    pass


class VISBase(metaclass=ABCMeta):
    """
    Base class for all VIS data classes
    """

    VIS_VALUE = "value"
    VIS_REQUEST_ID = "requestId"
    VIS_SUBSCRIPTION_ID = "subscriptionId"

    TIMEOUT_LIMIT = VIS_REQUEST_TIMEOUT_SECONDS

    def __init__(self, ws, path):
        self._ws = ws
        self._path = path

    @property
    def path(self):
        return self._path

    def _set(self, value):
        logger.debug("Setting {path} path with '{value}' value.".format(path=self.path, value=value))
        request_id = str(uuid4())
        self._ws.send(json.dumps({
            "action": "set",
            "path": self._path,
            "value": value,
            "requestId": request_id
        }))
        return request_id

    def _get(self):
        logger.debug("Request data get for {} path.".format(self.path))
        request_id = str(uuid4())
        self._ws.send(json.dumps({
            "action": "get",
            "requestId": request_id,
            "path": self._path
        }))
        return request_id

    def _subscribe(self):
        logger.debug("Subscribing to {} path.".format(self.path))
        request_id = str(uuid4())
        self._ws.send(json.dumps({
            "action": "subscribe",
            "requestId": request_id,
            "path": self._path
        }))
        return request_id

    @abstractmethod
    def process(self, data):
        pass

    @property
    @abstractmethod
    def request_ids(self):
        pass

    @property
    @abstractmethod
    def value(self):
        pass
