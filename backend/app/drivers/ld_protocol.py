"""Protokół LDROBOT STL-19P / LD19 (Waveshare D500 LiDAR Kit).

UART 230400 8N1, ciągły strumień pakietów po 47 bajtów:
  0x54 | 0x2C | speed u16 [°/s] | start u16 [0.01°] | 12 × (dist u16 [mm], intensity u8)
       | end u16 [0.01°] | timestamp u16 [ms] | CRC8 (poly 0x4D)
Kąty rosną zgodnie z ruchem wskazówek zegara (patrząc z góry).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

HEADER, VERLEN, PKT_LEN = 0x54, 0x2C, 47
_FMT = "<BBHH" + "HB" * 12 + "HHB"


def _crc_table() -> list[int]:
    tab = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = ((c << 1) ^ 0x4D) & 0xFF if c & 0x80 else (c << 1) & 0xFF
        tab.append(c)
    return tab


CRC_TABLE = _crc_table()


def crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = CRC_TABLE[(crc ^ b) & 0xFF]
    return crc


@dataclass
class Packet:
    speed: int          # °/s
    start: int          # 0.01°
    end: int            # 0.01°
    dist: np.ndarray    # (12,) mm
    intensity: np.ndarray
    timestamp: int


class LdParser:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.crc_errors = 0
        self.packets = 0

    def feed(self, data: bytes) -> list[Packet]:
        self.buf += data
        out: list[Packet] = []
        buf = self.buf
        while True:
            i = buf.find(bytes((HEADER, VERLEN)))
            if i < 0:
                del buf[:-1]
                break
            if i > 0:
                del buf[:i]
            if len(buf) < PKT_LEN:
                break
            pkt = bytes(buf[:PKT_LEN])
            if crc8(pkt[:-1]) != pkt[-1]:
                self.crc_errors += 1
                del buf[:1]
                continue
            del buf[:PKT_LEN]
            v = struct.unpack(_FMT, pkt)
            pts = v[4:28]
            out.append(Packet(
                speed=v[2], start=v[3], end=v[28], timestamp=v[29],
                dist=np.array(pts[0::2], np.float64), intensity=np.array(pts[1::2], np.float64),
            ))
            self.packets += 1
        return out


class ScanAssembler:
    """Składa pakiety w pełny obrót: 360 kubełków po 1° w układzie robota (CCW od przodu)."""

    def __init__(self, clockwise: bool, offset_deg: float, min_intensity: int) -> None:
        self.cw = clockwise
        self.offset = offset_deg
        self.min_int = min_intensity
        self.bins = np.zeros(360)
        self.last_start: int | None = None
        self.hz = 0.0

    def add(self, p: Packet) -> np.ndarray | None:
        out = None
        if self.last_start is not None and p.start + 18000 < self.last_start:
            out = self.bins.copy()          # nowy obrót
            self.bins[:] = 0.0
        self.last_start = p.start
        span = (p.end - p.start) % 36000
        if span > 3000:                     # uszkodzony pakiet
            return out
        ang = (p.start + span / 11.0 * np.arange(12)) / 100.0
        a = (-ang if self.cw else ang) + self.offset
        b = np.round(a).astype(int) % 360
        ok = (p.dist > 0) & (p.intensity >= self.min_int)
        self.bins[b[ok]] = p.dist[ok] / 1000.0
        self.hz = p.speed / 360.0
        return out
