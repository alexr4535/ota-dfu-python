from array import array
import gatt
import os
from gi.repository import GObject, Gio, GLib
from util import *
import struct
import datetime


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


class AnyDeviceDFU(gatt.Device):
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
        super().__init__(mac_address, manager)

    # --------------------------------------------------------------------------
    #    Bin: read binfile into bin_array
    # --------------------------------------------------------------------------
    def input_setup(self):
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
        if self.verbose:
            print(
                "Notification Enable succeeded for characteristic:", characteristic.uuid
            )

    def characteristic_write_value_succeeded(self, characteristic):
        if self.verbose:
            print(
                "Characteristic value was written successfully for characteristic:",
                characteristic.uuid,
            )

    def characteristic_value_updated(self, characteristic, value):
        if self.verbose:
            print(
                "Characteristic value was updated for characteristic:",
                characteristic.uuid,
            )
            print("New value is:", value)

    def services_resolved(self):
        super().services_resolved()

        print("[%s] Resolved services" % (self.mac_address))
        ble_dfu_serv = next(s for s in self.services if s.uuid == self.UUID_DFU_SERVICE)
        ctrl_point_char = next(
            c for c in ble_dfu_serv.characteristics if c.uuid == self.UUID_CTRL_POINT
        )
        packet_char = next(
            c for c in ble_dfu_serv.characteristics if c.uuid == self.UUID_PACKET
        )

        time_serv = next(
            s for s in self.services if s.uuid == "00001805-0000-1000-8000-00805f9b34fb"
        )
        time_char = next(
            c
            for c in time_serv.characteristics
            if c.uuid == "00002a2b-0000-1000-8000-00805f9b34fb"
        )

        # Subscribe to notifications from Control Point characteristic
        if self.verbose:
            print("Enabling notifications")
        ctrl_point_char.enable_notifications()
        # time_char.enable_notifications()

        # Send 'START DFU' + Application Command
        # Write "Start DFU" (0x01) to DFU Control Point
        if self.verbose:
            print("Sending START_DFU")
        time_char.write_value(get_current_time())
        # ctrl_point_char.write_value(bytearray.fromhex("01"))

        # Transmit binary image size
        # Need to pad the byte array with eight zero bytes
        # (because that's what the bootloader is expecting...)
        # Write the image size to DFU Packet
        # <Length of SoftDevice><Length of bootloader><Length of application>
        # lengths must be in uint32
        hex_size_array_lsb = uint32_to_bytes_le(len(self.bin_array))


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
