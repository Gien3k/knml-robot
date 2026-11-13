"""Przetwarzanie skanu LiDAR D500: tło, ICP (wirtualna klatka), śledzenie osób, strefy bezpieczeństwa.

Wejście: 360 zasięgów [m] w układzie robota (indeks = kąt CCW od przodu, 1°).

* Tło stoiska – percentyl 90 z okna ~60 s (próbkowanie 2 Hz). Stałe elementy
  (monitor, ścianki) przestają być "intruzami", ludzie i dłonie – nie.
* Tryb "mat" – pozycja robota względem punktu startu z dopasowania ICP skanu do
  mapy referencyjnej (dynamiczne obiekty odrzucane jako outliery). Na tej
  podstawie działa wirtualna klatka (geofence).
* Bezpieczeństwo – odległość punktów od PROSTOKĄTA obrysu robota, podział na
  strefy przód/tył/lewo/prawo, histereza zwolnienia blokady.
  - stand: liczą się obiekty dynamiczne (pierwszy plan) + wszystko < ESTOP,
  - mat:   liczy się każdy punkt (ściana też jest przeszkodą).
"""
from __future__ import annotations

import math
import threading
import time
import warnings

import numpy as np

from ..config import settings

N_BINS = 360
ANG = np.deg2rad(np.arange(N_BINS, dtype=np.float64))
COS, SIN = np.cos(ANG), np.sin(ANG)


def _rot(th: float) -> np.ndarray:
    c, s = math.cos(th), math.sin(th)
    return np.array([[c, -s], [s, c]])


def icp_2d(src: np.ndarray, dst: np.ndarray, init: np.ndarray, iters: int = 18) -> tuple[np.ndarray, float]:
    """Point-to-point ICP. src w układzie robota, dst w układzie startu. Zwraca (x,y,th), jakość 0..1."""
    x, y, th = init
    if len(src) < 20 or len(dst) < 20:
        return init.copy(), 0.0
    gate = 0.35
    inl = np.zeros(len(src), bool)
    for i in range(iters):
        R = _rot(th)
        p = src @ R.T + np.array([x, y])
        d2 = ((p[:, None, :] - dst[None, :, :]) ** 2).sum(-1)
        j = d2.argmin(1)
        dmin = np.sqrt(d2[np.arange(len(p)), j])
        inl = dmin < gate
        if inl.sum() < 15:
            return init.copy(), 0.0
        A, B = p[inl], dst[j[inl]]
        ca, cb = A.mean(0), B.mean(0)
        H = (A - ca).T @ (B - cb)
        dth = math.atan2(H[0, 1] - H[1, 0], H[0, 0] + H[1, 1])
        Rd = _rot(dth)
        t = cb - Rd @ ca
        nxy = Rd @ np.array([x, y]) + t
        x, y, th = nxy[0], nxy[1], th + dth
        gate = max(0.06, gate * 0.75)
        if abs(dth) < 1e-4 and np.hypot(*t) < 1e-4:
            break
    quality = float((dmin < 0.06).mean())
    return np.array([x, y, (th + math.pi) % (2 * math.pi) - math.pi]), quality


class Tracker:
    """Prosty tracker wielu obiektów (najbliższy sąsiad + EMA prędkości)."""

    def __init__(self) -> None:
        self.tracks: list[dict] = []
        self._next = 1

    def update(self, dets: list[tuple[float, float, float]], now: float) -> list[dict]:
        used = set()
        for tr in self.tracks:
            best, bd = None, 0.6
            for k, (x, y, _) in enumerate(dets):
                if k in used:
                    continue
                d = math.hypot(x - tr["x"], y - tr["y"])
                if d < bd:
                    best, bd = k, d
            if best is not None:
                used.add(best)
                x, y, wdt = dets[best]
                dt = max(1e-3, now - tr["t"])
                vx, vy = (x - tr["x"]) / dt, (y - tr["y"]) / dt
                tr["vx"] += 0.3 * (vx - tr["vx"])
                tr["vy"] += 0.3 * (vy - tr["vy"])
                tr["x"] += 0.6 * (x - tr["x"])
                tr["y"] += 0.6 * (y - tr["y"])
                tr["w"] = wdt
                tr["t"] = now
                tr["hits"] += 1
        for k, (x, y, wdt) in enumerate(dets):
            if k not in used:
                self.tracks.append({"id": self._next, "x": x, "y": y, "vx": 0.0, "vy": 0.0,
                                    "w": wdt, "t": now, "born": now, "hits": 1})
                self._next = self._next % 999 + 1
        self.tracks = [t for t in self.tracks if now - t["t"] < 1.0]
        return [t for t in self.tracks if t["hits"] >= 4 and now - t["born"] >= 0.4]


