"""Protokół LDROBOT LD19 / STL-19P (Waveshare D500 LiDAR Kit).

UART 230400 8N1. LiDAR sam nadaje ciągły strumień pakietów po 47 bajtów,
nie przyjmuje żadnych komend (złącze ma tylko Tx, PWM, GND, 5V):

  bajt  0      0x54           nagłówek
  bajt  1      0x2C           VerLen (typ 1, 12 punktów)
  bajty 2-3    u16 LE         prędkość obrotu [°/s]
  bajty 4-5    u16 LE         kąt początkowy [0,01°]
  bajty 6-41   12 × (u16 LE odległość [mm], u8 intensywność)
  bajty 42-43  u16 LE         kąt końcowy [0,01°]
  bajty 44-45  u16 LE         znacznik czasu [ms]
  bajt  46     u8             CRC8 z bajtów 0..45 (wielomian 0x4D, init 0x00, MSB-first)

Kąty rosną zgodnie z ruchem wskazówek zegara (patrząc z góry).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

HEADER = 0x54
VERLEN = 0x2C
PKT_LEN = 47
POINTS = 12
BAUD = 230400
_FMT = "<BBHH" + "HB" * POINTS + "HHB"
_SYNC = bytes((HEADER, VERLEN))


def _crc_table() -> tuple[int, ...]:
    tab = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = ((c << 1) ^ 0x4D) & 0xFF if c & 0x80 else (c << 1) & 0xFF
        tab.append(c)
    return tuple(tab)


CRC_TABLE = _crc_table()


def crc8(data: bytes) -> int:
    crc = 0
    for b in data:
        crc = CRC_TABLE[crc ^ b]
    return crc


@dataclass
class Packet:
    speed: int            # °/s
    start: int            # 0,01°
    end: int              # 0,01°
    dist: np.ndarray      # (12,) mm
    intensity: np.ndarray  # (12,)
    timestamp: int


def build_packet(start_cdeg: int, end_cdeg: int, dist_mm, intensity=None,
                 speed: int = 3600, timestamp: int = 0) -> bytes:
    """Składa poprawny pakiet (testy, symulator portu)."""
    dist = [int(d) for d in dist_mm]
    inten = [200] * POINTS if intensity is None else [int(i) for i in intensity]
    assert len(dist) == POINTS and len(inten) == POINTS
    pts: list[int] = []
    for d, i in zip(dist, inten):
        pts += [d, i]
    body = struct.pack(_FMT[:-1], HEADER, VERLEN, speed, start_cdeg % 36000, *pts,
                       end_cdeg % 36000, timestamp % 65536)
    return body + bytes((crc8(body),))


class LdParser:
    """Strumieniowy parser: dowolne kawałki bajtów -> lista poprawnych pakietów."""

    def __init__(self) -> None:
        self.buf = bytearray()
        self.crc_errors = 0
        self.packets = 0

    def feed(self, data: bytes) -> list[Packet]:
        if data:
            self.buf += data
        out: list[Packet] = []
        buf = self.buf
        while True:
            i = buf.find(_SYNC)
            if i < 0:
                # zostaw ostatni bajt – może być początkiem nagłówka
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
            pts = v[4:4 + 2 * POINTS]
            out.append(Packet(
                speed=v[2], start=v[3], end=v[4 + 2 * POINTS], timestamp=v[5 + 2 * POINTS],
                dist=np.array(pts[0::2], np.float64), intensity=np.array(pts[1::2], np.float64),
            ))
            self.packets += 1
        if len(buf) > 4096:            # same śmieci bez nagłówka – nie rośnij w nieskończoność
            del buf[:-PKT_LEN]
        return out


class ScanAssembler:
    """Składa pakiety w pełny obrót: 360 koszyków po 1° w układzie robota.

    Wyjście: zasięgi [m], indeks = kąt przeciwnie do ruchu wskazówek zegara od przodu
    robota (0° = przód, 90° = lewa strona). 0 = brak echa.
    """

    def __init__(self, clockwise: bool = True, offset_deg: float = 0.0, min_intensity: int = 15,
                 min_range_m: float = 0.03, max_range_m: float = 12.0) -> None:
        self.cw = clockwise
        self.offset = offset_deg
        self.min_int = min_intensity
        self.min_mm = min_range_m * 1000.0
        self.max_mm = max_range_m * 1000.0
        self.bins = np.zeros(360)
        self.last_start: int | None = None
        self.hz = 0.0

    def add(self, p: Packet) -> np.ndarray | None:
        out = None
        if self.last_start is not None and p.start + 18000 < self.last_start:
            out = self.bins.copy()          # kąt "zawinął się" = nowy obrót
            self.bins[:] = 0.0
        self.last_start = p.start
        span = (p.end - p.start) % 36000
        if span > 3000:                     # nieprawdopodobny zakres = uszkodzony pakiet
            return out
        ang = (p.start + span / (POINTS - 1) * np.arange(POINTS)) / 100.0
        a = (-ang if self.cw else ang) + self.offset
        b = np.round(a).astype(int) % 360
        ok = (p.dist >= self.min_mm) & (p.dist <= self.max_mm) & (p.intensity >= self.min_int)
        self.bins[b[ok]] = p.dist[ok] / 1000.0
        self.hz = p.speed / 360.0
        return out
