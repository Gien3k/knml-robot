"""Maszyna stanów napędu: dłoń -> intencja -> tarcza LiDAR -> rampa -> prędkości kół L/R.

Stany (to, co widzi odwiedzający):
  IDLE     CZEKAM    brak dłoni / inny gest / dłoń poza strefą (opcjonalnie "przywołanie" kołami)
  DRIVE    JAZDA     otwarta dłoń 0,35-1,2 m: prosto do przodu, bliżej = szybciej
  REVERSE  WSTECZNY  pięść: prosto do tyłu
  Bez skrętów – miejsce dłoni w obrazie nie ma znaczenia (łatwiej dla odwiedzających).
  ESTOP    STOP      wtargnięcie w tarczę LiDAR-u – bezwzględny priorytet nad kamerą

Kinematyka skid-steer: L = v + w, R = v - w (w > 0 = obrót w prawo; używa go tylko
"przywołanie"), obcięte do ±MAX_SPEED z zachowaniem proporcji.
Rampa: 0 -> max w RAMP_UP_S, hamowanie 2x szybciej, STOP awaryjny bez rampy.
Watchdog: dane z kamery starsze niż PERCEPTION_TIMEOUT_S = brak dłoni.

Moduł jest czysty (bez wątków i sprzętu) – testy podają czas jawnie.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

OPEN_GESTURES = {"FIVE", "FOUR"}     # kciuk bywa źle rozpoznany – FOUR też znaczy "otwarta dłoń"
FIST_GESTURES = {"FIST"}

IDLE, DRIVE, REVERSE, ESTOP = "IDLE", "DRIVE", "REVERSE", "ESTOP"


@dataclass
class DriveParams:
    max_speed: float = 0.5
    ramp_up_s: float = 0.6
    hand_near_m: float = 0.35
    hand_far_m: float = 1.20
    min_throttle: float = 0.25
    hand_ema: float = 0.35
    enable_reverse: bool = True
    reverse_scale: float = 0.6
    perception_timeout_s: float = 0.30
    attract: bool = False
    attract_interval_s: float = 20.0
    attract_duration_s: float = 1.5
    attract_speed: float = 0.3

    @classmethod
    def from_settings(cls, s) -> "DriveParams":
        return cls(**{k: getattr(s, k) for k in cls.__dataclass_fields__})


@dataclass
class HandObs:
    t: float                       # czas obserwacji (monotonic)
    present: bool
    gesture: str | None = None     # FIVE / FIST / ... / None
    dist_m: float | None = None    # odległość dłoni od kamery
    x_norm: float | None = None    # pozycja dłoni w obrazie 0..1 (obraz NIE lustrzany)


@dataclass
class DriveInputs:
    now: float
    hand: HandObs | None
    shield_active: bool = False
    actuation_ok: bool = True      # czy koła mogą się ruszać (kamera/LiDAR/ESP32 OK)


@dataclass
class DriveOutput:
    state: str = IDLE
    sub: str = "no_hand"           # szczegół stanu dla tekstów UI
    request: str = "none"          # intencja z kamery: drive | reverse | none
    throttle: float = 0.0          # 0..1, siła żądania z kamery
    target_l: float = 0.0
    target_r: float = 0.0
    cmd_l: float = 0.0
    cmd_r: float = 0.0
    estop_edge: bool = False       # True w kroku, w którym zaczął się STOP awaryjny


def ramp(cur: float, target: float, up_rate: float, down_rate: float, dt: float) -> float:
    """Ogranicza zmianę prędkości: przyspieszanie up_rate, hamowanie down_rate [jedn./s]."""
    if cur == target:
        return cur
    if cur != 0.0 and (target == 0.0 or (cur > 0) != (target > 0)):
        # hamowanie do zera (przy zmianie kierunku najpierw zatrzymanie)
        return cur - math.copysign(min(abs(cur), down_rate * dt), cur)
    step = (up_rate if abs(target) > abs(cur) else down_rate) * dt
    return cur + max(-step, min(step, target - cur))


class DriveLogic:
    def __init__(self, params: DriveParams) -> None:
        self.p = params
        self.cmd_l = 0.0
        self.cmd_r = 0.0
        self._dist_ema: float | None = None
        self._last_t: float | None = None
        self._last_activity: float | None = None
        self._attract_until = 0.0
        self._attract_t0 = 0.0
        self._was_estop = False

    # ------------------------------------------------------------------
    def throttle_for(self, dist_m: float) -> float:
        p = self.p
        span = max(1e-6, p.hand_far_m - p.hand_near_m)
        x = (p.hand_far_m - dist_m) / span
        return max(p.min_throttle, min(1.0, p.min_throttle + (1.0 - p.min_throttle) * x))

    def step(self, inp: DriveInputs) -> DriveOutput:
        p = self.p
        now = inp.now
        dt = 0.05 if self._last_t is None else max(0.0, min(0.2, now - self._last_t))
        self._last_t = now
        if self._last_activity is None:
            self._last_activity = now
        out = DriveOutput()

        # ---------- 1. tarcza LiDAR: bezwzględny priorytet ----------
        if inp.shield_active:
            self._fill_request(inp, out)          # dla paska decyzji: co "chciała" kamera
            self.cmd_l = self.cmd_r = 0.0
            self._attract_until = 0.0
            self._last_activity = now
            out.state, out.sub = ESTOP, "shield"
            out.estop_edge = not self._was_estop
            self._was_estop = True
            return out
        self._was_estop = False

        # ---------- 2. intencja z kamery ----------
        v = w = 0.0
        self._fill_request(inp, out)
        if out.request in ("drive", "reverse"):
            self._last_activity = now
            self._attract_until = 0.0
            # zawsze prosto: otwarta dłoń = przód, pięść = tył (w zostaje 0)
            if out.request == "drive":
                v = out.throttle * p.max_speed
                out.state, out.sub = DRIVE, ""
            else:
                v = -out.throttle * p.reverse_scale * p.max_speed
                out.state, out.sub = REVERSE, ""
        else:
            out.state = IDLE
            if out.sub not in ("no_hand", "stale"):
                self._last_activity = now          # ktoś jest przy stoisku
            # przywołanie: krótki ruch kół, gdy nikt nie podchodzi
            if p.attract and inp.actuation_ok:
                if now < self._attract_until:
                    phase = (now - self._attract_t0) / max(p.attract_duration_s, 1e-6)
                    a = p.attract_speed * p.max_speed
                    sgn = 1.0 if phase < 0.5 else -1.0
                    v, w = 0.0, sgn * a
                    out.sub = "attract"
                elif out.sub in ("no_hand", "stale") and now - self._last_activity >= p.attract_interval_s:
                    self._attract_t0 = now
                    self._attract_until = now + p.attract_duration_s
                    self._last_activity = now + p.attract_duration_s
                    v, w = 0.0, p.attract_speed * p.max_speed
                    out.sub = "attract"

        # ---------- 3. kinematyka skid-steer ----------
        left, right = v + w, v - w
        m = max(abs(left), abs(right))
        if m > p.max_speed:
            left, right = left * p.max_speed / m, right * p.max_speed / m

        # ---------- 4. blokada sprzętowa ----------
        if not inp.actuation_ok:
            left = right = 0.0
            self.cmd_l = self.cmd_r = 0.0
            self._attract_until = 0.0
        out.target_l, out.target_r = left, right

        # ---------- 5. rampa ----------
        up = p.max_speed / max(p.ramp_up_s, 1e-6)
        self.cmd_l = ramp(self.cmd_l, left, up, 2.0 * up, dt)
        self.cmd_r = ramp(self.cmd_r, right, up, 2.0 * up, dt)
        out.cmd_l, out.cmd_r = self.cmd_l, self.cmd_r
        return out

    def _fill_request(self, inp: DriveInputs, out: DriveOutput) -> None:
        """Co "chce" kamera – niezależnie od tego, czy tarcza na to pozwala."""
        p = self.p
        h = inp.hand
        if h is None or inp.now - h.t > p.perception_timeout_s:
            self._dist_ema = None
            out.request, out.sub = "none", ("stale" if h is not None and h.present else "no_hand")
            return
        if not h.present:
            self._dist_ema = None
            out.request, out.sub = "none", "no_hand"
            return
        d = h.dist_m
        if d is None:
            out.request, out.sub = "none", "no_distance"
            return
        self._dist_ema = d if self._dist_ema is None else self._dist_ema + p.hand_ema * (d - self._dist_ema)
        d = self._dist_ema
        if d < p.hand_near_m:
            out.request, out.sub = "none", "too_close"
            return
        if d > p.hand_far_m:
            out.request, out.sub = "none", "too_far"
            return
        g = h.gesture
        if g in OPEN_GESTURES:
            out.request, out.throttle = "drive", self.throttle_for(d)
        elif g in FIST_GESTURES and p.enable_reverse:
            out.request, out.throttle = "reverse", self.throttle_for(d)
        else:
            out.request, out.sub = "none", "other_gesture"
