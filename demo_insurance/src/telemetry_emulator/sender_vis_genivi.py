#
#  Copyright (c) 2018-2021 Renesas Inc.
#  Copyright (c) 2018-2021 EPAM Systems Inc.
#

import os
import sys
import ssl
import json
import logging

from copy import copy
from websocket import create_connection

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telemetry_emulator.config import (
    VIS_URL, DRIVER_UUID, VEHICLE_VIN, INSIDE_MESSAGE, OUTSIDE_MESSAGE
)
from telemetry_emulator.services.http_client import HTTPClient, HTTPClientType
from telemetry_emulator.services.vis import VISData, VISSubscription, VISClientNoValueException
from telemetry_emulator.genivi.converter import GeniviCloudConverter

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

        self._current_sample = {
            'ac_stat': 0,  # Air conditioning status
            'acc_mode': False,  # Cruise Switch: ACC or ESC mode pressed

            # 'airtemp_outsd': int(round(self.airtemp_outsd)),  # Outside air temperature
            'airtemp_outsd': None,  # Outside air temperature

            'aud_mode_adv': False,  # Audio-mode advance
            'aus': False,  # Cruise Switch: Cancel Pressed
            'auto_stat': 0,  # Automatic temperature control status
            'autodfgstat': 7,  # Automatic defog status

            # 'batt_volt': int(round(self.batt_volt)),  # System voltage
            'batt_volt': None,  # System voltage

            'cell_vr': 0,  # Cell Phone/Voice Recognition Request
            'cruise_tgl': False,  # Cruise Switch: On/Off  Pressed
            'defrost_sel': False,  # Defrost select switch
            'dn_arw_step_rq': 0,  # Down Arrow Request / Odometer Trip Reset
            'dr_lk_stat': 2,  # Door lock status
            # 'drv_ajar': self.drv_ajar,  # Driver door ajar (1 = Door Ajar)
            # 'drv_seatbelt': self.drv_seatbelt,  # Drivers seat belt status
            'ebl_stat': 2,  # Electric backlite status
            'engcooltemp': 26,  # Engine coolant temperature

            # 'engoiltemp': int(round(self.engoiltemp)),  # Oil temperature
            'engoiltemp': None,  # Oil temperature

            'engstyle': 7,  # Engine output version
            'fg_ajar': False,  # Flipper glass ajar
            'fl_hs_stat': 0,  # Front left heated seat status
            'fl_vs_stat': 0,  # Front left vented seat status
            'fr_hs_stat': 0,  # Front right heated seat status
            'fr_vs_stat': 0,  # Front right vented seat status
            'ft_drv_atc_temp': 72,  # Front driver HVAC control auto temperature status
            'ft_drv_mtc_temp': 127,  # Front driver HVAC control manual temperature status
            'ft_hvac_blw_fn_sp': 0,  # Front HVAC blower fan speed status in 'bars'
            'ft_hvac_ctrl_stat': 0,  # Front HVAC control status
            'ft_hvac_md_stat': 15,  # Front HVAC control mode status
            'ft_psg_atc_temp': 72,  # Front passenger HVAC control auto temperature status
            'ft_psg_mtc_temp': 127,  # Front passenger HVAC control manual temperature status

            # 'gas_range': self.gas_range,  # Gas range or DTE
            'gas_range': None,  # Gas range or DTE

            'hibmlvr_stat': 0,  # High beam lever state
            'hl_stat': 0,  # Headlamp status
            'hrnsw_psd': False,  # Horn switch pressed (1=pressed)
            'hrnswpsd': False,  # Horn switch pressed (1=pressed)
            'hsw_stat': False,  # Heated steering wheel status
            'l_r_ajar': False,  # Left rear door ajar
            'max_acsts': 0,  # Maximum A/C status
            'menu_rq': 0,  # Menu Switch Request / Back

            # 'odo': self.odometer,  # Odometer km
            'odo': None,  # Odometer km

            # 'oil_press': int(round(self.oil_press)),  # Oil pressure
            'oil_press': None,  # Oil pressure

            'preset_cfg': 0,  # Preset Configuration
            'prkbrkstat': 7,  # Parking brake status
            'prnd_stat': 0,  # PRND Status
            'psg_ajar': False,  # Passenger door ajar
            'psg_ods_stat': 0,  # Passenger Occupant Detection Sensor Status
            'psg_seatbelt': 0,  # Passengers seat belt status
            'r_r_ajar': False,  # Right rear door ajar
            'recirc_stat': 0,  # Recirculation status
            'reserved_1': False,  # Reserved
            'reserved_2': 0,  # Reserved
            'reserved_3': 0,  # Reserved
            'reserved_4': 0,  # Reserved
            'reserved_5': 0,  # Reserved
            'rl_heat_stat': 0,  # Rear left heat status
            'rl_vent_off': False,  # Rear left vent off request

            # 'rr_dr_unlkd': self.rr_dr_unlkd,  # Rear door (hatch / lift gate is unlocked
            'rr_dr_unlkd': False,  # Rear door (hatch / lift gate is unlocked

            'rr_heat_stat': 0,  # Rear right heat status
            'rr_vent_off': False,  # Rear right vent off request
            'rt_arw_rst_rq': 0,  # Right Arrow Reset Request
            's_minus_b': False,  # Cruise Switch: Coast or Set/Decel Pressed
            's_plus_b': False,  # Cruise Switch: Resume/Accel Pressed
            'seek': 0,  # Seek up/down
            'stw_lvr_stat': 0,  # Steering wheel lever state
            'stw_temp': 20,  # Steering wheel temperature
            'sync_stat': False,  # Synchronization Status
            'tirepressfl': 27,  # Tire pressure front left
            'tirepressfr': 27,  # Tire pressure front right
            'tirepressrl': 27,  # Tire pressure rear left
            'tirepressrr': 27,  # Tire pressure rear right
            'tirepressspr': 27,  # Tire pressure spare tire
            'up_arw_rq': 0,  # Up Arrow Request / Step
            'vc_body_style': 7,  # Body style
            'vc_country': 2,  # Country Code
            'vc_model_year': 225,  # Model year
            'vc_veh_line': 29,  # Vehicle line
            'vol': 0,  # Volume up/down
            'wa': False,  # Cruise Switch: distance / launch mode pressed
            'wh_up': False,  # Cruise Switch: Implausible State=1
            'wprsw6posn': 0,  # Wiper switch (6 stages) position
            'wprwash_r_sw_posn_v3': 0,  # Backlite wiper/washer switch position
            'wprwashsw_psd': 0,  # Front wiper switch pressed
        }

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

        telemetry_handler = VISSubscription(ws=self._ws, path="Signal.*")
        telemetry_handler.get()
        self._message_handlers.append(telemetry_handler)

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

            if handler.path == "Signal.*":
                vin = VEHICLE_VIN or self._vin.value
                try:
                    converted_value = GeniviCloudConverter(src_data=handler.value).get_converted_values()
                    if handler.path == 'Signal.Drivetrain.Transmission.Gear':
                        print("!!!!!!!!!GEAR NOT CONVERTED:" + str(handler.value))
                        print("!!!!!!!!!GEAR CONVERTED:" + str(converted_value))
                    self._current_sample.update(converted_value)
                    if not self._current_sample.get('engcooltemp'):
                        self._current_sample['engcooltemp'] = 26
                    telemetry = copy(self._current_sample)
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
            logger.exception(exc)
            vis.disconnect()

    vis.shutdown()
