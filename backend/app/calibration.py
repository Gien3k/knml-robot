"""Kreator kalibracji radaru (LiDAR) uruchamiany z ekranu stoiska.

Ukryte wejście: 5 szybkich kliknięć w logo Koła + hasło (CALIB_PASSWORD, domyślnie KNML).
Ekran prowadzi krok po kroku, z procentem postępu:

  1. clear   ( 0–20 %)  wszyscy odsuwają się, radar uczy się pustego stoiska,
  2. front   (20–45 %)  osoba ok. 1 m na wprost kamery – pary kierunków kamera / LiDAR,
  3. side    (45–70 %)  ta osoba przechodzi w bok przed kamerą – kierunek obrotu LiDAR-u,
  4. relearn (70–90 %)  nowe ustawienie kąta + ponowna nauka tła (wszyscy z dala),
  5. brake   (90–100 %) dłoń ok. 20 cm przed robotem musi włączyć hamowanie.

Kamera daje prawdziwy kierunek do osoby. LiDAR widzi ją pod kątem
a = s·surowy + poprawka (s = -1, gdy czujnik liczy zgodnie z zegarem), więc z par
(kamera, LiDAR) wyliczamy s (korelacja zmian kierunku przy przejściu w bok)
i poprawkę (średnia kołowa). Wynik: DATA_DIR/calibration.json, wczytywany przy starcie.
W trakcie kalibracji koła są zablokowane (control.py, lock="calibration").
"""
from __future__ import annotations

import hmac
import json
import logging
import math
import os
import threading
import time

from .config import Settings
from .hub import Hub, LidarGeom

log = logging.getLogger("calib")

STEPS = [
    ("clear", "Odsuń się od robota",
     "Wszyscy co najmniej 2 metry od robota. Radar nauczy się pustego stoiska."),
    ("front", "Stań przed kamerą",
     "Stań ok. 1 metra przed robotem, na wprost kamery, i stój spokojnie."),
    ("side", "Przejdź powoli w bok",
     "Cały czas przed kamerą: dwa kroki w lewo, potem dwa w prawo."),
    ("relearn", "Odsuń się jeszcze raz",
     "Odejdź na 2 metry. Radar nauczy się stoiska z nowym ustawieniem."),
    ("brake", "Sprawdź hamowanie",
     "Włóż dłoń ok. 20 cm przed robotem i przytrzymaj chwilę."),
]
SPANS = {"clear": (0, 20), "front": (20, 45), "side": (45, 70), "relearn": (70, 90), "brake": (90, 100)}
COUNTDOWN_S = 5.0
FRONT_SAMPLES = 20
SIDE_SPAN_DEG = 15.0
SIDE_MIN_SAMPLES = 25
MIN_CORR = 0.6
DONE_SHOW_S = 25.0
FILE = "calibration.json"
NO_PROGRESS_S = 150.0        # brak postępu tyle sekund = kalibracja sama się przerywa


def wrap(a: float) -> float:
    return (a + 180.0) % 360.0 - 180.0


def circ_mean(vals: list[float]) -> float:
    s = sum(math.sin(math.radians(v)) for v in vals)
    c = sum(math.cos(math.radians(v)) for v in vals)
    return math.degrees(math.atan2(s, c))


def solve(samples: list[tuple[float, float]], offset: float, clockwise: bool) -> dict:
    """samples: [(kierunek z kamery, kierunek z LiDAR-u przy obecnym ustawieniu)] w stopniach.
    Zwraca nowe ustawienie: offset, clockwise, czy odwrócono kierunek, rozrzut i korelację."""
    s = -1 if clockwise else 1
    cams = [bc for bc, _ in samples]
    lids = [bl for _, bl in samples]
    raw = [s * (bl - offset) for bl in lids]
    mc, ml = circ_mean(cams), circ_mean(lids)
    dc = [wrap(c - mc) for c in cams]
    dl = [wrap(b - ml) for b in lids]
    den = math.sqrt(sum(a * a for a in dc) * sum(b * b for b in dl)) or 1e-9
    corr = sum(a * b for a, b in zip(dc, dl)) / den
    s_new = s if corr >= 0 else -s
    offs = [wrap(bc - s_new * r) for bc, r in zip(cams, raw)]
    o_new = circ_mean(offs)
    resid = math.sqrt(sum(wrap(o - o_new) ** 2 for o in offs) / len(offs))
    return {"offset_deg": round(wrap(o_new), 1), "clockwise": s_new == -1, "flipped": s_new != s,
            "residual_deg": round(resid, 1), "corr": round(corr, 2), "samples": len(samples)}


