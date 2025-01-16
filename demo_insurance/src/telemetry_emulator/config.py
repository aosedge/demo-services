#
#  Copyright (c) 2018-2021 Renesas Inc.
#  Copyright (c) 2018-2021 EPAM Systems Inc.
#

import os

# All parameters are here for development needs

# telemetry server address
SEND_TO_ADDRESS = ("telemetry_server", 9999)

# reconnect delay
CONNECTION_RETRY_DELAY = 0.5

# interval between data sending
SENDING_INTERVAL = 0.4
SENDING_TIMEOUT_SECONDS = int(os.environ.get("SENDING_TIMEOUT_SECONDS", 3))

# emulator data update time
EMULATOR_UPDATE_TIME = 0.4

CONTROL_API_ADDRESS = ("0.0.0.0", 8800)

TELEMETRY_REST_HOST = os.environ.get("TELEMETRY_REST_HOST", "demo-insurance.aoscloud.io")
TELEMETRY_REST_URL = "https://{host}/api/v1/telemetry/data/".format(
    host=TELEMETRY_REST_HOST
)

DRIVER_UUID = os.environ.get("DRIVER_UUID", "NoDriverUUID")
VEHICLE_VIN = os.environ.get("VEHICLE_VIN")

VIS_URL = os.environ.get("VIS_URL", "wss://wwwivi:443/")

VIS_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("VIS_REQUEST_TIMEOUT_SECONDS", 3))

# Vehicle onboard alert messages
INSIDE_MESSAGE = "Vehicle inside rectangle"
OUTSIDE_MESSAGE = "Vehicle outside rectangle"

# Commented parameter will be sent to the vehicle
DISABLED_PARAMS = [
    # "in_rectangle",  # vehicle region alerts

    # "veh_speed",  # vehicle speed
    # "engrpm",  # engine rpm
    # "gr",  # current gear
    # "odo",  # odometer
    # "batt_volt",  # system voltage
    # "lrw",  # steering wheel

    # "airtemp_outsd",  # outside temperature
    # "veh_int_temp",  # vehicle interior temperature
    # "engoiltemp",  # engine oil temperature
    # "oil_press",  # oil pressure
    # "gas_range",  # gas range
    # "avgfuellvl",  # average fuel level
]
