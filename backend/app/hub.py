"""Centralny stan systemu współdzielony przez wątki sprzętu, symulację i serwer WWW.

Zasada: producenci *nadpisują* najnowszy stan, konsumenci czytają tylko najnowszą
wartość. Nic nie jest kolejkowane – stara klatka czy stary skan są bezwartościowe.
"""
from __future__ import annotations

import asyncio
import datetime
import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np

from .config import Settings
from .logic.people import PeopleTracker, clusters
from .logic.shield import Shield, ShieldState

log = logging.getLogger("hub")

DEVICES = ("oak", "lidar", "esp32")
LABELS = {"oak": "OAK-D Pro", "lidar": "LiDAR D500", "esp32": "ESP32"}


@dataclass
class LidarGeom:
    """Ustawienie LiDAR-u względem robota: kąt robota = (-surowy jeśli clockwise, inaczej +surowy) + offset.
    Zmieniane na żywo przez kreator kalibracji (calibration.py)."""
    offset_deg: float = 0.0
    clockwise: bool = True
WHEELS_STOPPED = 0.02        # [m/s] poniżej = koło stoi
WHEELS_MOVING = 0.05


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
            except RuntimeError:        # pętla zamknięta przy wyłączaniu
                pass

    def _wake(self) -> None:
        ev, self._event = self._event, asyncio.Event()
        if ev is not None:
            ev.set()

    def get(self) -> tuple[int, Any]:
        with self._lock:
            return self._seq, self._item

    async def next(self, last_seq: int, timeout: float = 5.0) -> tuple[int, Any]:
        """Czeka na wartość nowszą niż last_seq (albo timeout) i zwraca (seq, item)."""
        if self._seq == last_seq and self._event is not None:
            try:
                await asyncio.wait_for(self._event.wait(), timeout)
            except asyncio.TimeoutError:
                pass
        return self.get()


class DeviceStatus:
    """Stan urządzenia dla UI: sim (szara kropka) | real (zielona) | error (czerwona)."""

    def __init__(self) -> None:
        self.state = "sim"
        self.starting = False
        self.detail = ""
        self.message = "Nie wykryto urządzenia"
        self.since = time.time()

    def set(self, state: str, detail: str = "", message: str = "", starting: bool = False) -> bool:
        changed = state != self.state or starting != self.starting
        if changed:
            self.since = time.time()
        self.state, self.detail, self.message, self.starting = state, detail, message, starting
        return changed

    @property
    def is_sim(self) -> bool:
        return self.state == "sim" and not self.starting

    def to_dict(self) -> dict:
        return {"state": self.state, "starting": self.starting, "detail": self.detail,
                "message": self.message, "since": self.since}


