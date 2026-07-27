"""macOS Tactrix OpenPort 2.0 detection and serial communication.

Provides a Device class with the same interface as the Windows J2534 Device,
backed by pyserial over the FTDI USB-serial port that the OpenPort 2.0
exposes on macOS.

Detection mirrors the Swift tactrix-mac-driver (IOKit USB scan), implemented
here as a system_profiler parse so it works without any compiled extensions.
"""

import glob
import logging
import os
import re
import subprocess
import sys
import time

TACTRIX_VID = 0x0403   # FTDI
TACTRIX_PID = 0xCC4D   # OpenPort 2.0

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# USB device detection (equivalent of the Swift tactrix-mac-driver)
# ---------------------------------------------------------------------------

def find_openport_usb():
    """Return list of dicts describing connected OpenPort 2.0 devices.

    Each dict: {vendor_id, product_id, serial, manufacturer, product, location,
                port_path}
    """
    if sys.platform != "darwin":
        return []
    try:
        raw = subprocess.check_output(
            ["system_profiler", "SPUSBDataType"],
            text=True, timeout=10,
        )
    except Exception:
        return []

    devices = []
    # Split on lines that look like a device name header (indented text ending with colon)
    blocks = re.split(r"\n(?=\s+\S.*:\s*$)", raw, flags=re.MULTILINE)
    for block in blocks:
        vid_m = re.search(r"Vendor ID:\s*0x([0-9a-fA-F]+)", block)
        pid_m = re.search(r"Product ID:\s*0x([0-9a-fA-F]+)", block)
        if not vid_m or not pid_m:
            continue
        vid = int(vid_m.group(1), 16)
        pid = int(pid_m.group(1), 16)
        if vid != TACTRIX_VID or pid != TACTRIX_PID:
            continue
        serial_m = re.search(r"Serial Number:\s*(\S+)", block)
        mfg_m = re.search(r"Manufacturer:\s*(.+)", block)
        loc_m = re.search(r"Location ID:\s*(0x[0-9a-fA-F]+)", block)
        serial = serial_m.group(1).strip() if serial_m else None
        port = _serial_port_for(serial) if serial else _any_tactrix_port()
        devices.append({
            "vendor_id": vid,
            "product_id": pid,
            "serial": serial,
            "manufacturer": (mfg_m.group(1).strip() if mfg_m else "Tactrix"),
            "product": "OpenPort 2.0",
            "location": (loc_m.group(1).strip() if loc_m else None),
            "port_path": port,
        })
    return devices


def _serial_port_for(serial):
    """Find the /dev/cu.* port matching an FTDI serial number."""
    for pattern in (
        f"/dev/cu.usbmodem{serial}*",
        f"/dev/cu.usbserial-{serial}*",
        f"/dev/cu.usbmodem*{serial}*",
    ):
        hits = glob.glob(pattern)
        if hits:
            return hits[0]
    return _any_tactrix_port()


def _any_tactrix_port():
    """Fallback: return the first plausible Tactrix serial port."""
    for pattern in ("/dev/cu.usbmodem*", "/dev/cu.usbserial-*"):
        hits = glob.glob(pattern)
        if hits:
            return hits[0]
    return None


def find_serial_ports():
    """Return list of serial port paths for detected OpenPort 2.0 devices."""
    return [d["port_path"] for d in find_openport_usb() if d.get("port_path")]


def default_port():
    """Return the first detected OpenPort 2.0 serial port, or None."""
    ports = find_serial_ports()
    return ports[0] if ports else None


# ---------------------------------------------------------------------------
# macOS Device class — same API as the Windows j2534.Device
# ---------------------------------------------------------------------------

class MacDeviceError(Exception):
    pass


