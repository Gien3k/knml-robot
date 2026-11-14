"""Auto-detekcja LiDAR-u D500 i ESP32 na portach szeregowych.

VID:PID to tylko wskazówka (LiDAR i ESP32 mogą mieć ten sam mostek CP2102),
rolę rozstrzyga TREŚĆ danych. Algorytm dla każdego nieprzypisanego portu:

 1. otwarcie bez resetu ESP32 (serial_util.open_port), wyłączny dostęp,
 2. 230400 bodów, pasywny odczyt 400 ms: >= 3 poprawne ramki LD19 => LiDAR
    (do portu LiDAR-u NIGDY nic nie zapisujemy),
 3. 115200 bodów, pasywny odczyt do 3 s: linia JSON z kluczem "T" => ESP32
    (ESP32 domyślnie sam nadaje telemetrię T:1001; śmieci po starcie są tolerowane),
 4. tylko gdy port milczał (albo nadawał czytelny tekst): jedno zapytanie
    {"T":130} i 500 ms na odpowiedź JSON => ESP32. Przy binarnym szumie nic nie
    wysyłamy – to może być LiDAR, który się jeszcze rozpędza,
 5. w przeciwnym razie "unknown" i ponowna próba za PROBE_RETRY_S.

Komenda ruchu może wyjść WYŁĄCZNIE na port z potwierdzoną rolą ESP32.
"""
from __future__ import annotations

import glob
import logging
import os
import time
from dataclasses import asdict, dataclass, field

from ..protocol import waveshare
from ..protocol.ld19 import BAUD as LIDAR_BAUD
from ..protocol.ld19 import LdParser
from .serial_util import PortBusy, open_port

log = logging.getLogger("discovery")

# Mostki USB-UART spotykane w D500 i płytkach Waveshare; 303A = natywne USB ESP32-S2/S3/C3
WHITELIST: set[tuple[int, int | None]] = {
    (0x10C4, 0xEA60),   # Silicon Labs CP2102/CP2104
    (0x1A86, 0x7523),   # WCH CH340
    (0x1A86, 0x7522),   # WCH CH340K
    (0x1A86, 0x55D3),   # WCH CH343
    (0x1A86, 0x55D4),   # WCH CH9102
    (0x303A, None),     # Espressif (dowolny PID)
}
GPIO_UARTS = ("/dev/ttyAMA0", "/dev/serial0")     # ESP32 na GPIO (RaspRover + Pi 5)

LIDAR_WINDOW_S = 0.4
ESP_PASSIVE_S = 3.0
ESP_QUERY_WAIT_S = 0.5


@dataclass
class PortInfo:
    device: str
    realpath: str
    vid: int | None = None
    pid: int | None = None
    description: str = ""
    hint: str = ""                   # whitelist | gpio | forced | probe_all

    def to_dict(self) -> dict:
        d = asdict(self)
        d["vidpid"] = f"{self.vid:04X}:{self.pid:04X}" if self.vid is not None and self.pid is not None else None
        return d


@dataclass
class ProbeResult:
    role: str                        # lidar | esp32 | unknown | busy | error
    frames: int = 0                  # poprawne ramki LD19
    json_lines: int = 0
    bytes_lidar: int = 0             # bajty odebrane przy 230400
    bytes_esp: int = 0               # bajty odebrane przy 115200
    wrote: int = 0                   # ile bajtów WYSŁALIŚMY (tylko zapytanie T:130)
    detail: str = ""
    duration_s: float = 0.0
    t: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


def parse_extra_ids(spec: str) -> set[tuple[int, int | None]]:
    out: set[tuple[int, int | None]] = set()
    for part in spec.replace(";", ",").split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        v, p = part.split(":", 1)
        try:
            out.add((int(v, 16), None if p.strip() in ("*", "") else int(p, 16)))
        except ValueError:
            continue
    return out


def _allowed(vid: int | None, pid: int | None, ids: set[tuple[int, int | None]]) -> bool:
    if vid is None:
        return False
    return (vid, pid) in ids or (vid, None) in ids


