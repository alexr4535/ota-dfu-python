from array import array
import gatt
import os
from gi.repository import GObject, Gio, GLib
from util import *
import struct
import datetime
import time
import math


def get_current_time():
    now = datetime.datetime.now()

    # https://www.bluetooth.com/wp-content/uploads/Sitecore-Media-Library/Gatt/Xml/Characteristics/org.bluetooth.characteristic.current_time.xml
    return bytearray(
        struct.pack(
            "HBBBBBBBB",
            now.year,
            now.month,
            now.day,
            now.hour,
            now.minute,
            now.second,
            now.weekday() + 1,  # numbered 1-7
            int(now.microsecond / 1e6 * 256),  # 1/256th of a second
            0b0001,  # adjust reason
        )
    )


class InfiniTimeDFU(gatt.Device):
    # Class constants
    UUID_DFU_SERVICE = "00001530-1212-efde-1523-785feabcd123"
    UUID_CTRL_POINT = "00001531-1212-efde-1523-785feabcd123"
    UUID_PACKET = "00001532-1212-efde-1523-785feabcd123"
    UUID_VERSION = "00001534-1212-efde-1523-785feabcd123"

    def __init__(self, mac_address, manager, firmware_path, datfile_path, verbose):
        self.firmware_path = firmware_path
        self.datfile_path = datfile_path
        self.target_mac = mac_address
        self.verbose = verbose
        self.current_step = 0
        self.pkt_receipt_interval = 10
        self.pkt_payload_size = 20
        self.done = False
        self.packet_recipt_count = 0

        super().__init__(mac_address, manager)

    def input_setup(self):
        """Bin: read binfile into bin_array"""
        print(
            "preparing "
            + os.path.split(self.firmware_path)[1]
            + " for "
            + self.target_mac
        )

        if self.firmware_path == None:
            raise Exception("input invalid")

        name, extent = os.path.splitext(self.firmware_path)

        if extent == ".bin":
            self.bin_array = array("B", open(self.firmware_path, "rb").read())

            self.image_size = len(self.bin_array)
            print("Binary image size: %d" % self.image_size)
            print(
                "Binary CRC32: %d" % crc32_unsigned(array_to_hex_string(self.bin_array))
            )
            return
        raise Exception("input invalid")

    def connect_succeeded(self):
        super().connect_succeeded()
        print("[%s] Connected" % (self.mac_address))

    def connect_failed(self, error):
        super().connect_failed(error)
        print("[%s] Connection failed: %s" % (self.mac_address, str(error)))

    def disconnect_succeeded(self):
        super().disconnect_succeeded()
        print("[%s] Disconnected" % (self.mac_address))

    def characteristic_enable_notifications_succeeded(self, characteristic):
        if self.verbose and characteristic.uuid == self.UUID_CTRL_POINT:
            print("Notification Enable succeeded for Control Point Characteristic")
        self.step_one()

    def characteristic_write_value_succeeded(self, characteristic):
        if self.verbose and characteristic.uuid == self.UUID_CTRL_POINT:
            print(
                "Characteristic value was written successfully for Control Point Characteristic"
            )
        if self.current_step == 1:
            self.step_two()

        if self.current_step == 3:
            self.step_four()

        if self.current_step == 5:
            self.step_six()

        if self.current_step == 6:
            print("Begin DFU")
            self.step_seven()

        if self.verbose and characteristic.uuid == self.UUID_PACKET:
            print(
                "Characteristic value was written successfully for Packet Characteristic"
            )

    def characteristic_value_updated(self, characteristic, value):
        if self.verbose:
            if characteristic.uuid == self.UUID_CTRL_POINT:
                print(
                    "Characteristic value was updated for Control Point Characteristic"
                )
            if characteristic.uuid == self.UUID_PACKET:
                print("Characteristic value was updated for Packet Characteristic")
        print("New value is:", value)

        if array_to_hex_string(value)[2:-2] == "01":
            self.step_three()

        if array_to_hex_string(value)[2:-2] == "02":
            self.step_five()

        if array_to_hex_string(value)[0:2] == "11":
            self.packet_recipt_count += 1
            print("receipt count", str(self.packet_recipt_count))
            if self.done != True:
                self.i += self.pkt_payload_size
                self.step_seven()
        
        if array_to_hex_string(value)[2:-2] == "04":
            self.step_nine()

    def services_resolved(self):
        super().services_resolved()

        print("[%s] Resolved services" % (self.mac_address))
        ble_dfu_serv = next(s for s in self.services if s.uuid == self.UUID_DFU_SERVICE)
        self.ctrl_point_char = next(
            c for c in ble_dfu_serv.characteristics if c.uuid == self.UUID_CTRL_POINT
        )
        self.packet_char = next(
            c for c in ble_dfu_serv.characteristics if c.uuid == self.UUID_PACKET
        )

        if self.verbose:
            print("[INFO ] Enabling notifications for Control Point Characteristic")
        self.ctrl_point_char.enable_notifications()

    def step_one(self):
        self.current_step = 1
        if self.verbose:
            print(
                "[INFO ] Sending ('Start DFU' (0x01), 'Application' (0x04)) to DFU Control Point"
            )
        self.ctrl_point_char.write_value(bytearray.fromhex("01 04"))

    def step_two(self):
        self.current_step = 2
        if self.verbose:
            print("[INFO ] Sending Image size to the DFU Packet characteristic")
        x = len(self.bin_array)
        hex_size_array_lsb = uint32_to_bytes_le(x)
        zero_pad_array_le(hex_size_array_lsb, 8)
        self.packet_char.write_value(bytearray(hex_size_array_lsb))
        print("[INFO ] Waiting for Image Size notification")

    def step_three(self):
        self.current_step = 3
        if self.verbose:
            print("[INFO ] Sending 'INIT DFU' + Init Packet Command")
        self.ctrl_point_char.write_value(bytearray.fromhex("02 00"))

    def step_four(self):
        self.current_step = 4
        if self.verbose:
            print("[INFO ] Sending the Init image (DAT)")
        self.packet_char.write_value(bytearray(self.get_init_bin_array()))
        if self.verbose:
            print("[INFO ] Send 'INIT DFU' + Init Packet Complete Command")
        self.ctrl_point_char.write_value(bytearray.fromhex("02 01"))
        print("[INFO ] Waiting for INIT DFU notification")

    def step_five(self):
        self.current_step = 5
        if self.verbose:
            print("Setting pkt receipt notification interval")
        self.ctrl_point_char.write_value(bytearray.fromhex("08 0A"))

    def step_six(self):
        self.current_step = 6
        if self.verbose:
            print(
                "[INFO ] Send 'RECEIVE FIRMWARE IMAGE' command to set DFU in firmware receive state"
            )
        self.ctrl_point_char.write_value(bytearray.fromhex("03"))
        self.segment_count = 0
        self.i = 0
        self.segment_total = int(
            math.ceil(self.image_size / float(self.pkt_payload_size))
        )

    def step_seven(self):
        self.current_step = 7
        # Send bin_array contents as as series of packets (burst mode).
        # Each segment is pkt_payload_size bytes long.
        # For every pkt_receipt_interval sends, wait for notification.
        # i starts at 0
        segment = self.bin_array[self.i : self.i + self.pkt_payload_size]
        self.packet_char.write_value(segment)
        self.segment_count += 1
        if (self.segment_count == self.segment_total):
            self.done = True
            self.step_eight()
        elif (self.segment_count % self.pkt_receipt_interval) != 0:
            self.i += self.pkt_payload_size
            self.step_seven()
        else:
            print("Waiting for Packet Reciept Notifiation...")
        # else, wait for notification

    def step_eight(self):
        self.current_step = 8
        print("Sending Validate command")
        self.ctrl_point_char.write_value(bytearray.fromhex("04"))

    def step_nine(self):
        self.current_step = 9
        print("Activate and reset")
        self.ctrl_point_char.write_value(bytearray.fromhex("05"))

    def get_init_bin_array(self):
        # Open the DAT file and create array of its contents
        init_bin_array = array("B", open(self.datfile_path, "rb").read())
        return init_bin_array


class InfiniTimeManager(gatt.DeviceManager):
    def __init__(self):
        cmd = "btmgmt info"
        btmgmt_proc = Gio.Subprocess.new(
            cmd.split(),
            Gio.SubprocessFlags.STDIN_PIPE | Gio.SubprocessFlags.STDOUT_PIPE,
        )
        _, stdout, stderr = btmgmt_proc.communicate_utf8()
        self.adapter_name = stdout.splitlines()[1].split(":")[0]
        self.alias = None
        self.scan_result = False
        self.mac_address = None
        super().__init__(self.adapter_name)

    def get_scan_result(self):
        return self.scan_result

    def get_mac_address(self):
        return self.mac_address

    def set_timeout(self, timeout):
        GObject.timeout_add(timeout, self.stop)

    def device_discovered(self, device):
        if device.alias() in ("InfiniTime", "Pinetime-JF"):
            self.alias = device.alias()
            self.scan_result = True
            self.mac_address = device.mac_address
            self.stop()

    def scan_for_infinitime(self):
        self.start_discovery()
        self.set_timeout(5 * 1000)
        self.run()
