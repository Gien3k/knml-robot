"""Kamera FPV robota (UVC /dev/video*) – sterownik prawdziwy i symulowany.

Prawdziwa kamera: format MJPG; jeśli OpenCV pozwala, klatki JPEG są przekazywane
bez dekodowania (CAP_PROP_CONVERT_RGB=0) – zero pracy CPU na RPi 5.
"""
from __future__ import annotations

import math
import time

import cv2
import numpy as np

from ..config import settings
from ..hub import Hub
from ..sim.world import get_world
from .common import Driver, encode_jpeg, publish_frame


class FpvReal(Driver):
    sensor, kind = "fpv", "real"

    def loop(self) -> None:
        s = settings
        cap = cv2.VideoCapture(self.target, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise IOError(f"nie można otworzyć {self.target}")
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, s.fpv_width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, s.fpv_height)
        cap.set(cv2.CAP_PROP_FPS, s.fpv_fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or s.fpv_width
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or s.fpv_height

        passthrough = False
        try:
            cap.set(cv2.CAP_PROP_CONVERT_RGB, 0)
            ok, raw = cap.read()
            if ok and raw is not None and raw.ndim <= 2 and raw.size > 4 and raw.reshape(-1)[0] == 0xFF \
                    and raw.reshape(-1)[1] == 0xD8 and cv2.imdecode(raw.reshape(-1), cv2.IMREAD_COLOR) is not None:
                passthrough = True
            else:
                cap.set(cv2.CAP_PROP_CONVERT_RGB, 1)
        except cv2.error:
            cap.set(cv2.CAP_PROP_CONVERT_RGB, 1)

        hub = self.hub
        hub.set_status("fpv", "real", f"{self.target} · {w}x{h}",
                       "MJPEG bez dekodowania" if passthrough else "kodowanie JPEG na CPU")
        hub.event("info", f"Kamera FPV podłączona ({self.target})")
        fails = 0
        try:
            while not self.halted:
                ok, frame = cap.read()
                if not ok or frame is None:
                    fails += 1
                    if fails > 30:
                        raise IOError("kamera przestała zwracać klatki")
                    self.sleep(0.02)
                    continue
                fails = 0
                jpeg = frame.reshape(-1).tobytes() if passthrough else encode_jpeg(frame, 75)
                g = hub.rover.get("gimbal", {})
                publish_frame(hub, "fpv", jpeg, w, h, pan=g.get("pan", 0.0), tilt=g.get("tilt", 0.0))
                hub.status["fpv"].fps.tick()
        finally:
            cap.release()


class FpvMock(Driver):
    sensor, kind = "fpv", "mock"

    def __init__(self, hub: Hub, message: str = "") -> None:
        super().__init__(hub, "sim")
        self.message = message

    def loop(self) -> None:
        s = settings
        world = get_world()
        hub = self.hub
        hub.set_status("fpv", "mock", "symulacja", self.message or "Nie wykryto kamery FPV – widok symulowany")
        period = 1.0 / max(5, min(s.mock_fps, 20))
        nxt = time.monotonic()
        while not self.halted:
            world.tick()
            g = hub.rover.get("gimbal", {"pan": 0.0, "tilt": 0.0})
            cam = {"w": 640, "h": 360, "hfov": 100.0, "h_m": s.camera_height_m + 0.05,
                   "yaw": math.radians(g.get("pan", 0.0)), "pitch": math.radians(g.get("tilt", 0.0)) * 0.5,
                   "tint": (0.92, 1.0, 1.08)}
            img = world.render(cam, "FPV - SYMULACJA")
            publish_frame(hub, "fpv", encode_jpeg(img, 72), 640, 360, pan=g.get("pan", 0.0), tilt=g.get("tilt", 0.0))
            hub.status["fpv"].fps.tick()
            nxt += period
            if self.sleep(nxt - time.monotonic()):
                break
            if nxt < time.monotonic() - 0.5:
                nxt = time.monotonic()
