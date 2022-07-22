#
#  Copyright (c) 2018-2021 Renesas Inc.
#  Copyright (c) 2018-2021 EPAM Systems Inc.
#

import logging

from time import time

# logging setup
logger = logging.getLogger(__name__)

from telemetry_emulator.services.vis.vis_base import VISBase, RequestInfo, VISClientNoValueException


class VISData(VISBase):
    """
    Provides simple access to VIS data structures: get, set values
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._value = None
        self._get_request_info = None
        self._set_request_info = None

    @property
    def request_ids(self):
        result = []
        for item in (self._get_request_info, self._set_request_info,):
            if item:
                result.append(item.id)
        return result

    @property
    def value(self):
        if self._value:
            return self._value
        else:
            self.get()
            raise VISClientNoValueException()

    def get(self, force=False):
        if force:
            self._get_request_info = None

        if self._get_request_info and self._get_request_info.timeout < time():
            logger.error(
                "Response was not received from VIS for {path} path in {seconds} seconds, requesting data again."
                    .format(path=self.path, seconds=self.TIMEOUT_LIMIT)
            )
            self._get_request_info = None
        else:
            logger.debug("Waiting for VIS response for {} path.".format(self.path))

        if self._get_request_info is None:
            request_id = self._get()
            self._get_request_info = RequestInfo(id=request_id, timeout=time() + self.TIMEOUT_LIMIT)

    def set(self, value, force=False):
        if force:
            self._set_request_info = None

        if self._set_request_info and self._set_request_info.timeout < time():
            logger.error(
                "Set respond was not received from VIS for setting {path} path in {seconds} seconds."
                    .format(path=self.path, seconds=self.TIMEOUT_LIMIT)
            )
            self._set_request_info = None
        else:
            logger.debug("VIS did not respond for the last set operation for {} path yet.".format(self.path))

        if self._set_request_info is None:
            request_id = self._set(value=value)
            self._set_request_info = RequestInfo(id=request_id, timeout=time() + self.TIMEOUT_LIMIT)

    def process(self, data):
        request_id = data.get(self.VIS_REQUEST_ID)

        if not request_id:
            logger.error("Data structure does not have {} field.".format(self.VIS_REQUEST_ID))
            return

        if request_id == self._get_request_info.id:
            logger.info("Received value for path {}".format(self.path))
            self._value = data.get(self.VIS_VALUE)
            self._get_request_info = None
        elif request_id == self._set_request_info.id:
            logger.info("Received VIS confirmation to data set for path {}".format(self.path))
            self._set_request_info = None
        else:
            logger.error("Received message with unexpected request id {mid}, expected: {sid}".format(
                mid=request_id,
                sid=str(self.request_ids)
            ))
