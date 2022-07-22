#
#  Copyright (c) 2018-2021 Renesas Inc.
#  Copyright (c) 2018-2021 EPAM Systems Inc.
#

import logging

from time import time
from copy import copy

from telemetry_emulator.services.vis.vis_data import VISData
from telemetry_emulator.services.vis.vis_base import VISBase, RequestInfo, VISClientNoValueException

# logging setup
logger = logging.getLogger(__name__)


class VISSubscription(VISBase):
    """
    Provides access to VIS subscriptions
    """

    def __init__(self, ws, *args, **kwargs):
        super().__init__(ws=ws, *args, **kwargs)
        self._ws = ws

        # Subscription value
        self._value = None

        # Subscription VIS id
        self._subscription_id = None

        # Subscription request info
        self._subscription_request = None

        # Initial subscription data access point
        self._initial_data = VISData(ws=self._ws, path=self.path)

    @property
    def request_ids(self):
        result = []
        if self._subscription_id:
            result.append(self._subscription_id)
        for item in (self._subscription_request,):
            if item:
                result.append(item.id)
        if self._initial_data:
            result.extend(self._initial_data.request_ids)
        return result

    @property
    def value(self):
        if self._value:
            return copy(self._value)
        else:
            self.get()
            raise VISClientNoValueException()

    def get(self, force=False):
        if force:
            self._subscription_id = None
            self._subscription_request = None

        if self._value is None:
            self._initial_data.get()

        if self._subscription_id is None:
            if self._subscription_request and self._subscription_request.timeout < time():
                logger.error(
                    "We did not get subscription respond from VIS for {path} path in {seconds} seconds, "
                    "requesting it again.".format(path=self.path, seconds=self.TIMEOUT_LIMIT)
                )
                self._subscription_request = None
            else:
                logger.debug("Waiting for VIS response for {} path.".format(self.path))

            if self._subscription_request is None:
                request_id = self._subscribe()
                self._subscription_request = RequestInfo(id=request_id, timeout=time() + self.TIMEOUT_LIMIT)

    def process(self, data):
        request_id = data.get(self.VIS_REQUEST_ID)
        subscription_id = data.get(self.VIS_SUBSCRIPTION_ID)

        if request_id is not None:
            # it is request
            if self._subscription_request and self._subscription_request.id == request_id:
                self._subscription_id = data[self.VIS_SUBSCRIPTION_ID]
                logger.info("Received subscription id {sid} for path: {path}.".format(
                    sid=self._subscription_id,
                    path=self.path
                ))
            elif request_id in self._initial_data.request_ids:
                logger.info("Received initial data for {} path.".format(self.path))
                self._initial_data.process(data=data)
                try:
                    cumulative_data = {}
                    if isinstance(self._initial_data.value, list):
                        for item in self._initial_data.value:
                            cumulative_data.update(item)
                    else:
                        cumulative_data = self._initial_data.value
                    self._value = cumulative_data
                except VISClientNoValueException:
                    logger.info("No initial value yet.")
            else:
                logger.error("Message does not belong to this subscription: {}".format(str(data)))

        elif subscription_id is not None:
            # it is subscription message
            if subscription_id != self._subscription_id:
                logger.error("Subscription id does not belong to this Subscription, expected {ex}, got {got}.".format(
                    ex=self._subscription_id,
                    got=subscription_id
                ))
                return

            logger.info("Received subscription data update for path {}.".format(self.path))
            if self._value:
                try:
                    cumulative_data = {}
                    if isinstance(data[self.VIS_VALUE], list):
                        for item in data[self.VIS_VALUE]:
                            cumulative_data.update(item)
                    else:
                        cumulative_data = data[self.VIS_VALUE]
                    self._value.update(cumulative_data)
                except KeyError:
                    logger.error("No value payload in subscription update.")
            else:
                logger.debug("Initial value was not received, getting it.")
                try:
                    self._value = self._initial_data.value
                except VISClientNoValueException:
                    logger.info("No initial value for {path} yet.".format(path=self.path))

        else:
            logger.error("Data structure does not have '{r}' nor '{s}' fields.".format(
                r=self.VIS_REQUEST_ID,
                s=self.VIS_SUBSCRIPTION_ID
            ))
