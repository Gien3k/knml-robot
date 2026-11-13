"""Wspólne elementy sterowników: bazowy wątek, kodowanie JPEG, kolorowanie głębi."""
from __future__ import annotations

import logging
import threading
import time

import cv2
import numpy as np

from ..hub import Hub

log = logging.getLogger("drivers")

LABELS = {"oak": "OAK-D Pro", "lidar": "LiDAR D500", "rover": "Napęd 4WD (ESP32)"}


class Driver(threading.Thread):
    sensor = ""
    kind = "mock"   # real | mock

    def __init__(self, hub: Hub, target: str = "") -> None:
        super().__init__(daemon=True, name=f"{self.sensor}-{self.kind}")
        self.hub = hub
        self.target = target          # port / urządzenie
        self._halt = threading.Event()
        self.error: str | None = None

    def stop(self) -> None:
        self._halt.set()

    @property
    def halted(self) -> bool:
        return self._halt.is_set()

    def sleep(self, s: float) -> bool:
        """Czeka s sekund; True gdy zażądano zatrzymania."""
        return self._halt.wait(max(0.0, s))

    def run(self) -> None:
        try:
            self.loop()
        except Exception as e:  # urządzenie odłączone itp.
            self.error = f"{type(e).__name__}: {e}"
            log.warning("%s (%s) zakończony błędem: %s", LABELS[self.sensor], self.kind, self.error)

    def loop(self) -> None:  # pragma: no cover
        raise NotImplementedError


def encode_jpeg(img: np.ndarray, quality: int = 80) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else b""


def publish_frame(hub: Hub, name: str, jpeg: bytes, w: int, h: int, **meta) -> None:
    header = {"ts": time.time(), "w": w, "h": h, **meta}
    hub.video[name].publish((header, jpeg))


def _build_depth_lut() -> np.ndarray:
    """Sekwencyjna rampa jednego odcienia (niebieski): blisko = jasno, daleko = ciemno."""
    stops = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b", "#0b2140"]
    rgb = np.array([[int(h[i:i + 2], 16) for i in (1, 3, 5)] for h in stops], np.float32)
    xs = np.linspace(0, 255, len(stops))
    lut = np.zeros((256, 1, 3), np.uint8)
    for c in range(3):
        lut[:, 0, 2 - c] = np.interp(np.arange(256), xs, rgb[:, c]).astype(np.uint8)  # BGR
    lut[0, 0] = (25, 26, 26)  # brak pomiaru = kolor tła kafelka
    return lut


DEPTH_LUT = _build_depth_lut()


def colorize_depth(depth_mm: np.ndarray, out_w: int = 320, out_h: int = 180,
                   zmin: float = 300.0, zmax: float = 4000.0) -> np.ndarray:
    small = cv2.resize(depth_mm, (out_w, out_h), interpolation=cv2.INTER_NEAREST).astype(np.float32)
    norm = np.clip((small - zmin) / (zmax - zmin), 0, 1) * 254 + 1
    norm[small <= 0] = 0
    return cv2.applyColorMap(norm.astype(np.uint8), DEPTH_LUT)
