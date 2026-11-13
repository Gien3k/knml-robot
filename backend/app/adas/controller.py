"""Kontroler ADAS (20 Hz): gest -> intencja ruchu -> filtry bezpieczeństwa -> koła + głowica.

Kolejność priorytetów (wyższy nadpisuje niższy):
  1. STOP operatora (spacja / API)
  2. STOP AWARYJNY z LiDAR-u (obiekt < ESTOP_DISTANCE od obrysu)
  3. Blokada kierunkowa z LiDAR-u (obiekt < SAFETY_DISTANCE) + "odbicie" (mat)
  4. Wirtualna klatka (geofence, tryb mat)
  5. Blokady sprzętowe: prawdziwe koła nie słuchają symulowanych sensorów
  6. Intencja z gestu (otwarta dłoń = jazda, pięść = stop, pozycja dłoni = skręt)
"""
from __future__ import annotations

import logging
import math
import threading
import time

import numpy as np

from ..config import settings
from ..hub import Hub
from .gestures import FIST, OPEN

log = logging.getLogger("adas")

# Kamera OAK względem środka robota (m, układ robota)
OAK_OFFSET_X = 0.10


def plural_osoba(n: int) -> str:
    if n == 1:
        return "1 osobę"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} osoby"
    return f"{n} osób"


