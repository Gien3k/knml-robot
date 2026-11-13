"""Nadzorca sprzętu: hot-plug i automatyczny fallback do symulacji.

Co DETECT_INTERVAL_S sekund:
  1. prawdziwy sterownik, który padł (odłączony kabel)  -> zdarzenie + symulacja,
  2. dla sensorów bez prawdziwego sterownika -> autodetekcja; jeśli znaleziono
     urządzenie, symulacja jest zatrzymywana i startuje prawdziwy sterownik,
  3. brakujące sensory -> symulacja (HW_MODE=auto) albo OFF (HW_MODE=real).
Nieudane sondowanie portu jest ponawiane z opóźnieniem, żeby nie resetować w kółko
urządzeń, które nie są naszym sprzętem.
"""
from __future__ import annotations

import logging
import threading
import time

from .config import settings
from .drivers import detect
from .drivers.common import LABELS, Driver
from .drivers.fpv import FpvMock, FpvReal
from .drivers.lidar import LidarMock, LidarReal
from .drivers.oak import OakMock, OakReal
from .drivers.rover import RoverMock, RoverReal
from .hub import Hub

log = logging.getLogger("supervisor")


class Supervisor(threading.Thread):
    def __init__(self, hub: Hub, lidar_proc) -> None:
        super().__init__(daemon=True, name="supervisor")
        self.hub = hub
        self.proc = lidar_proc
        self.drivers: dict[str, Driver | None] = {k: None for k in hub.SENSORS}
        self._halt = threading.Event()
        self._port_backoff: dict[tuple[str, str], float] = {}
        self._lost: dict[str, str] = {}

    # ------------------------------------------------------------------
    def run(self) -> None:
        log.info("Tryb sprzętu: HW_MODE=%s", settings.hw_mode)
        while not self._halt.is_set():
            try:
                self.cycle()
            except Exception:
                log.exception("Błąd cyklu nadzorcy")
            self._halt.wait(settings.detect_interval_s)

    def shutdown(self) -> None:
        self._halt.set()
        for d in self.drivers.values():
            if d:
                d.stop()
        for d in self.drivers.values():
            if d:
                d.join(timeout=2.0)

    # ------------------------------------------------------------------
    def _alive(self, name: str, kind: str | None = None) -> bool:
        d = self.drivers[name]
        return d is not None and d.is_alive() and (kind is None or d.kind == kind)

    def _start(self, name: str, drv: Driver) -> None:
        old = self.drivers[name]
        if old is not None:
            old.stop()
            old.join(timeout=2.0)
        self.drivers[name] = drv
        drv.start()

    def _used_ports(self) -> set[str]:
        return {d.target for d in self.drivers.values() if d and d.kind == "real" and d.is_alive()}

    def cycle(self) -> None:
        hub, mode = self.hub, settings.hw_mode

        # 1. utracone prawdziwe urządzenia
        for name, d in self.drivers.items():
            if d is not None and d.kind == "real" and not d.is_alive():
                msg = f"{LABELS[name]}: rozłączono ({d.error or 'koniec pracy'})"
                log.warning(msg)
                hub.event("warn", msg + (" – przełączam na symulację" if mode == "auto" else ""))
                self._lost[name] = d.error or ""
                self.drivers[name] = None

        # 2. autodetekcja
        if mode != "mock":
            self._detect()

        # 3. brakujące -> symulacja / off
        for name in hub.SENSORS:
            if self._alive(name):
                continue
            if mode == "real":
                hub.set_status(name, "off", "", "Nie wykryto urządzenia")
                continue
            msg = ""
            if name in self._lost:
                msg = "Urządzenie odłączone – dane symulowane"
            elif mode == "mock":
                msg = "Tryb symulacji (HW_MODE=mock)"
            factory = {"oak": lambda: OakMock(hub, msg), "lidar": lambda: LidarMock(hub, self.proc, msg),
                       "rover": lambda: RoverMock(hub, msg), "fpv": lambda: FpvMock(hub, msg)}[name]
            self._start(name, factory())

    def _port_allowed(self, kind: str, port: str) -> bool:
        return time.monotonic() >= self._port_backoff.get((kind, port), 0.0)

    def _detect(self) -> None:
        hub, s = self.hub, settings

        # OAK-D (USB, Luxonis 03e7)
        if not self._alive("oak", "real") and detect.oak_available():
            log.info("Wykryto OAK-D – uruchamiam sterownik")
            self._start("oak", OakReal(hub, "usb"))
            self._lost.pop("oak", None)

        # LiDAR – nasłuch pakietów 0x54 0x2C
        if not self._alive("lidar", "real"):
            ports = [s.lidar_port] if s.lidar_port else detect.serial_candidates()
            for port in ports:
                if port in self._used_ports() or not self._port_allowed("lidar", port):
                    continue
                if detect.probe_lidar(port, s.lidar_baud):
                    log.info("Wykryto LiDAR D500 na %s", port)
                    self._start("lidar", LidarReal(hub, port, self.proc))
                    self._lost.pop("lidar", None)
                    break
                self._port_backoff[("lidar", port)] = time.monotonic() + 6.0

        # ESP32 – zapytanie {"T":130}
        if not self._alive("rover", "real"):
            ports = [s.rover_port] if s.rover_port else detect.serial_candidates()
            for port in ports:
                if port in self._used_ports() or not self._port_allowed("rover", port):
                    continue
                if detect.probe_rover(port, s.rover_baud):
                    log.info("Wykryto ESP32 RaspRover na %s", port)
                    self._start("rover", RoverReal(hub, port))
                    self._lost.pop("rover", None)
                    break
                self._port_backoff[("rover", port)] = time.monotonic() + 12.0

        # Kamera FPV (UVC)
        if not self._alive("fpv", "real"):
            cams = [{"device": s.fpv_device}] if s.fpv_device else detect.video_candidates()
            for c in cams:
                dev = c["device"]
                if not self._port_allowed("fpv", dev):
                    continue
                import cv2
                cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
                ok = cap.isOpened() and cap.read()[0]
                cap.release()
                if ok:
                    log.info("Wykryto kamerę FPV %s (%s)", dev, c.get("name", ""))
                    self._start("fpv", FpvReal(hub, dev))
                    self._lost.pop("fpv", None)
                    break
                self._port_backoff[("fpv", dev)] = time.monotonic() + 10.0