class Device:
    """Tactrix OpenPort 2.0 over macOS serial (pyserial).

    Provides the same interface as j2534.Device so the app can use either
    backend transparently.
    """

    def __init__(self, port_path=None, baud=None, request_timeout_ms=400, **_kw):
        self.port_path = port_path or default_port()
        self.baud = int(baud or 15625)
        self.request_timeout_ms = int(request_timeout_ms)
        self._ser = None
        self._fw_version = "macOS serial"
        if not self.port_path:
            raise MacDeviceError(
                "No Tactrix OpenPort 2.0 detected.\n\n"
                "Connect the cable and check System Information → USB."
            )

    def open(self):
        try:
            import serial
        except ImportError:
            raise MacDeviceError(
                "pyserial is required for macOS.\n\n"
                "Install it:  pip3 install pyserial"
            )
        try:
            self._ser = serial.Serial(
                port=self.port_path,
                baudrate=self.baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.request_timeout_ms / 1000.0,
                write_timeout=2.0,
            )
            self._ser.reset_input_buffer()
            self._ser.reset_output_buffer()
            logger.info("Opened %s at %d baud", self.port_path, self.baud)
        except Exception as e:
            raise MacDeviceError(f"Could not open {self.port_path}: {e}")

    def version(self):
        return (self._fw_version, self.port_path, "macOS")

    def battery_mv(self):
        return None

    def ground_pin1(self):
        if self._ser:
            self._ser.dtr = True
        return 0

    def release_pin1(self):
        if self._ser:
            self._ser.dtr = False
        return 0

    def mut2_connect(self, baud=None):
        baud = int(baud or self.baud)
        self.baud = baud
        if self._ser:
            self._ser.baudrate = baud
            self._ser.reset_input_buffer()

    def mut2_init(self, addr=0x00, attempts=5):
        """5-baud ISO 9141 slow init.

        Sends the address byte at ~5 baud by temporarily switching the serial
        port to a low baud rate, then switches back to the data rate and reads
        the ECU key bytes.
        """
        import serial
        if not self._ser:
            raise MacDeviceError("Device not open")
        for attempt in range(attempts):
            try:
                self._ser.baudrate = 5
                self._ser.write(bytes([addr]))
                self._ser.flush()
                time.sleep(2.0)
                self._ser.baudrate = self.baud
                self._ser.timeout = 1.0
                key = self._ser.read(4)
                self._ser.timeout = self.request_timeout_ms / 1000.0
                self._ser.reset_input_buffer()
                if key:
                    logger.info("MUT-II init OK (attempt %d), key=%s",
                                attempt + 1, key.hex())
                    return key
            except Exception as e:
                logger.warning("Init attempt %d failed: %s", attempt + 1, e)
                try:
                    self._ser.baudrate = self.baud
                except Exception:
                    pass
        raise MacDeviceError(
            "MUT-II init failed after %d attempts.\n\n"
            "Check: ignition on, engine in run mode (not flash/boot),\n"
            "pin 1 grounded (CE Lancer requires this)." % attempts
        )

    def mut2_request(self, req, timeout=None):
        """Send a single-byte MUT-II request and return the response byte."""
        if not self._ser:
            return None
        timeout_s = (int(timeout) if timeout else self.request_timeout_ms) / 1000.0
        old_timeout = self._ser.timeout
        try:
            self._ser.timeout = timeout_s
            self._ser.reset_input_buffer()
            self._ser.write(bytes([req]))
            self._ser.flush()
            # read echo of our own byte first (FTDI loopback)
            echo = self._ser.read(1)
            # then the ECU response
            resp = self._ser.read(1)
            if resp:
                return resp[0]
            return None
        except Exception:
            return None
        finally:
            self._ser.timeout = old_timeout

    def read_dtcs(self, low_req=0x38, high_req=0x39):
        from j2534 import DTC_TABLE
        lo = self.mut2_request(low_req)
        hi = self.mut2_request(high_req)
        if lo is None and hi is None:
            return None
        lo = lo or 0
        hi = hi or 0
        bits = lo | (hi << 8)
        found = []
        for code, bit, desc in DTC_TABLE:
            if bits & (1 << bit):
                found.append((code, desc))
        return found

    def clear_dtcs(self, req=0xCA):
        return self.mut2_request(req)

    def disconnect(self):
        pass

    def close(self):
        self.disconnect()
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
            self._ser = None