class AdasController(threading.Thread):
    def __init__(self, hub: Hub, lidar_proc) -> None:
        super().__init__(daemon=True, name="adas")
        self.hub = hub
        self.lidar_proc = lidar_proc
        self._halt = threading.Event()
        self.cmd_l = 0.0
        self.cmd_r = 0.0
        self.pan = 0.0
        self.state = "IDLE"
        self._bounce_until = 0.0
        self._bounce_next = 0.0
        self._bounce_reason = ""
        self._prev_v = 0.0
        self._last_state = None
        self.hz = 20.0

    def stop(self) -> None:
        self._halt.set()

    def motion_mode(self) -> str:
        return "mat" if settings.motion_mode == "mat" else "stand"

    def run(self) -> None:
        period = 1.0 / self.hz
        nxt = time.monotonic()
        while not self._halt.is_set():
            try:
                self.step(period)
            except Exception:  # kontroler nie może umrzeć
                log.exception("Błąd kroku kontrolera")
                self.hub.set_drive_cmd(0.0, 0.0, self.pan, settings.gimbal_tilt_deg)
            nxt += period
            delay = nxt - time.monotonic()
            if delay < 0:
                nxt = time.monotonic()
                delay = 0
            self._halt.wait(delay)

    # ------------------------------------------------------------------
    def step(self, dt: float) -> None:
        hub, s = self.hub, settings
        now = time.monotonic()
        mode = self.motion_mode()
        self.lidar_proc.set_motion_mode(mode)
        if hub.recalibrate_request.is_set():
            hub.recalibrate_request.clear()
            self.lidar_proc.recalibrate()
            hub.event("info", "Ponowna kalibracja tła LiDAR / wirtualnej klatki")

        vmax = s.mat_max_wheel_speed if mode == "mat" else s.max_wheel_speed
        wmax = 2.0 * vmax / s.track_width_m * 0.6

        st = {k: hub.status[k].mode for k in hub.SENSORS}
        rover_real = st["rover"] == "real"
        oak_real = st["oak"] == "real"
        lidar_real = st["lidar"] == "real"

        hand = hub.oak.get("hand")
        if hand and now - hand.get("t", 0) > 0.6:
            hand = None
        lid = hub.lidar
        safety = lid.get("safety") or {"state": "OK", "blocked": {}, "min_dist": None}
        tracks = lid.get("tracks") or []
        pose = lid.get("pose") or {"x": 0, "y": 0, "th": 0, "quality": 1, "calibrating": True}

        # ---------------- 6. intencja z gestu / operatora ----------------
        v = w = 0.0
        state, reason = "IDLE", ""
        manual = hub.manual_drive
        if manual and now < manual["until"]:
            v = (manual["L"] + manual["R"]) / 2
            w = (manual["R"] - manual["L"]) / s.track_width_m
            state = "MANUAL"
        else:
            hub.manual_drive = None
            if hand and hand["gesture"] == OPEN and hand["conf"] >= 0.5:
                off = hand["offset"]
                off = 0.0 if abs(off) < 0.12 else (off - math.copysign(0.12, off)) / 0.88
                v = vmax * 0.85
                w = -s.steer_gain * off * wmax
                state = "DRIVE"
            elif hand and hand["gesture"] == FIST and hand["conf"] >= 0.5:
                state = "STOP_GESTURE"

        # ---------------- 5. blokady sprzętowe ----------------
        locked = ""
        if rover_real and state != "MANUAL":
            if not oak_real:
                locked = "Napęd zablokowany: gesty pochodzą z symulacji (brak OAK-D)."
            elif not lidar_real and mode == "mat":
                locked = "Napęd zablokowany: tryb maty wymaga prawdziwego LiDAR-u."
        if locked:
            v = w = 0.0
            state = "LOCKED"
            reason = locked
        if rover_real and not lidar_real and mode == "stand":
            v *= 0.5
            w *= 0.5

        # ---------------- 4. wirtualna klatka ----------------
        geofence = {"active": mode == "mat", "radius": s.geofence_radius_m, "dist": 0.0, "breach": False}
        if mode == "mat":
            px, py, th = pose["x"], pose["y"], pose["th"]
            dist = math.hypot(px, py)
            geofence["dist"] = round(dist, 3)
            if pose.get("calibrating") or pose.get("quality", 0) < 0.3:
                if v != 0:
                    reason = "Słaba lokalizacja ICP – tylko obrót w miejscu."
                v = 0.0
            elif dist > 0.75 * s.geofence_radius_m and v != 0:
                heading = np.array([math.cos(th), math.sin(th)]) * math.copysign(1, v)
                if heading @ np.array([px, py]) > 0:
                    v_try = v
                    v = 0.0
                    geofence["breach"] = True
                    if state in ("DRIVE", "MANUAL"):
                        state = "GEOFENCE"
                        reason = "Granica wirtualnej klatki – ruch na zewnątrz zablokowany."
                    # "odbicie" od granicy klatki – krótki ruch z powrotem do środka strefy
                    if state == "GEOFENCE" and v_try > 0 and now >= self._bounce_next \
                            and not safety.get("blocked", {}).get("rear"):
                        self._bounce_until = now + 0.5
                        self._bounce_next = now + 1.5
                        self._bounce_reason = "geofence"

        # ---------------- 3. blokady kierunkowe + odbicie ----------------
        blocked = safety.get("blocked", {})
        if blocked.get("front") and v > 0:
            v = 0.0
            if mode == "mat" and self._prev_v > 0.02 and not blocked.get("rear") and now >= self._bounce_next:
                self._bounce_until = now + 0.45
                self._bounce_next = now + 1.5
                self._bounce_reason = "obstacle"
            if state in ("DRIVE", "MANUAL"):
                state, reason = "BLOCKED", "front"
        if blocked.get("rear") and v < 0:
            v = 0.0
            if state in ("DRIVE", "MANUAL"):
                state, reason = "BLOCKED", "rear"
        if (blocked.get("left") or blocked.get("right")) and w != 0:
            w = 0.0
            if state in ("DRIVE", "MANUAL"):
                state, reason = "BLOCKED", "side"
        if now < self._bounce_until and not blocked.get("rear"):
            v, w = -0.6 * vmax, 0.0
            state, reason = "BOUNCE", self._bounce_reason

        if state in ("IDLE", "STOP_GESTURE") and safety.get("state") == "WARN":
            zones = [z for z, b in blocked.items() if b]
            state = "BLOCKED"
            reason = zones[0] if zones and zones[0] in ("front", "rear") else ("side" if zones else "front")

        # ---------------- 2. / 1. stop awaryjny ----------------
        if safety.get("state") == "ESTOP":
            v = w = 0.0
            state = "ESTOP"
            self._bounce_until = 0.0
        if hub.manual_estop:
            v = w = 0.0
            state = "OPERATOR_STOP"

        # ---------------- kinematyka skid-steer ----------------
        left = v - w * s.track_width_m / 2
        right = v + w * s.track_width_m / 2
        m = max(abs(left), abs(right), 1e-9)
        if m > vmax:
            left, right = left * vmax / m, right * vmax / m
        emergency = state in ("ESTOP", "OPERATOR_STOP")
        step = s.accel_limit * dt
        if emergency:
            self.cmd_l, self.cmd_r = 0.0, 0.0     # hamowanie natychmiastowe
        else:
            self.cmd_l += float(np.clip(left - self.cmd_l, -step, step))
            self.cmd_r += float(np.clip(right - self.cmd_r, -step, step))
        self._prev_v = (self.cmd_l + self.cmd_r) / 2

        # ---------------- głowica: śledzi najbliższą osobę ----------------
        target = None
        if tracks:
            target = min(tracks, key=lambda t: t["d"])
            bearing = math.degrees(math.atan2(target["y"], target["x"]))
            self.pan += 0.25 * (float(np.clip(bearing, -150, 150)) - self.pan)
        else:
            self.pan *= 0.97
        hub.set_drive_cmd(self.cmd_l, self.cmd_r, round(self.pan, 1), s.gimbal_tilt_deg)

        # ---------------- fuzja OAK <-> LiDAR (do wizualizacji) ----------------
        fused = self._fuse(hub.oak.get("detections") or [], tracks)

        if state != self._last_state:
            if state in ("ESTOP", "GEOFENCE", "LOCKED", "OPERATOR_STOP"):
                hub.event("warn" if state != "ESTOP" else "critical", f"ADAS: {state}")
            self._last_state = state

        self.state = state
        hub.adas = {
            "state": state, "reason": reason, "mode": mode,
            "intent": {"v": round(v, 3), "w": round(w, 3)},
            "cmd": {"L": round(self.cmd_l, 3), "R": round(self.cmd_r, 3)},
            "vmax": vmax, "geofence": geofence, "target": target["id"] if target else None,
            "fused": fused, "gesture": hand,
            "narration": self._narrate(state, reason, hand, tracks, safety, w),
        }

    @staticmethod
    def _fuse(dets: list[dict], tracks: list[dict]) -> dict:
        """Dopasowanie detekcji OAK (xyz kamery) do torów LiDAR (układ robota)."""
        pairs = {}
        for d in dets:
            if d.get("label") != "osoba":
                continue
            x_c, _, z_c = d["xyz"]
            X, Y = z_c + OAK_OFFSET_X, -x_c
            best, bd = None, 0.6
            for t in tracks:
                dd = math.hypot(t["x"] - X, t["y"] - Y)
                if dd < bd:
                    best, bd = t, dd
            if best is not None:
                pairs[str(best["id"])] = round(d["conf"], 2)
        return pairs

    @staticmethod
    def _narrate(state, reason, hand, tracks, safety, w) -> str:
        """Jedno zdanie dla odwiedzających – uzupełnia hasło stanu, z ich perspektywy."""
        md = safety.get("min_dist")
        cm = f"{md * 100:.0f} cm" if md is not None else "bardzo blisko"
        n = sum(1 for t in tracks if t.get("w", 0) >= 0.15)   # szerokość sylwetki, nie dłoń
        if state == "OPERATOR_STOP":
            return "Obsługa stoiska wstrzymała robota. Koła stoją do odwołania."
        if state == "ESTOP":
            return f"Coś jest {cm} od robota. LiDAR ma pierwszeństwo przed gestami, więc koła stanęły."
        if state == "BOUNCE":
            if reason == "geofence":
                return "Robot dotknął granicy strefy na macie i cofa się do środka."
            return "Przeszkoda z przodu, więc robot odsuwa się do tyłu."
        if state == "GEOFENCE":
            return "Dalej jest granica strefy bezpieczeństwa na macie. Robot może tylko skręcać."
        if state == "BLOCKED":
            where = {"front": "z przodu", "rear": "z tyłu", "side": "z boku"}.get(reason, "")
            return f"Przeszkoda {where}, {cm} od robota. Ruch w tę stronę jest zablokowany."
        if state == "LOCKED":
            return reason
        if state == "MANUAL":
            return "Obsługa stoiska testuje silniki."
        if state == "DRIVE":
            if abs(w) > 0.05:
                # w<0: robot skręca w swoje prawo, czyli w lewą stronę stojącego przed nim widza
                side = "lewą" if w < 0 else "prawą"
                return f"Dłoń jest przesunięta w bok, więc robot skręca w Twoją {side} stronę."
            return "Otwarta dłoń to gaz. Przesuń ją w bok, żeby skręcić, albo zaciśnij pięść."
        if state == "STOP_GESTURE":
            return "Pięść zatrzymuje koła. Otwórz dłoń, żeby ruszyć."
        if hand:
            return "Widzę dłoń. Rozłóż palce, żeby ruszyć, albo zaciśnij pięść."
        if n:
            who = "osobę" if n == 1 else plural_osoba(n)
            return f"Widzę {who} przy stoisku. Stań przed robotem i wyciągnij rękę w stronę kamery."
        return "Stań przed robotem i wyciągnij rękę w stronę kamery. Zaciśnięta pięść zatrzymuje koła."