class Hub:
    def __init__(self, settings: Settings) -> None:
        s = self.s = settings
        self.lock = threading.RLock()
        self.devices = {k: DeviceStatus() for k in DEVICES}
        self.sim_active = False

        # --- kamera ---
        self.frame_slot = LatestSlot()          # (jpeg: bytes, meta: dict)
        self.hand: dict | None = None           # ostatnia obserwacja dłoni (t = monotonic)
        self.cam_people: dict = {"t": 0.0, "list": [], "source": None}   # osoby z kamery
        self.cam_t = 0.0                        # ostatnia wiadomość z kamery
        self.cam_info: dict = {}

        # --- "jak widzi robot": głębia / podczerwień (osobny strumień MJPEG) ---
        self.vision_slot = LatestSlot()         # (jpeg: bytes, meta: dict)
        self.vision_t = 0.0                     # ostatnia klatka (monotonic)
        self.vision_lens: str | None = None     # czego dotyczy ostatnia klatka: depth | ir
        self.lens: dict = {"lens": "color", "scene": "drive", "auto": True, "progress": 0.0}
        self.lens_request: str | None = None    # wybór z ekranu / API (color | depth | ir | auto)

        # --- tryb ekranu: menu (wybór gry) | drive (jazda dłonią) | game (papier, kamień, nożyce) ---
        self.mode: dict = {"mode": "menu" if s.menu else "drive", "menu": s.menu, "back_in": None}
        # wybór z ekranu: (drive | game | menu, numer ekranu, na którym go zrobiono albo None)
        self.mode_request: tuple[str, int | None] | str | None = None
        # lustro obrazu wspólne dla wszystkich ekranów (kursor dłoni wszędzie trafia w tę samą kartę)
        self.mirror: bool = bool(settings.oak_mirror)
        self.clients: dict[str, dict] = {}      # otwarte strony: {id: {build, ua, t}} (POST /api/hello)

        # --- gra: papier, kamień, nożyce ---
        self.game: dict = {"active": False, "available": False}
        self.game_request: tuple[str, int | None] | str | None = None    # start | stop (+ numer ekranu)
        self.oled: list[str] | None = None      # 4 linie na ekranik łazika; None = ekran firmware'u

        # --- LiDAR + tarcza + ludzie ---
        self.shield = Shield(s.shield_radius_m, s.shield_margin_m, s.shield_hysteresis_m,
                             s.shield_release_s, s.shield_learn_s,
                             absorb_s=s.shield_absorb_s, forget_s=s.shield_forget_s)
        self.moved_t = 0.0                      # kiedy radar wykrył przesunięcie robota
        self.shield_state = ShieldState(radius_m=s.shield_radius_m)
        self.people = PeopleTracker()
        self.scan: dict | None = None
        self.scan_seq = 0
        self._scan_source: str | None = None
        self.recalibrate_request = threading.Event()
        self.lidar_geom = LidarGeom(s.lidar_angle_offset_deg, s.lidar_clockwise)
        self.calib_active = False               # kreator kalibracji: koła zablokowane
        self.estop_event = threading.Event()    # budzi sterownik ESP32 natychmiast

        # --- napęd ---
        self.drive: dict = {}                   # wynik pętli sterowania (dla UI)
        self.drive_cmd = {"L": 0.0, "R": 0.0, "t": 0.0, "allowed": False}
        self.wheels: dict | None = None         # prędkości zmierzone / symulowane
        self.esp32: dict = {}                   # telemetria (napięcie itd.)
        self.battery: dict | None = None        # {"v": 11.4, "level": ok|low|critical}

        # --- czas reakcji hamulca ---
        self.brake: dict = {"last_s": None, "wheels_s": None, "record_s": None, "count": 0,
                            "t": 0.0, "day": datetime.date.today().isoformat()}
        self._brake_pending: dict | None = None

        self.events: deque[dict] = deque(maxlen=60)

    # ------------------------------------------------------------------ ogólne
    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.frame_slot.bind(loop)
        self.vision_slot.bind(loop)

    def event(self, level: str, msg: str) -> None:
        self.events.append({"t": time.time(), "level": level, "msg": msg})
        getattr(log, "warning" if level in ("warn", "error") else "info")(msg)

    def set_device(self, name: str, state: str, detail: str = "", message: str = "",
                   starting: bool = False) -> None:
        if self.devices[name].set(state, detail, message, starting):
            log.info("%s -> %s%s %s", LABELS[name], state, " (start)" if starting else "", message)

    # ------------------------------------------------------------------ LiDAR
    def on_scan(self, ranges: np.ndarray, now: float, source: str, hz: float = 10.0) -> ShieldState:
        with self.lock:
            if source != self._scan_source or self.recalibrate_request.is_set():
                if self.recalibrate_request.is_set():
                    self.event("info", "Ponowna nauka tła LiDAR-u")
                self.recalibrate_request.clear()
                self.shield.reset()
                self.people.reset()
                self._scan_source = source
            prev = self.shield_state.active
            moving = self.wheels_moving(now)
            st = self.shield.update(ranges, now, absorb_ok=not moving)
            self.shield_state = st
            if st.moved:
                self.moved_t = now
                self.people.reset()
                self.event("warn", "LiDAR: robot przesunięty – ponowna nauka tła (koła stoją)")
            if st.absorbed:
                log.info("LiDAR: %d koszyków nieruchomego przedmiotu wchłoniętych do tła", st.absorbed)

            # ludzie: obiekty z LiDAR-u + potwierdzenie osobami z kamery
            self.people.update(clusters(ranges, st.fg_bins, self.s.people_max_range_m) if st.ready else [], now)
            cam = self.camera_people_xy(now)
            unmatched = self.people.fuse([(c["map"][0], c["map"][1], c["conf"]) for c in cam], now)
            tracks, count = self.people.export(now)
            self.scan_seq += 1
            self.scan = {"t": now, "seq": self.scan_seq, "source": source, "hz": round(hz, 1),
                         "r": np.round(np.asarray(ranges) * 1000).astype(int).tolist(),
                         "fg": st.fg_bins, "tracks": tracks, "count": count,
                         "cam_only": [cam[i]["map"] for i in unmatched
                                      if cam[i]["dist_m"] <= self.s.people_max_range_m]}
        if st.active and not prev:
            self.estop_event.set()                  # stop kół zawsze
            if moving:                              # pomiar "Stop w 0,1 s" tylko, gdy koła się kręciły
                self._brake_start(st.trigger_t if st.trigger_t is not None else now, now)
            self.event("warn", "LiDAR: wtargnięcie w strefę bezpieczeństwa – STOP")
        elif self._brake_pending and "t_sent" not in self._brake_pending and now - self._brake_pending["t_decide"] > 1.0:
            self._brake_finish(self._brake_pending["t_decide"])     # ESP32 nie potwierdził – czas decyzji
        return st

    def clear_scan(self) -> None:
        with self.lock:
            self.scan = None
            self._scan_source = None
            self.shield.reset()
            self.people.reset()
            self.shield_state = ShieldState(radius_m=self.s.shield_radius_m)

    # ------------------------------------------------------------------ kamera
    def on_hand(self, obs: dict | None, now: float, source: str) -> None:
        self.hand = None if obs is None else {**obs, "t": now, "source": source}
        self.cam_t = now

    def on_people(self, people: list[dict] | None, now: float, source: str) -> None:
        """Osoby z kamery: bbox (0..1 obrazu NIE lustrzanego), pewność, odległość [m]."""
        if people is not None:
            self.cam_people = {"t": now, "list": people, "source": source}

    def camera_people_xy(self, now: float) -> list[dict]:
        """Osoby z kamery przeliczone do układu robota (środek LiDAR-u)."""
        cp = self.cam_people
        if now - cp["t"] > 0.6:
            return []
        s = self.s
        k = math.tan(math.radians(s.oak_hfov_deg) / 2)
        out = []
        for p in cp["list"]:
            d = p.get("dist_m")
            if not d:
                continue
            x0, _, x1, _ = p["bbox"]
            xc = ((x0 + x1) / 2 - 0.5) * 2.0 * d * k
            out.append({**p, "map": [round(d + s.cam_offset_x, 3), round(-xc + s.cam_offset_y, 3)]})
        return out

    def on_frame(self, jpeg: bytes, w: int, h: int) -> None:
        self.frame_slot.publish((jpeg, {"w": w, "h": h, "t": time.time()}))

    def on_vision(self, jpeg: bytes, lens: str, w: int, h: int) -> None:
        """Klatka głębi (pokolorowana) albo podczerwieni – tylko gdy ekran ją pokazuje."""
        self.vision_t = time.monotonic()
        self.vision_lens = lens
        self.vision_slot.publish((jpeg, {"w": w, "h": h, "lens": lens, "t": time.time()}))

    def vision_live(self, now: float) -> str | None:
        """Soczewka, z której przychodzą świeże klatki (None = brak / stare)."""
        return self.vision_lens if self.vision_t and now - self.vision_t < 1.0 else None

    def clear_camera(self) -> None:
        self.hand = None
        self.cam_people = {"t": 0.0, "list": [], "source": None}
        self.cam_t = 0.0
        self.cam_info = {}
        self.vision_t = 0.0
        self.vision_lens = None

    # ------------------------------------------------------------------ koła i hamulec
    def wheels_moving(self, now: float) -> bool:
        """Koła się kręcą albo robot ma polecenie ruchu (tylko wtedy hamowanie ma sens dla widza)."""
        w = self.wheels
        if w is not None and now - w.get("t", now) <= 1.0 and max(abs(w["L"]), abs(w["R"])) > WHEELS_MOVING:
            return True
        c = self.drive_cmd
        return bool(c.get("allowed")) and max(abs(c["L"]), abs(c["R"])) > 0.02 and abs(now - c.get("t", 0.0)) < 0.5

    def on_wheels(self, left: float, right: float, source: str, now: float) -> None:
        self.wheels = {"L": left, "R": right, "source": source, "t": now}
        p = self._brake_pending
        if p and p.get("moving") and "wheels_s" not in p and "t_sent" in p and source in ("esp32", "sim") \
                and abs(left) < WHEELS_STOPPED and abs(right) < WHEELS_STOPPED:
            p["wheels_s"] = round(now - p["t_detect"], 3)
            self.brake["wheels_s"] = p["wheels_s"]

    def on_stop_sent(self, t_sent: float) -> None:
        """Sterownik ESP32 właśnie wysłał STOP po wtargnięciu w tarczę."""
        p = self._brake_pending
        if p and "t_sent" not in p:
            self._brake_finish(t_sent)

    def _brake_start(self, t_detect: float, t_decide: float) -> None:
        w = self.wheels
        measurable = w is not None and w.get("source") in ("esp32", "sim")
        moving = measurable and max(abs(w["L"]), abs(w["R"])) > WHEELS_MOVING
        self._brake_pending = {"t_detect": t_detect, "t_decide": t_decide, "moving": moving}
        if self.devices["esp32"].state != "real":
            self._brake_finish(t_decide)        # symulacja: decyzja = stop kół w tym samym kroku

    def _brake_finish(self, t_sent: float) -> None:
        p = self._brake_pending
        if p is None:
            return
        p["t_sent"] = t_sent
        react = round(max(0.0, t_sent - p["t_detect"]), 3)
        b = self.brake
        today = datetime.date.today().isoformat()
        if b["day"] != today:
            b.update(day=today, record_s=None, count=0)
        b["last_s"] = react
        b["wheels_s"] = None
        b["count"] += 1
        b["t"] = time.time()
        if b["record_s"] is None or react < b["record_s"]:
            b["record_s"] = react
        log.info("Hamulec: reakcja %.3f s (rekord dnia %.3f s)", react, b["record_s"])
