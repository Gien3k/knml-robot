"""Nadzorca sprzętu: hot-plug, przypisanie ról portów i przełączanie symulacji.

Co DETECT_INTERVAL_S sekund:
  1. martwy sterownik (wypięty kabel, 2 s bez danych) -> port zwolniony, status "sim",
  2. nieprzypisane porty -> sondowanie (hw/discovery.py) -> LiDAR / ESP32,
  3. symulacja całego demo działa TYLKO, gdy wszystkie trzy urządzenia są symulowane.
Kamerą zajmuje się osobny wątek (hw/camera.py), uruchamiany tutaj.
"""
from __future__ import annotations

import logging
import os
import threading
import time

from .config import Settings
from .hub import DEVICES, Hub
from .hw.camera import CameraManager
from .hw.discovery import candidate_ports, probe
from .hw.esp32 import Esp32Driver
from .hw.lidar import LidarDriver
from .protocol import waveshare
from .sim.runner import SimRunner

log = logging.getLogger("supervisor")


class Supervisor(threading.Thread):
    def __init__(self, hub: Hub, settings: Settings) -> None:
        super().__init__(daemon=True, name="supervisor")
        self.hub, self.s = hub, settings
        self._halt = threading.Event()
        self.camera = CameraManager(hub, settings)
        self.lidar: LidarDriver | None = None
        self.esp32: Esp32Driver | None = None
        self.sim: SimRunner | None = None
        self.assigned: dict[str, str] = {}          # realpath -> rola
        self.backoff: dict[str, float] = {}         # realpath -> czas następnej próby
        self.probes: dict[str, dict] = {}           # do diagnostyki
        self.ports: list[dict] = []

    # ------------------------------------------------------------------
    def run(self) -> None:
        log.info("Tryb sprzętu: HW_MODE=%s", self.s.hw_mode)
        if self.s.hw_mode != "sim":
            self.camera.start()
        while not self._halt.is_set():
            try:
                self.cycle()
            except Exception:
                log.exception("Błąd cyklu nadzorcy")
            self._halt.wait(self.s.detect_interval_s)

    def shutdown(self) -> None:
        self._halt.set()
        self.camera.stop()
        for d in (self.esp32, self.lidar, self.sim):
            if d is not None:
                d.stop()
        for d in (self.esp32, self.lidar, self.sim):
            if d is not None:
                d.join(timeout=2.0)
        if self.camera.proc is not None:
            CameraManager._kill(self.camera.proc)

    # ------------------------------------------------------------------
    def cycle(self) -> None:
        if self.s.hw_mode == "sim":
            for k in DEVICES:
                self.hub.set_device(k, "sim", message="Tryb symulacji (HW_MODE=sim)")
        else:
            self._check_drivers()
            self._discover()
        self._update_sim()

    def _release(self, drv) -> None:
        for path, role in list(self.assigned.items()):
            if role == drv.role:
                del self.assigned[path]
                self.backoff.pop(path, None)         # sonduj od nowa od razu

    def _check_drivers(self) -> None:
        hub, now = self.hub, time.monotonic()
        if self.lidar is not None:
            if not self.lidar.is_alive():
                msg = f"LiDAR rozłączony ({self.lidar.error or 'koniec pracy'})"
                hub.event("warn", msg)
                self._release(self.lidar)
                self.lidar = None
                hub.clear_scan()
                hub.set_device("lidar", "sim", message=msg)
            else:
                sc = hub.scan
                fresh = sc is not None and sc["source"] == "lidar" and now - sc["t"] <= 1.0
                if fresh:
                    hub.set_device("lidar", "real", self.lidar.port,
                                   f"{sc['hz']:.1f} obr./s · {self.lidar.packets} pakietów")
                elif self.lidar.packets == 0:
                    hub.set_device("lidar", "real", self.lidar.port, "Czekam na pierwszy obrót…", starting=True)
                else:
                    hub.set_device("lidar", "error", self.lidar.port, "Brak świeżych skanów")

        if self.esp32 is not None:
            if not self.esp32.is_alive():
                msg = f"ESP32 rozłączony ({self.esp32.error or 'koniec pracy'})"
                hub.event("warn", msg)
                self._release(self.esp32)
                self.esp32 = None
                hub.wheels = None
                hub.esp32 = {}
                hub.set_device("esp32", "sim", message=msg)
            else:
                silent = now - self.esp32.last_rx
                if silent <= 1.0:
                    v = hub.esp32.get("voltage")
                    batt = ""
                    if v is not None:
                        batt = f" · {v:.2f} V" if v >= waveshare.NO_BATTERY_V else f" · brak ogniw ({v:.2f} V)"
                    hub.set_device("esp32", "real", self.esp32.port, f"{self.esp32.sent} komend{batt}")
                else:
                    hub.set_device("esp32", "error", self.esp32.port, f"Brak odpowiedzi od {silent:.1f} s")

    def _discover(self) -> None:
        s, hub = self.s, self.hub
        need_lidar, need_esp = self.lidar is None, self.esp32 is None
        forced_lidar = os.path.realpath(s.lidar_port) if s.lidar_port else ""
        cands = candidate_ports(s.serial_extra_ids, s.serial_probe_all, (s.lidar_port, s.esp32_port))
        self.ports = [c.to_dict() for c in cands]
        if not (need_lidar or need_esp):
            return
        for c in cands:
            if self._halt.is_set() or not (need_lidar or need_esp):
                break
            if c.realpath in self.assigned or self.backoff.get(c.realpath, 0.0) > time.monotonic():
                continue
            if forced_lidar and c.realpath == forced_lidar:
                if need_lidar:          # LIDAR_PORT: bez sondowania, tylko odczyt
                    self._start_lidar(c.device, c.realpath, "LIDAR_PORT")
                    need_lidar = False
                continue
            res = probe(c.device, allow_query=need_esp)
            self.probes[c.device] = {**res.to_dict(), "port": c.to_dict()}
            log.info("Sondowanie %s: %s (%s)", c.device, res.role, res.detail)
            if res.role == "lidar" and need_lidar:
                self._start_lidar(c.device, c.realpath, res.detail)
                need_lidar = False
            elif res.role == "esp32" and need_esp:
                self._start_esp32(c.device, c.realpath, res.detail)
                need_esp = False
            else:
                self.backoff[c.realpath] = time.monotonic() + s.probe_retry_s
        if need_lidar and hub.devices["lidar"].state != "sim":
            hub.set_device("lidar", "sim", message="Nie wykryto LiDAR-u")
        if need_esp and hub.devices["esp32"].state != "sim":
            hub.set_device("esp32", "sim", message="Nie wykryto ESP32")

    def _start_lidar(self, dev: str, real: str, why: str) -> None:
        self.assigned[real] = "lidar"
        self.lidar = LidarDriver(self.hub, dev, self.s)
        self.lidar.start()
        self.hub.set_device("lidar", "real", dev, f"Wykryto: {why}", starting=True)
        self.hub.event("info", f"LiDAR D500 na {dev} ({why})")

    def _start_esp32(self, dev: str, real: str, why: str) -> None:
        self.assigned[real] = "esp32"
        self.esp32 = Esp32Driver(self.hub, dev, self.s)
        self.esp32.start()
        self.hub.set_device("esp32", "real", dev, f"Wykryto: {why}")
        self.hub.event("info", f"ESP32 na {dev} ({why})")

    # ------------------------------------------------------------------
    def _update_sim(self) -> None:
        hub = self.hub
        all_sim = self.s.hw_mode != "real" and all(hub.devices[k].is_sim for k in DEVICES)
        if all_sim and self.sim is None:
            self.sim = SimRunner(hub, self.s)
            self.sim.start()
            hub.sim_active = True
            hub.event("info", "Symulacja: wszystkie urządzenia symulowane – pełne demo")
        elif not all_sim and self.sim is not None:
            self.sim.stop()
            self.sim.join(timeout=2.0)
            self.sim = None
            hub.sim_active = False
            if hub.devices["oak"].state != "real":
                hub.clear_camera()
            if hub.scan is not None and hub.scan["source"] == "sim":
                hub.clear_scan()
            if self.esp32 is None:
                hub.wheels = None
                hub.esp32 = {}
            hub.event("info", "Symulacja wyłączona – wykryto prawdziwy sprzęt")

    # ------------------------------------------------------------------
    def diagnostics(self) -> dict:
        return {
            "hw_mode": self.s.hw_mode, "sim_active": self.hub.sim_active,
            "ports": self.ports, "assigned": self.assigned,
            "probes": self.probes,
            "backoff_s": {k: round(v - time.monotonic(), 1) for k, v in self.backoff.items()
                          if v > time.monotonic()},
            "lidar": None if self.lidar is None else {"port": self.lidar.port, "packets": self.lidar.packets,
                                                      "crc_errors": self.lidar.crc_errors,
                                                      "error": self.lidar.error},
            "esp32": None if self.esp32 is None else {"port": self.esp32.port, "sent": self.esp32.sent,
                                                      "error": self.esp32.error},
            "camera": {"last_check": self.camera.last_check, "mode": self.camera.mode,
                       "running": self.camera.proc is not None, "fails": self.camera.fails,
                       "person_detect_enabled": self.camera.person_enabled,
                       "person_detect_active": self.camera.persons_active,
                       "person_strikes": self.camera.person_strikes},
        }