class LidarProcessor:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.motion_mode = "stand"
        self.bg_buf: list[np.ndarray] = []
        self.bg = np.zeros(N_BINS)            # tło (układ startu, polarnie)
        self.bg_ready = False
        self._last_bg_sample = 0.0
        self.ref_pts: np.ndarray | None = None  # mapa referencyjna dla ICP
        self.pose = np.zeros(3)
        self.pose_quality = 1.0
        self.tracker = Tracker()
        self._blocked_until: dict[str, float] = {}
        self._estop_until = 0.0
        self._calib_scans: list[np.ndarray] = []
        self.calibrating = True

    def recalibrate(self) -> None:
        with self.lock:
            self.bg_buf.clear()
            self.bg_ready = False
            self.ref_pts = None
            self.pose = np.zeros(3)
            self._calib_scans = []
            self.calibrating = True
            self.tracker = Tracker()

    def set_motion_mode(self, mode: str) -> None:
        if mode != self.motion_mode:
            self.motion_mode = mode
            self.recalibrate()

    # ------------------------------------------------------------------
    def process(self, ranges: np.ndarray, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        with self.lock:
            return self._process(ranges, now)

    def _process(self, r: np.ndarray, now: float) -> dict:
        s = settings
        valid = (r > s.lidar_min_range_m) & (r < s.lidar_max_range_m)
        x, y = r * COS, r * SIN
        pts = np.stack([x, y], 1)

        # --- kalibracja (pierwsze ~2 s): mapa referencyjna ---
        if self.calibrating:
            self._calib_scans.append(np.where(valid, r, np.nan))
            if len(self._calib_scans) >= 15:
                stack = np.array(self._calib_scans)
                with np.errstate(all="ignore"), warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    ref = np.nanpercentile(stack, 90, axis=0)
                ref = np.nan_to_num(ref, nan=0.0)
                self.bg = ref.copy()
                self.bg_ready = True
                ok = ref > 0
                self.ref_pts = np.stack([ref * COS, ref * SIN], 1)[ok]
                self.calibrating = False

        # --- pozycja (tylko tryb mat) ---
        mat = self.motion_mode == "mat"
        if mat and self.ref_pts is not None:
            sub = pts[valid][::2]
            self.pose, q = icp_2d(sub, self.ref_pts[::2], self.pose)
            self.pose_quality = 0.7 * self.pose_quality + 0.3 * q
        else:
            self.pose = np.zeros(3)
            self.pose_quality = 1.0

        # --- punkty w układzie startu ---
        R = _rot(self.pose[2])
        pw = pts @ R.T + self.pose[:2]
        rw = np.hypot(pw[:, 0], pw[:, 1])
        bw = (np.round(np.degrees(np.arctan2(pw[:, 1], pw[:, 0]))) % N_BINS).astype(int)

        # --- adaptacja tła (stand: okno 60 s; mat: stała mapa z kalibracji) ---
        if not mat and not self.calibrating and now - self._last_bg_sample > 0.5:
            self._last_bg_sample = now
            self.bg_buf.append(np.where(valid, r, np.nan))
            if len(self.bg_buf) > 120:
                self.bg_buf.pop(0)
            if len(self.bg_buf) >= 10 and len(self.bg_buf) % 4 == 0:
                with np.errstate(all="ignore"), warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    bg = np.nanpercentile(np.array(self.bg_buf), 90, axis=0)
                self.bg = np.nan_to_num(bg, nan=0.0)

        # --- pierwszy plan ---
        if self.bg_ready:
            bgv = self.bg[bw]
            fg = valid & (((bgv > 0) & (rw < bgv - 0.15)) | ((bgv <= 0) & (rw < 3.5)))
            if mat and self.ref_pts is not None and len(self.ref_pts) and fg.any():
                # po przesunięciu robota kubełki kątowe się rozjeżdżają – dokładniej:
                # punkt jest "nowy", jeśli jest daleko od każdego punktu mapy referencyjnej
                cand = np.flatnonzero(fg)
                d2 = ((pw[cand][:, None, :] - self.ref_pts[None, :, :]) ** 2).sum(-1).min(1)
                fg[cand[d2 < 0.12 ** 2]] = False
        else:
            fg = np.zeros(N_BINS, bool)

        # --- klastry -> tracker (w układzie startu) ---
        dets = self._clusters(pw, fg)
        tracks = self.tracker.update(dets, now)
        Rinv = R.T
        out_tracks = []
        for t in tracks:
            rel = Rinv @ (np.array([t["x"], t["y"]]) - self.pose[:2])
            vrel = Rinv @ np.array([t["vx"], t["vy"]])
            out_tracks.append({
                "id": t["id"], "x": round(float(rel[0]), 3), "y": round(float(rel[1]), 3),
                "vx": round(float(vrel[0]), 2), "vy": round(float(vrel[1]), 2),
                "w": round(float(t["w"]), 2), "age": round(now - t["born"], 1),
                "d": round(float(np.hypot(*rel)), 2),
            })

        safety = self._safety(pts, valid, fg, now, mat)
        return {
            "ranges": r, "fg": fg, "tracks": out_tracks, "safety": safety,
            "pose": {"x": round(float(self.pose[0]), 3), "y": round(float(self.pose[1]), 3),
                     "th": round(float(self.pose[2]), 3), "quality": round(self.pose_quality, 2),
                     "mode": self.motion_mode, "calibrating": self.calibrating},
        }

    @staticmethod
    def _clusters(pw: np.ndarray, fg: np.ndarray) -> list[tuple[float, float, float]]:
        idx = np.flatnonzero(fg)
        if len(idx) == 0:
            return []
        groups, cur = [], [idx[0]]
        for a, b in zip(idx[:-1], idx[1:]):
            if b - a <= 2 and np.hypot(*(pw[b] - pw[a])) < 0.25:
                cur.append(b)
            else:
                groups.append(cur)
                cur = [b]
        groups.append(cur)
        # zawinięcie 359° -> 0°
        if len(groups) > 1 and groups[0][0] <= 1 and groups[-1][-1] >= N_BINS - 2 \
                and np.hypot(*(pw[groups[0][0]] - pw[groups[-1][-1]])) < 0.25:
            groups[0] = groups[-1] + groups[0]
            groups.pop()
        out = []
        for g in groups:
            if len(g) < 2:
                continue
            p = pw[g]
            width = float(np.hypot(*(p[0] - p[-1])))
            if not 0.06 <= width <= 0.9:      # sylwetka/ręka, nie fragment ściany
                continue
            c = p.mean(0)
            out.append((float(c[0]), float(c[1]), width))
        # ręka wyciągnięta przed tułowiem zasłania jego środek – echo ciała rozpada się na
        # dwa wąskie kawałki w tej samej odległości; scalamy je z powrotem w jedną sylwetkę
        merged = True
        while merged:
            merged = False
            for i in range(len(out)):
                for j in range(i + 1, len(out)):
                    (x1, y1, w1), (x2, y2, w2) = out[i], out[j]
                    d = math.hypot(x1 - x2, y1 - y2)
                    if abs(math.hypot(x1, y1) - math.hypot(x2, y2)) < 0.15 and d < 0.45:
                        ws = w1 + w2 + 1e-6
                        out[i] = ((x1 * w1 + x2 * w2) / ws, (y1 * w1 + y2 * w2) / ws, min(0.9, d + (w1 + w2) / 2))
                        out.pop(j)
                        merged = True
                        break
                if merged:
                    break
        return out

    def _safety(self, pts, valid, fg, now, mat) -> dict:
        s = settings
        hl, hw = s.robot_half_length_m, s.robot_half_width_m
        x, y = pts[:, 0], pts[:, 1]
        dx = np.maximum(np.abs(x) - hl, 0.0)
        dy = np.maximum(np.abs(y) - hw, 0.0)
        dist = np.hypot(dx, dy)
        inside = (dx == 0) & (dy == 0)            # odbicia od własnej obudowy
        considered = valid & ~inside
        considered &= (fg | (dist < s.estop_distance_m)) if not mat else considered
        if not self.bg_ready and not mat:
            considered &= dist < s.estop_distance_m

        zones = {
            "front": considered & (x > hl),
            "rear": considered & (x < -hl),
            "left": considered & (np.abs(x) <= hl) & (y > 0),
            "right": considered & (np.abs(x) <= hl) & (y < 0),
        }
        mins = {}
        for k, m in zones.items():
            mins[k] = float(dist[m].min()) if m.any() else None

        hold = s.clear_hold_s
        blocked = {}
        for k, d in mins.items():
            if d is not None and d < s.safety_distance_m:
                self._blocked_until[k] = now + hold
            blocked[k] = now < self._blocked_until.get(k, 0.0)

        all_d = dist[considered]
        min_d = float(all_d.min()) if all_d.size else None
        min_ang = None
        if all_d.size:
            k = np.flatnonzero(considered)[int(np.argmin(all_d))]
            min_ang = int(k)
        if min_d is not None and min_d < s.estop_distance_m:
            self._estop_until = now + hold
        estop = now < self._estop_until

        state = "ESTOP" if estop else ("WARN" if any(blocked.values()) else "OK")
        return {
            "state": state, "min_dist": None if min_d is None else round(min_d, 3),
            "min_angle": min_ang, "zones": {k: (None if v is None else round(v, 3)) for k, v in mins.items()},
            "blocked": blocked, "safety_m": s.safety_distance_m, "estop_m": s.estop_distance_m,
            "intrusions": int(np.count_nonzero(considered & (dist < s.safety_distance_m))),
        }
