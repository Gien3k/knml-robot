"""Centralny stan systemu współdzielony między wątkami sterowników a pętlą asyncio.

Zasada: wątki sensorów *publikują* najnowszy stan (nadpisują), konsumenci zawsze
czytają tylko najnowszą wartość. Nic nie jest kolejkowane – to klucz do niskich
opóźnień (stara klatka wideo jest bezwartościowa).
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


class LatestSlot:
    """Slot "tylko najnowsza wartość" z powiadamianiem asyncio z dowolnego wątku."""

    def __init__(self) -> None:
        self._item: Any = None
        self._seq = 0
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._event: asyncio.Event | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._event = asyncio.Event()

    def publish(self, item: Any) -> None:
        with self._lock:
            self._item = item
            self._seq += 1
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._wake)
            except RuntimeError:  # pętla zamknięta przy wyłączaniu
                pass

    def _wake(self) -> None:
        ev = self._event
        self._event = asyncio.Event()
        if ev is not None:
            ev.set()

    @property
    def seq(self) -> int:
        return self._seq

    def get(self) -> tuple[int, Any]:
        with self._lock:
            return self._seq, self._item

    async def next(self, last_seq: int) -> tuple[int, Any]:
        """Czeka na wartość nowszą niż last_seq i zwraca (seq, item)."""
        while self._seq == last_seq:
            ev = self._event
            assert ev is not None, "LatestSlot.bind() nie zostało wywołane"
            await ev.wait()
        return self.get()


class FpsCounter:
    def __init__(self, alpha: float = 0.15) -> None:
        self._last: float | None = None
        self._fps = 0.0
        self._alpha = alpha

    def tick(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if self._last is not None:
            dt = now - self._last
            if dt > 0:
                inst = 1.0 / dt
                self._fps = inst if self._fps == 0 else self._fps + self._alpha * (inst - self._fps)
        self._last = now

    @property
    def value(self) -> float:
        if self._last is None or time.monotonic() - self._last > 2.0:
            return 0.0
        return self._fps


@dataclass
class SensorStatus:
    mode: str = "off"          # real | mock | off
    detail: str = ""           # np. port, numer seryjny
    message: str = ""          # komunikat dla operatora
    fps: FpsCounter = field(default_factory=FpsCounter)
    since: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "detail": self.detail,
            "message": self.message,
            "fps": round(self.fps.value, 1),
            "since": self.since,
        }


class Hub:
    SENSORS = ("oak", "lidar", "rover", "fpv")

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.video: dict[str, LatestSlot] = {k: LatestSlot() for k in ("oak", "depth", "fpv")}
        self.scan_slot = LatestSlot()
        self.status: dict[str, SensorStatus] = {k: SensorStatus() for k in self.SENSORS}

        # Dane publikowane przez sensory (podmieniane w całości – atomowo pod GIL)
        self.oak: dict = {"detections": [], "hand": None, "t": 0.0, "latency_ms": None,
                          "nn": False, "nn_fps": 0.0}
        self.lidar: dict = {"tracks": [], "safety": None, "pose": None, "t": 0.0, "hz": 0.0}
        self.rover: dict = {"wheels": {"L": 0.0, "R": 0.0}, "battery_v": None,
                            "gimbal": {"pan": 0.0, "tilt": 0.0}, "imu": {}, "t": 0.0}
        self.adas: dict = {}
        self.perf: dict = {}

        # Komenda napędu (pisze kontroler, czyta sterownik ESP32)
        self._drive_cmd = {"L": 0.0, "R": 0.0, "pan": 0.0, "tilt": 0.0, "t": 0.0}

        # Polecenia operatora
        self.manual_estop = False
        self.manual_drive: dict | None = None          # {"L","R","until"}
        self.recalibrate_request = threading.Event()

        self.events: deque[dict] = deque(maxlen=30)
        self.nn_fps = FpsCounter()

    # ---- pętla asyncio ----
    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        for s in self.video.values():
            s.bind(loop)
        self.scan_slot.bind(loop)

    # ---- zdarzenia ----
    def event(self, level: str, msg: str) -> None:
        self.events.append({"t": time.time(), "level": level, "msg": msg})

    # ---- napęd ----
    def set_drive_cmd(self, left: float, right: float, pan: float, tilt: float) -> None:
        self._drive_cmd = {"L": left, "R": right, "pan": pan, "tilt": tilt, "t": time.monotonic()}

    def get_drive_cmd(self, timeout_s: float) -> dict:
        cmd = dict(self._drive_cmd)
        cmd["stale"] = time.monotonic() - cmd["t"] > timeout_s
        if cmd["stale"]:
            cmd["L"] = cmd["R"] = 0.0
        return cmd

    def set_status(self, name: str, mode: str, detail: str = "", message: str = "") -> None:
        st = self.status[name]
        if st.mode != mode:
            st.since = time.time()
            st.fps = FpsCounter()
        st.mode, st.detail, st.message = mode, detail, message
