"""LiDAR D500 – wątek czytający port (wyłącznie odczyt) i składający pełne obroty."""
from __future__ import annotations

import logging
import threading
import time

from ..config import Settings
from ..hub import Hub
from ..protocol.ld19 import BAUD, LdParser, ScanAssembler
from .serial_util import ReadOnlyPort, open_port

log = logging.getLogger("lidar")

NO_DATA_S = 2.0


class LidarDriver(threading.Thread):
    role = "lidar"

    def __init__(self, hub: Hub, port: str, settings: Settings, opener=open_port) -> None:
        super().__init__(daemon=True, name="lidar")
        self.hub, self.port, self.s = hub, port, settings
        self.opener = opener
        self._halt = threading.Event()
        self.error: str | None = None
        self.packets = 0
        self.crc_errors = 0

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        try:
            self._loop()
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            log.warning("LiDAR %s: %s", self.port, self.error)

    def _loop(self) -> None:
        s = self.s
        ser = ReadOnlyPort(self.opener(self.port, BAUD, 0.05))
        parser = LdParser()
        asm = ScanAssembler(s.lidar_clockwise, s.lidar_angle_offset_deg, s.lidar_min_intensity,
                            s.lidar_min_range_m, s.lidar_max_range_m)
        last_valid = time.monotonic()
        try:
            while not self._halt.is_set():
                data = ser.read(2048)
                now = time.monotonic()
                pkts = parser.feed(data)
                if pkts:
                    last_valid = now
                elif now - last_valid > NO_DATA_S:
                    raise IOError(f"brak poprawnych ramek od {NO_DATA_S:.0f} s")
                g = self.hub.lidar_geom
                asm.cw, asm.offset = g.clockwise, g.offset_deg
                for p in pkts:
                    scan = asm.add(p)
                    if scan is not None:
                        self.hub.on_scan(scan, time.monotonic(), "lidar", asm.hz)
                self.packets, self.crc_errors = parser.packets, parser.crc_errors
        finally:
            try:
                ser.close()
            except Exception:
                pass