def load_geom(hub: Hub, s: Settings) -> dict | None:
    """Wczytuje zapisaną kalibrację (jeśli jest) do hub.lidar_geom."""
    path = os.path.join(s.data_dir, FILE)
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        hub.lidar_geom = LidarGeom(float(d["offset_deg"]), bool(d["clockwise"]))
        hub.event("info", f"Kalibracja radaru wczytana: kąt {d['offset_deg']:+.0f}°, "
                          f"{'zgodnie' if d['clockwise'] else 'przeciwnie'} do zegara")
        return d
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError) as e:
        log.warning("Nie można wczytać %s: %s", path, e)
        return None


class Calibrator(threading.Thread):
    def __init__(self, hub: Hub, settings: Settings) -> None:
        super().__init__(daemon=True, name="calibration")
        self.hub, self.s = hub, settings
        self._halt = threading.Event()
        self._lock = threading.RLock()
        self.active = False
        self.done_until = 0.0
        self.state: dict = {"active": False, "done": False}
        self._reset()

    # ------------------------------------------------------------------ sterowanie
    def check_password(self, password: str | None) -> bool:
        a = (password or "").strip().upper().encode()
        b = self.s.calib_password.strip().upper().encode()
        return bool(b) and hmac.compare_digest(a, b)

    def _reset(self) -> None:
        self.i = 0
        self.t_step = time.monotonic()
        self.countdown_end = 0.0
        self.relearn_sent = False
        self.seen_learning = False
        self.front: list[tuple[float, float]] = []
        self.side: list[tuple[float, float]] = []
        self.last_lid: float | None = None
        self.result: dict | None = None
        self.message = ""
        self.prev_geom = LidarGeom(self.hub.lidar_geom.offset_deg, self.hub.lidar_geom.clockwise)
        self.applied = False
        self._last_progress = -1
        self._progress_t = time.monotonic()

    def begin(self) -> None:
        with self._lock:
            self._reset()
            self.active = True
            self.done_until = 0.0
            self.hub.calib_active = True
            self._enter(0)
            self._publish(0.0, [{"label": "Wszyscy co najmniej 2 m od robota", "ok": None}], countdown=COUNTDOWN_S)
            self.hub.event("info", "Kalibracja radaru: start")

    def cancel(self) -> None:
        with self._lock:
            if self.applied:                    # przywróć poprzednie ustawienie i naucz tło od nowa
                self.hub.lidar_geom = self.prev_geom
                self.hub.recalibrate_request.set()
            self.active = False
            self.done_until = 0.0
            self.hub.calib_active = False
            self.state = {"active": False, "done": False}
            self.hub.event("info", "Kalibracja radaru przerwana")

    def skip(self) -> None:
        with self._lock:
            if self.active and STEPS[self.i][0] == "brake":
                self._finish(brake_ok=False)

    def close(self) -> None:
        with self._lock:
            self.done_until = 0.0
            if not self.active:
                self.state = {"active": False, "done": False}

    def status(self) -> dict:
        if not self.active and self.done_until and time.monotonic() > self.done_until:
            self.state = {"active": False, "done": False}
            self.done_until = 0.0
        return self.state

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        while not self._halt.is_set():
            with self._lock:
                now = time.monotonic()
                try:
                    if self.active:
                        self._tick(now)
                        p = self.state.get("progress", 0)
                        if p != self._last_progress:
                            self._last_progress, self._progress_t = p, now
                        elif now - self._progress_t > NO_PROGRESS_S:
                            self.hub.event("warn", "Kalibracja przerwana: brak postępu przez 2,5 minuty")
                            self.cancel()
                except Exception:
                    log.exception("Błąd kroku kalibracji")
            self._halt.wait(0.1)

    # ------------------------------------------------------------------ kroki
    def _enter(self, i: int) -> None:
        self.i = i
        self.t_step = time.monotonic()
        self.message = ""
        key = STEPS[i][0]
        if key in ("clear", "relearn"):
            self.countdown_end = self.t_step + COUNTDOWN_S
            self.relearn_sent = False
            self.seen_learning = False

    def _publish(self, frac: float, checks: list[dict], countdown: float | None = None) -> None:
        key, title, hint = STEPS[self.i]
        a, b = SPANS[key]
        self.state = {
            "active": True, "done": False, "step": key, "index": self.i + 1, "total": len(STEPS),
            "title": title, "hint": hint, "progress": int(round(a + (b - a) * max(0.0, min(1.0, frac)))),
            "checks": checks, "countdown": None if countdown is None else math.ceil(max(0.0, countdown)),
            "message": self.message, "can_skip": key == "brake",
        }

    def _pair(self, now: float) -> tuple[float, float, float, float] | None | str:
        """Jedna osoba z kamery + jej tor z LiDAR-u (dobrany po odległości). Zwraca kierunki i odległości."""
        cam = [c for c in self.hub.camera_people_xy(now) if 0.4 <= (c.get("dist_m") or 0) <= 2.4]
        if not cam:
            return "no_cam"
        if len(cam) > 1:
            return "many_cam"
        c = cam[0]
        cx, cy = c["map"]
        d_cam = math.hypot(cx, cy)
        b_cam = math.degrees(math.atan2(cy, cx))
        tracks = [t for t in (self.hub.scan or {}).get("tracks", [])
                  if 0.3 <= t.get("d", 0) <= 2.8 and t.get("w", 0) >= 0.08]
        cands = [t for t in tracks if abs(t["d"] - d_cam) < 0.35]
        if not cands:
            return "no_lidar"
        if self.last_lid is not None:           # ciągłość: ta sama osoba co w poprzedniej próbce
            t = min(cands, key=lambda t: abs(wrap(math.degrees(math.atan2(t["y"], t["x"])) - self.last_lid))
                    + 30 * abs(t["d"] - d_cam))
        else:
            t = min(cands, key=lambda t: abs(t["d"] - d_cam))
        b_lid = math.degrees(math.atan2(t["y"], t["x"]))
        return b_cam, b_lid, d_cam, t["d"]

    @staticmethod
    def _fmt_m(v: float) -> str:
        return f"{v:.1f}".replace(".", ",") + " m"

    def _tick(self, now: float) -> None:
        hub = self.hub
        key = STEPS[self.i][0]
        lidar_live = bool(hub.drive.get("lidar_live"))
        cam_live = bool(hub.drive.get("cam_live"))
        st = hub.shield_state

        if key in ("clear", "relearn"):
            if not lidar_live:
                self.message = "Radar nie przesyła danych – sprawdź kabel LiDAR-u."
            left = self.countdown_end - now
            if left > 0:
                self._publish(0.35 * (1 - left / COUNTDOWN_S),
                              [{"label": "Wszyscy co najmniej 2 m od robota", "ok": None}], countdown=left)
                return
            if not self.relearn_sent:
                hub.recalibrate_request.set()
                self.relearn_sent = True
                self.t_learn = now
                self._publish(0.35, [{"label": "Radar uczy się stoiska", "ok": None}])
                return
            if not st.ready:
                self.seen_learning = True
            learn = st.learn_progress if self.seen_learning else 0.0
            if self.seen_learning and st.ready:
                self._enter(self.i + 1)
                return
            if not self.seen_learning and now - self.t_learn > 3.0 and st.ready:
                self._enter(self.i + 1)         # bardzo szybka nauka – stan "uczy się" mógł umknąć
                return
            self._publish(0.35 + 0.65 * learn, [{"label": "Radar uczy się stoiska", "ok": None}])
            return

        if key in ("front", "side"):
            if not cam_live:
                self.message = "Kamera nie przesyła obrazu."
            p = self._pair(now)
            checks = [
                {"label": "Kamera widzi osobę" if not isinstance(p, tuple) else f"Kamera widzi osobę ({self._fmt_m(p[2])})",
                 "ok": isinstance(p, tuple) or p == "no_lidar"},
                {"label": "Radar widzi tę samą osobę" if not isinstance(p, tuple) else f"Radar widzi tę samą osobę ({self._fmt_m(p[3])})",
                 "ok": isinstance(p, tuple)},
            ]
            if p == "no_cam":
                self.message = "Kamera nikogo nie widzi – stań na wprost, ok. 1 m od kamery."
            elif p == "many_cam":
                self.message = "Kamera widzi kilka osób – przed robotem może stać tylko jedna."
            elif p == "no_lidar":
                self.message = "Radar nie widzi osoby w tej odległości – podejdź na ok. 1 m."
            else:
                self.message = ""

            if key == "front":
                if isinstance(p, tuple) and abs(p[0]) < 35:
                    b_cam, b_lid = p[0], p[1]
                    diffs = [wrap(bc - bl) for bc, bl in self.front]
                    if len(diffs) >= 5 and abs(wrap((b_cam - b_lid) - circ_mean(diffs))) > 15:
                        pass                    # wyraźnie inna para – pomijamy (np. sąsiedni obiekt)
                    else:
                        self.front.append((b_cam, b_lid))
                        self.last_lid = b_lid
                n = len(self.front)
                checks.append({"label": f"Pomiary {min(n, FRONT_SAMPLES)}/{FRONT_SAMPLES}", "ok": n >= FRONT_SAMPLES})
                if n >= FRONT_SAMPLES:
                    self._enter(self.i + 1)
                    return
                self._publish(n / FRONT_SAMPLES, checks)
                return

            # side: przejście w bok przed kamerą
            if isinstance(p, tuple) and abs(p[0]) < 40:
                self.side.append((p[0], p[1]))
                self.last_lid = p[1]
            cams = [bc for bc, _ in self.side]
            span = (max(cams) - min(cams)) if cams else 0.0
            frac = min(span / SIDE_SPAN_DEG, 1.0) * 0.8 + min(len(self.side) / SIDE_MIN_SAMPLES, 1.0) * 0.2
            checks.append({"label": f"Przejście w bok: {min(span, SIDE_SPAN_DEG):.0f}° z {SIDE_SPAN_DEG:.0f}°",
                           "ok": span >= SIDE_SPAN_DEG})
            if span >= SIDE_SPAN_DEG and len(self.side) >= SIDE_MIN_SAMPLES:
                g = hub.lidar_geom
                res = solve(self.front + self.side, g.offset_deg, g.clockwise)
                if abs(res["corr"]) < MIN_CORR:
                    self.side.clear()
                    self.message = "Nie udało się ustalić kierunku – przejdź jeszcze raz, wolniej."
                    self._publish(0.0, checks)
                    return
                self.result = res
                hub.lidar_geom = LidarGeom(res["offset_deg"], res["clockwise"])
                self.applied = True
                hub.event("info", f"Kalibracja: kąt {res['offset_deg']:+.0f}°, "
                                  f"{'odwrócony kierunek, ' if res['flipped'] else ''}rozrzut {res['residual_deg']:.0f}°")
                self._enter(self.i + 1)
                return
            self._publish(frac, checks)
            return

        if key == "brake":
            front = st.active and st.nearest_deg is not None and abs(wrap(st.nearest_deg)) <= 60
            if st.active:
                self._finish(brake_ok=True, brake_front=front)
                return
            self._publish(0.0, [{"label": "Hamowanie po włożeniu dłoni", "ok": None}])
            if now - self.t_step > 45:
                self.message = "Radar nie wykrył dłoni. Możesz pominąć ten krok."
            return

    def _finish(self, brake_ok: bool, brake_front: bool = False) -> None:
        g = self.hub.lidar_geom
        res = dict(self.result or {})
        res.update({"offset_deg": round(g.offset_deg, 1), "clockwise": g.clockwise,
                    "brake_ok": brake_ok, "brake_front": brake_front, "t": time.time()})
        saved = True
        try:
            os.makedirs(self.s.data_dir, exist_ok=True)
            with open(os.path.join(self.s.data_dir, FILE), "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
        except OSError as e:
            saved = False
            log.warning("Nie zapisano kalibracji: %s", e)
        self.active = False
        self.hub.calib_active = False
        self.done_until = time.monotonic() + DONE_SHOW_S
        self.state = {"active": False, "done": True, "progress": 100, "saved": saved, "result": res,
                      "title": "Kalibracja zakończona", "index": len(STEPS), "total": len(STEPS)}
        self.hub.event("info", f"Kalibracja radaru zakończona ({'zapisana' if saved else 'NIE zapisana'})")
