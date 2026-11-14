"""Pętla sterowania 20 Hz: stan sensorów -> DriveLogic -> komenda dla ESP32 + teksty dla UI.

Zasady dopuszczenia ruchu kół (bezpieczniejszy wariant):
  * symulacja (wszystko symulowane): koła wirtualne, wystarczy nauczona tarcza,
  * prawdziwy sprzęt: koła ruszają WYŁĄCZNIE, gdy kamera jest prawdziwa i daje dane,
    a prawdziwy LiDAR daje świeże skany (<= LIDAR_STALE_S) z nauczonym tłem.
    Bez LiDAR-u koła stoją, chyba że DRIVE_WITHOUT_LIDAR=1.
"""
from __future__ import annotations

import logging
import threading
import time

from .config import Settings
from .hub import Hub
from .logic.drive import DRIVE, ESTOP, IDLE, REVERSE, DriveInputs, DriveLogic, DriveOutput, DriveParams, HandObs

log = logging.getLogger("control")

STATE_LABEL = {IDLE: "CZEKAM", DRIVE: "JAZDA", REVERSE: "WSTECZNY", ESTOP: "STOP"}


def _m(v: float) -> str:
    return f"{v:.2f}".replace(".", ",")


def _cm(v: float) -> str:
    return f"{round(v * 100):d}"


def header_text(out: DriveOutput, lock: str, s: Settings) -> tuple[str, str]:
    if out.state == ESTOP:
        return "Radar wykrył rękę przy robocie · Hamowanie awaryjne ma pierwszeństwo przed kamerą", "stop"
    if lock == "calibration":
        return "Trwa kalibracja radaru · Koła są zablokowane", "muted"
    if lock == "no_camera":
        return "Czekam na kamerę · Za chwilę będzie można sterować dłonią", "muted"
    if lock == "learning":
        return "Robot poznaje otoczenie stoiska · Za chwilę możesz zaczynać", "accent"
    if lock in ("no_lidar", "lidar_stale"):
        return "Koła czekają na radar · Bez niego robot nie ruszy", "muted"
    if out.state == DRIVE:
        return "Im bliżej kamery, tym szybciej · Zaciśnij pięść, aby jechać do tyłu", "go"
    if out.state == REVERSE:
        return "Pięść to jazda do tyłu · Otwórz dłoń, aby jechać do przodu", "accent"
    if out.sub == "too_close":
        return (f"Trochę dalej · Trzymaj dłoń {_cm(s.hand_near_m)}–{_cm(s.hand_far_m)} cm od kamery",
                "accent")
    if out.sub == "too_far":
        return "Podejdź bliżej · Pokaż otwartą dłoń z odległości ok. 1 metra", "accent"
    if out.sub in ("other_gesture", "no_distance"):
        return "Rozłóż wszystkie palce, aby ruszyć · Zaciśnij pięść, aby cofnąć", "accent"
    return "Pokaż otwartą dłoń do kamery, aby ruszyć kołami · Włóż rękę blisko radaru, aby zahamować", "accent"


def decision_texts(out: DriveOutput, lock: str, shield, cam_live: bool, lidar_live: bool) -> dict:
    # --- kamera ---
    if not cam_live:
        cam = ("BRAK OBRAZU", "muted")
    elif out.request == "drive":
        cam = (f"JEDŹ {round(out.throttle * 100)}%", "go")
    elif out.request == "reverse":
        cam = (f"COFAJ {round(out.throttle * 100)}%", "accent")
    else:
        cam = {"too_close": ("ZA BLISKO", "muted"), "too_far": ("ZA DALEKO", "muted"),
               "other_gesture": ("INNY GEST", "muted"), "no_distance": ("BRAK GŁĘBI", "muted")
               }.get(out.sub, ("BRAK DŁONI", "muted"))
    # --- LiDAR ---
    if not lidar_live:
        lid = ("BRAK DANYCH", "muted")
    elif not shield.ready:
        lid = (f"UCZĘ SIĘ {round(shield.learn_progress * 100)}%", "accent")
    elif shield.active:
        d = shield.nearest_m
        lid = (f"PRZESZKODA {_m(d)} m" if d is not None else "PRZESZKODA", "stop")
    else:
        lid = ("CZYSTO", "go")
    # --- decyzja ---
    if out.state == ESTOP:
        dec = ("HAMUJ", "stop")
    elif lock:
        dec = ("STOJĘ", "muted")
    elif out.state == DRIVE:
        dec = ("JEDŹ", "go")
    elif out.state == REVERSE:
        dec = ("COFAJ", "accent")
    elif out.sub == "attract":
        dec = ("MACHAM KOŁAMI", "accent")
    else:
        dec = ("STOJĘ", "muted")
    return {"camera": {"text": cam[0], "tone": cam[1]}, "lidar": {"text": lid[0], "tone": lid[1]},
            "decision": {"text": dec[0], "tone": dec[1]}}


