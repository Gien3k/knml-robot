"""Autodetekcja sprzętu: USB (sysfs), porty szeregowe (sondowanie protokołu), kamery V4L2.

LiDAR i ESP32 często mają ten sam konwerter USB-UART (CP2102/CH340), więc nie
rozróżniamy ich po VID/PID, tylko po tym, co "mówią":
  * LiDAR D500 – sam nadaje pakiety 0x54 0x2C @230400 (tylko nasłuch),
  * ESP32 Waveshare – odpowiada JSON-em {"T":1001,...} na zapytanie {"T":130}.
"""
from __future__ import annotations

import glob
import logging
import os
import time

from .ld_protocol import LdParser

log = logging.getLogger("detect")

LUXONIS_VID = "03e7"


def _read(path: str, default: str = "") -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return default


def usb_devices() -> list[dict]:
    out = []
    for d in glob.glob("/sys/bus/usb/devices/*"):
        vid = _read(os.path.join(d, "idVendor"))
        if not vid:
            continue
        out.append({
            "bus": os.path.basename(d), "vid": vid, "pid": _read(os.path.join(d, "idProduct")),
            "manufacturer": _read(os.path.join(d, "manufacturer")),
            "product": _read(os.path.join(d, "product")),
        })
    return out


def oak_usb_present() -> bool:
    return any(d["vid"] == LUXONIS_VID for d in usb_devices())


def serial_candidates() -> list[str]:
    ports = sorted(glob.glob("/dev/ttyUSB*")) + sorted(glob.glob("/dev/ttyACM*"))
    if os.path.exists("/dev/ttyAMA0"):          # RPi 5: UART na GPIO (ESP32 w RaspRover)
        ports.append("/dev/ttyAMA0")
    return ports


def serial_info() -> list[dict]:
    try:
        from serial.tools import list_ports
        return [{"port": p.device, "vid": f"{p.vid:04x}" if p.vid else None,
                 "pid": f"{p.pid:04x}" if p.pid else None, "desc": p.description}
                for p in list_ports.comports()]
    except Exception:
        return []


def probe_lidar(port: str, baud: int, timeout: float = 0.8) -> bool:
    import serial
    try:
        with serial.Serial(port, baud, timeout=0.05, exclusive=True) as ser:
            ser.reset_input_buffer()
            parser = LdParser()
            end = time.monotonic() + timeout
            got = 0
            while time.monotonic() < end:
                got += len(parser.feed(ser.read(256)))
                if got >= 3:
                    return True
    except (OSError, ValueError, Exception) as e:  # SerialException dziedziczy po IOError
        log.debug("probe_lidar %s: %s", port, e)
    return False


def open_serial_noreset(port: str, baud: int, timeout: float = 0.0):
    """Otwiera port bez szarpania DTR/RTS (nie resetuje ESP32 przez auto-reset)."""
    import serial
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout = port, baud, timeout
    ser.dtr = False
    ser.rts = False
    ser.exclusive = True
    ser.open()
    return ser


def probe_rover(port: str, baud: int, timeout: float = 2.0) -> bool:
    try:
        ser = open_serial_noreset(port, baud, 0.05)
    except Exception as e:
        log.debug("probe_rover %s: %s", port, e)
        return False
    try:
        ser.reset_input_buffer()
        buf = b""
        end = time.monotonic() + timeout
        last_q = 0.0
        while time.monotonic() < end:
            if time.monotonic() - last_q > 0.4:
                ser.write(b'{"T":130}\n')
                last_q = time.monotonic()
            buf += ser.read(256)
            if b'"T":1001' in buf or b'"T": 1001' in buf:
                return True
            buf = buf[-2048:]
        return False
    except Exception as e:
        log.debug("probe_rover %s: %s", port, e)
        return False
    finally:
        try:
            ser.close()
        except Exception:
            pass


def video_candidates() -> list[dict]:
    """Kamery UVC na USB (pomija węzły metadanych i wewnętrzne węzły ISP/kodeków RPi 5)."""
    out = []
    for p in sorted(glob.glob("/sys/class/video4linux/video*"), key=lambda s: int(s.rsplit("video", 1)[1] or 0)):
        name = _read(os.path.join(p, "name"))
        index = _read(os.path.join(p, "index"), "0")
        dev = os.path.realpath(os.path.join(p, "device"))
        if "/usb" not in dev or index != "0":
            continue
        out.append({"device": "/dev/" + os.path.basename(p), "name": name})
    return out


_dai = None
_dai_error: str | None = None


def depthai():
    """Leniwy import DepthAI (cache)."""
    global _dai, _dai_error
    if _dai is None and _dai_error is None:
        try:
            import depthai as dai  # noqa: F401
            _dai = dai
        except Exception as e:
            _dai_error = f"{type(e).__name__}: {e}"
            log.warning("DepthAI niedostępne: %s", _dai_error)
    return _dai


def oak_available() -> bool:
    if not oak_usb_present():
        return False
    dai = depthai()
    if dai is None:
        return False
    try:
        return len(dai.Device.getAllAvailableDevices()) > 0
    except Exception as e:
        log.debug("getAllAvailableDevices: %s", e)
        return False


def diagnostics() -> dict:
    dai = depthai()
    return {
        "usb": usb_devices(),
        "serial": serial_info(),
        "serial_candidates": serial_candidates(),
        "video": video_candidates(),
        "depthai": getattr(dai, "__version__", None) if dai else _dai_error,
    }
