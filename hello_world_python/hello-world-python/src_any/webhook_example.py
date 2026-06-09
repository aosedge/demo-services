#!/usr/bin/env python3
# Copyright (c) 2018-2025 EPAM Systems
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import time
import json
import datetime
import logging

from urllib import request

logger = logging.getLogger(__name__)

# Go to https://webhook.site/#/ 
#   and copy from "Your unique URL (Please copy it from here, not from the address bar!)" field
#   and paste server link to HTTP_REQUEST_RECEIVER_URL

HTTP_REQUEST_RECEIVER_URL = "https://webhook.site/your-uniq-guid-here"

DATA_SENDING_DELAY = 2
WAIT_TIMEOUT = 5
DELAY_AFTER_ERROR = 2


def main():
    # Initialize data accessor to "VIN" attribute and get this attribute.
    greetings = 'Hello world!'

    # Send information to HTTP server.
    while True:
        try:
            logger.info("Sending telemetry to '{url}'".format(url=HTTP_REQUEST_RECEIVER_URL))
            json_data={"Unit said": greetings, "datetime": datetime.datetime.now().isoformat()}

            params = json.dumps(json_data).encode('utf8')
            request_data = request.Request(
                HTTP_REQUEST_RECEIVER_URL,
                data=params,
                headers={'content-type': 'application/json'}
            )
            request.urlopen(request_data)
            time.sleep(DATA_SENDING_DELAY)

        except KeyboardInterrupt:
            logger.info("Received Keyboard interrupt. shutting down")
            break
        except Exception as exc:
            logger.error(
                "Unhandled exception: {exc_name}".format(exc_name=exc.__class__.__name__),
                exc_info=True,
            )
            time.sleep(DELAY_AFTER_ERROR)
            continue


if __name__ == '__main__':
    main()