class ControlLoop(threading.Thread):
    HZ = 20.0

    def __init__(self, hub: Hub, settings: Settings) -> None:
        super().__init__(daemon=True, name="control")
        self.hub, self.s = hub, settings
        self.logic = DriveLogic(DriveParams.from_settings(settings))
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        period = 1.0 / self.HZ
        nxt = time.monotonic()
        while not self._halt.is_set():
            try:
                self.step(time.monotonic())
            except Exception:                # pętla sterowania nie może umrzeć
                log.exception("Błąd kroku sterowania")
                self.hub.drive_cmd = {"L": 0.0, "R": 0.0, "t": time.monotonic(), "allowed": False}
            nxt += period
            delay = nxt - time.monotonic()
            if delay < 0:
                nxt, delay = time.monotonic(), 0.0
            self._halt.wait(delay)

    def step(self, now: float) -> DriveOutput:
        hub, s = self.hub, self.s
        sim = hub.sim_active
        dev = hub.devices
        cam_real = dev["oak"].state == "real"
        cam_live = hub.cam_t > 0 and now - hub.cam_t < 1.0 and (sim or cam_real)

        sc = hub.scan
        src_ok = sc is not None and (sc["source"] == "sim" if sim else sc["source"] == "lidar")
        lidar_live = src_ok and now - sc["t"] <= s.lidar_stale_s
        st = hub.shield_state
        shield_ready = lidar_live and st.ready

        if sim:
            lock = "" if shield_ready else ("learning" if lidar_live else "no_lidar")
        elif not (cam_real and cam_live):
            lock = "no_camera"
        elif shield_ready:
            lock = ""
        elif s.drive_without_lidar and sc is None:
            lock = ""
        elif lidar_live:
            lock = "learning"
        else:
            lock = "lidar_stale" if sc is not None else "no_lidar"

        if hub.calib_active:
            lock = "calibration"

        obs = None
        if cam_live:
            h = hub.hand
            if h is not None and h.get("present"):
                obs = HandObs(t=h["t"], present=True, gesture=h.get("gesture"),
                              dist_m=h.get("dist_m"), x_norm=h.get("x_norm"))
            else:
                obs = HandObs(t=hub.cam_t, present=False)

        out = self.logic.step(DriveInputs(now=now, hand=obs, shield_active=bool(st.active and lidar_live),
                                          actuation_ok=not lock))
        hub.drive_cmd = {"L": out.cmd_l, "R": out.cmd_r, "t": now, "allowed": not lock}
        if out.estop_edge:
            hub.estop_event.set()

        header, tone = header_text(out, lock, s)
        hub.drive = {
            "state": out.state, "label": STATE_LABEL[out.state], "sub": out.sub, "lock": lock,
            "request": out.request, "throttle": round(out.throttle, 3),
            "target": {"L": round(out.target_l, 3), "R": round(out.target_r, 3)},
            "cmd": {"L": round(out.cmd_l, 3), "R": round(out.cmd_r, 3)},
            "header": {"text": header, "tone": tone},
            "decision": decision_texts(out, lock, st, cam_live, lidar_live),
            "cam_live": cam_live, "lidar_live": lidar_live,
        }
        return out
