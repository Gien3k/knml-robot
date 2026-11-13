"""LiDAR D500 – sterownik prawdziwy (UART) i symulowany."""
from __future__ import annotations

import time

import numpy as np

from ..config import settings
from ..hub import Hub
from ..sim.world import get_world
from .common import Driver
from .ld_protocol import LdParser, ScanAssembler


def handle_scan(hub: Hub, proc, ranges: np.ndarray, hz: float) -> None:
    res = proc.process(ranges)
    now = time.time()
    hub.lidar = {"tracks": res["tracks"], "safety": res["safety"], "pose": res["pose"],
                 "t": time.monotonic(), "hz": round(hz, 1)}
    hub.scan_slot.publish({
        "type": "scan", "t": now,
        "r": [int(v * 1000) for v in res["ranges"]],
        "fg": [int(i) for i in np.flatnonzero(res["fg"])],
    })
    hub.status["lidar"].fps.tick()


class LidarReal(Driver):
    sensor, kind = "lidar", "real"

    def __init__(self, hub: Hub, port: str, proc) -> None:
        super().__init__(hub, port)
        self.proc = proc

    def loop(self) -> None:
        import serial
        s = settings
        ser = serial.Serial(self.target, s.lidar_baud, timeout=0.05, exclusive=True)
        parser = LdParser()
        asm = ScanAssembler(s.lidar_clockwise, s.lidar_angle_offset_deg, s.lidar_min_intensity)
        self.hub.set_status("lidar", "real", self.target, "Skanowanie 360°")
        self.hub.event("info", f"LiDAR D500 podłączony ({self.target})")
        last_data = time.monotonic()
        try:
            while not self.halted:
                data = ser.read(2048)
                now = time.monotonic()
                if data:
                    last_data = now
                elif now - last_data > 2.0:
                    raise IOError("brak danych z LiDAR-u od 2 s")
                for pkt in parser.feed(data):
                    scan = asm.add(pkt)
                    if scan is not None:
                        handle_scan(self.hub, self.proc, scan, asm.hz)
        finally:
            ser.close()


class LidarMock(Driver):
    sensor, kind = "lidar", "mock"

    def __init__(self, hub: Hub, proc, message: str = "") -> None:
        super().__init__(hub, "sim")
        self.proc = proc
        self.message = message

    def loop(self) -> None:
        world = get_world()
        self.hub.set_status("lidar", "mock", "symulacja", self.message or "Nie wykryto LiDAR-u – dane symulowane")
        nxt = time.monotonic()
        while not self.halted:
            world.tick()
            handle_scan(self.hub, self.proc, world.lidar_scan(), 10.0)
            nxt += 0.1
            if self.sleep(nxt - time.monotonic()):
                break
            if nxt < time.monotonic() - 0.5:
                nxt = time.monotonic()