def candidate_ports(extra_ids: str = "", probe_all: bool = False,
                    forced: tuple[str, ...] = ()) -> list[PortInfo]:
    """Porty do sondowania, bez duplikatów (np. /dev/serial0 -> /dev/ttyAMA0)."""
    ids = WHITELIST | parse_extra_ids(extra_ids)
    out: list[PortInfo] = []
    seen: set[str] = set()

    def add(info: PortInfo) -> None:
        if info.realpath in seen:
            return
        seen.add(info.realpath)
        out.append(info)

    for dev in forced:
        if dev and os.path.exists(dev):
            add(PortInfo(dev, os.path.realpath(dev), hint="forced"))
    try:
        from serial.tools import list_ports
        ports = list(list_ports.comports())
    except Exception as e:                      # brak sysfs itp.
        log.debug("list_ports: %s", e)
        ports = []
    for p in sorted(ports, key=lambda x: x.device):
        if _allowed(p.vid, p.pid, ids):
            add(PortInfo(p.device, os.path.realpath(p.device), p.vid, p.pid, p.description or "", "whitelist"))
        elif probe_all and (p.vid is not None or "ttyUSB" in p.device or "ttyACM" in p.device):
            add(PortInfo(p.device, os.path.realpath(p.device), p.vid, p.pid, p.description or "", "probe_all"))
    for dev in GPIO_UARTS:
        if os.path.exists(dev):
            add(PortInfo(dev, os.path.realpath(dev), description="UART na GPIO", hint="gpio"))
    if probe_all:
        for dev in sorted(glob.glob("/dev/ttyUSB*") + glob.glob("/dev/ttyACM*")):
            add(PortInfo(dev, os.path.realpath(dev), hint="probe_all"))
    return out


def _mostly_binary(data: bytes) -> bool:
    if len(data) < 32:
        return False
    printable = sum(1 for b in data if 32 <= b < 127 or b in (9, 10, 13))
    return printable / len(data) < 0.7


def probe(path: str, opener=open_port, lidar_window: float = LIDAR_WINDOW_S,
          passive_window: float = ESP_PASSIVE_S, query_wait: float = ESP_QUERY_WAIT_S,
          allow_query: bool = True) -> ProbeResult:
    """Ustala rolę urządzenia na porcie. Nie wysyła NIC, dopóki port milczy przy 230400."""
    t0 = time.monotonic()
    res = ProbeResult(role="unknown")
    try:
        ser = opener(path, LIDAR_BAUD, 0.05)
    except PortBusy as e:
        return ProbeResult(role="busy", detail=f"port zajęty: {e}", duration_s=time.monotonic() - t0)
    except Exception as e:
        return ProbeResult(role="error", detail=f"{type(e).__name__}: {e}", duration_s=time.monotonic() - t0)

    try:
        # --- krok 2: LiDAR, wyłącznie odczyt ---
        try:
            ser.reset_input_buffer()
        except Exception:
            pass
        parser = LdParser()
        end = time.monotonic() + lidar_window
        while time.monotonic() < end:
            data = ser.read(512)
            res.bytes_lidar += len(data)
            res.frames += len(parser.feed(data))
            if res.frames >= 3:
                res.role = "lidar"
                res.detail = f"{res.frames} poprawnych ramek LD19"
                return res

        # --- krok 3: ESP32, pasywnie ---
        ser.baudrate = waveshare.BAUD
        try:
            ser.reset_input_buffer()
        except Exception:
            pass
        received = bytearray()
        buf = b""

        def scan_lines() -> bool:
            nonlocal buf
            *lines, buf = buf.split(b"\n")
            buf = buf[-512:]
            for ln in lines:
                if waveshare.parse_line(ln) is not None:
                    res.json_lines += 1
                    return True
            return False

        end = time.monotonic() + passive_window
        while time.monotonic() < end:
            data = ser.read(256)
            if data:
                received += data[: max(0, 4096 - len(received))]
                res.bytes_esp += len(data)
                buf += data
                if scan_lines():
                    res.role = "esp32"
                    res.detail = "telemetria JSON (pasywnie)"
                    return res

        # --- krok 4: jedno zapytanie, tylko przy ciszy / czytelnym tekście ---
        if not allow_query:
            res.detail = "cisza – zapytanie wyłączone"
            return res
        if _mostly_binary(bytes(received)):
            res.detail = f"binarny szum ({res.bytes_esp} B) – nie wysyłam zapytania"
            return res
        res.wrote += ser.write(waveshare.QUERY) or 0
        end = time.monotonic() + query_wait
        while time.monotonic() < end:
            data = ser.read(256)
            if data:
                res.bytes_esp += len(data)
                buf += data
                if scan_lines():
                    res.role = "esp32"
                    res.detail = "odpowiedź JSON na {\"T\":130}"
                    return res
        res.detail = "brak ramek LD19 i brak JSON"
        return res
    except Exception as e:
        res.role = "error"
        res.detail = f"{type(e).__name__}: {e}"
        return res
    finally:
        res.duration_s = round(time.monotonic() - t0, 3)
        try:
            ser.close()
        except Exception:
            pass
