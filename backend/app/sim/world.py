"""Wspólny świat symulacji – działa TYLKO, gdy wszystkie trzy urządzenia są symulowane.

Układ współrzędnych = układ robota (środek LiDAR-u): X do przodu (w stronę
odwiedzających), Y w lewo, Z w górę. Robot stoi na podstawce i się nie przemieszcza.

LiDAR i kamera patrzą na TEN SAM świat: przechodniów, "kierowcę" wykonującego
scenariusz gestów i drugą rękę, którą kierowca wkłada w tarczę LiDAR-u.
Kamera jest renderowana fizycznie (z-bufor brył, cieniowanie w podczerwieni,
wzór projektora kropek, szum), a dłoń na obrazie powstaje z tych samych 21 punktów,
które trafiają do interfejsu jako wynik "sieci".
"""
from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

from . import hand as handmodel

HAND_Z = 0.92         # wysokość środka dłoni [m] – tuż pod kamerą, cała dłoń mieści się w kadrze
PALM_IDX = (0, 5, 9, 13, 17)


@dataclass
class Phase:
    dur: float
    name: str
    pos: tuple[float, float]       # gdzie stoi kierowca
    reach: float                   # jak daleko przed sobą trzyma dłoń
    lateral: float                 # przesunięcie dłoni w jego prawo [m]
    hand: str | None               # open | fist | None
    poke: bool = False             # druga ręka w tarczy LiDAR-u


SCRIPT = (
    Phase(5.0, "approach", (1.30, 0.00), 0.15, 0.0, None),
    Phase(1.5, "idle", (1.30, 0.00), 0.15, 0.0, None),
    Phase(3.5, "drive_far", (1.30, 0.00), 0.30, 0.0, "open"),
    Phase(3.5, "drive_near", (1.05, 0.00), 0.48, 0.0, "open"),
    Phase(3.0, "steer_a", (1.10, 0.00), 0.42, 0.20, "open"),
    Phase(3.0, "steer_b", (1.10, 0.00), 0.42, -0.20, "open"),
    Phase(3.0, "fist", (1.15, 0.00), 0.40, 0.0, "fist"),
    Phase(2.0, "drive_mid", (1.15, 0.00), 0.38, 0.0, "open"),
    Phase(4.0, "poke", (1.15, 0.00), 0.38, 0.0, "open", poke=True),
    Phase(2.0, "drive_mid2", (1.15, 0.00), 0.38, 0.0, "open"),
    Phase(4.0, "leave", (3.40, -2.20), 0.15, 0.0, None),
    Phase(14.0, "away", (4.60, -3.60), 0.15, 0.0, None),
)
SCRIPT_LEN = sum(p.dur for p in SCRIPT)
POKE_TARGET = np.array([0.03, -0.23])     # prawy bok robota, ~23 cm od LiDAR-u


@dataclass
class Person:
    pid: int
    x: float
    y: float
    height: float
    albedo: float
    speed: float
    tx: float = 0.0
    ty: float = 0.0
    wait_until: float = 0.0
    radius: float = 0.18
    heading: float = math.pi


def _u(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


class SimWorld:
    def __init__(self, cam_x: float = 0.10, cam_y: float = 0.0, cam_h: float = 0.95,
                 hfov_deg: float = 69.0, seed: int = 7) -> None:
        self.lock = threading.Lock()
        self.rng = random.Random(seed)
        self.t0: float | None = None
        self._last: float | None = None
        self.cam = np.array([cam_x, cam_y, cam_h])
        self.hfov = hfov_deg

        self.segments = np.array([
            (-1.0, -2.2, -1.0, 2.2),             # tylna ściana stoiska
            (-1.0, 2.2, 0.7, 2.2),               # ścianka lewa
            (-1.0, -2.2, 0.7, -2.2),             # ścianka prawa
            (5.0, -4.5, 5.0, 4.5),               # stoisko naprzeciwko
            (-0.12, 0.42, -0.12, 1.08),          # monitor na stole
            (-0.20, 0.42, -0.20, 1.08),
            (-0.70, -1.75, -0.70, -1.05),        # roll-up
        ], dtype=np.float64)
        self.pillars = np.array([[3.3, -2.9, 0.25]], dtype=np.float64)

        self.walkers: list[Person] = []
        for i in range(3):
            p = Person(pid=i + 1, x=self.rng.uniform(1.8, 4.0), y=self.rng.uniform(-2.5, 2.5),
                       height=self.rng.uniform(1.6, 1.88), albedo=self.rng.uniform(0.32, 0.7),
                       speed=self.rng.uniform(0.45, 1.0))
            self._new_target(p, 0.0)
            self.walkers.append(p)
        self.driver = Person(pid=9, x=4.6, y=-3.6, height=1.76, albedo=0.5, speed=1.15)

        self.phase = SCRIPT[0]
        self.reach = 0.15
        self.lateral = 0.0
        self.hand_vis = 0.0         # 0..1 – płynne wyciąganie ręki
        self.poke = 0.0             # 0..1
        self.hand_open = True
        self.hand_world = np.zeros(3)
        self.poke_world = np.zeros(3)
        self._cache: dict = {}

    # ------------------------------------------------------------------ czas
    def _new_target(self, p: Person, now: float) -> None:
        p.tx = self.rng.uniform(1.6, 4.3)
        p.ty = self.rng.uniform(-3.0, 3.0)
        p.wait_until = now + self.rng.uniform(0.0, 3.5)

    def tick(self, now: float) -> None:
        with self.lock:
            if self.t0 is None:
                self.t0 = self._last = now
            dt = min(0.1, now - self._last)
            if dt <= 0:
                return
            self._last = now
            t = now - self.t0
            for p in self.walkers:
                self._step_walker(p, dt, t)
            self._step_driver(dt, t)

    def _step_walker(self, p: Person, dt: float, t: float) -> None:
        if t < p.wait_until:
            return
        dx, dy = p.tx - p.x, p.ty - p.y
        d = math.hypot(dx, dy)
        if d < 0.1:
            self._new_target(p, t)
            return
        step = min(d, p.speed * dt)
        p.x += dx / d * step
        p.y += dy / d * step
        target_h = math.atan2(dy, dx)
        p.heading += ((target_h - p.heading + math.pi) % (2 * math.pi) - math.pi) * min(1.0, dt * 4)
        p.x = max(p.x, 1.5)
        ddx, ddy = p.x - self.driver.x, p.y - self.driver.y
        dd = math.hypot(ddx, ddy)
        if 0 < dd < 0.6:
            p.x += ddx / dd * (0.6 - dd)
            p.y += ddy / dd * (0.6 - dd)

    def _step_driver(self, dt: float, t: float) -> None:
        tc = t % SCRIPT_LEN
        acc = 0.0
        ph = SCRIPT[-1]
        for p in SCRIPT:
            if tc < acc + p.dur:
                ph = p
                break
            acc += p.dur
        self.phase = ph
        drv = self.driver
        dx, dy = ph.pos[0] - drv.x, ph.pos[1] - drv.y
        d = math.hypot(dx, dy)
        if d > 1e-3:
            step = min(d, drv.speed * dt)
            drv.x += dx / d * step
            drv.y += dy / d * step
        if d > 0.3:
            drv.heading = math.atan2(dy, dx)
        else:
            drv.heading = math.atan2(-drv.y, -drv.x)          # twarzą do robota

        k = min(1.0, dt * 4.0)
        self.reach += (ph.reach - self.reach) * k
        self.lateral += (ph.lateral - self.lateral) * k
        self.hand_vis += ((1.0 if ph.hand else 0.0) - self.hand_vis) * min(1.0, dt * 5.0)
        self.poke += ((1.0 if ph.poke else 0.0) - self.poke) * min(1.0, dt * 3.5)
        if ph.hand:
            self.hand_open = ph.hand == "open"

        F = _u(np.array([-drv.x, -drv.y]))                     # od kierowcy do robota
        R = np.array([F[1], -F[0]])                            # jego prawa strona
        reach = 0.12 + (self.reach - 0.12) * self.hand_vis
        z = HAND_Z - 0.35 * (1.0 - self.hand_vis) + 0.012 * math.sin(t * 2.1)
        xy = np.array([drv.x, drv.y]) + F * reach + R * self.lateral * self.hand_vis
        self.hand_world = np.array([xy[0], xy[1], z])
        shoulder_l = np.array([drv.x, drv.y]) - R * 0.19 + F * 0.05
        pk = shoulder_l + (POKE_TARGET - shoulder_l) * self.poke
        self.poke_world = np.array([pk[0], pk[1], HAND_Z - 0.05])

    def snapshot(self) -> dict:
        with self.lock:
            drv = self.driver
            return {
                "walkers": [(p.x, p.y, p.height, p.albedo, p.radius, p.heading) for p in self.walkers],
                "driver": (drv.x, drv.y, drv.height, drv.albedo, drv.radius, drv.heading),
                "hand": self.hand_world.copy(), "hand_vis": self.hand_vis, "hand_open": self.hand_open,
                "poke": self.poke, "poke_world": self.poke_world.copy(), "phase": self.phase.name,
            }

    # ---------------------------------------------------------------- LiDAR
    def lidar_scan(self, n: int = 360) -> np.ndarray:
        """Zasięgi [m] dla n kątów w układzie robota (CCW od przodu). 0 = brak echa."""
        s = self.snapshot()
        ang = np.deg2rad(np.arange(n) * (360.0 / n))
        d = np.stack([np.cos(ang), np.sin(ang)], 1)
        circles = [(w[0], w[1], w[4]) for w in s["walkers"]]
        dv = s["driver"]
        circles.append((dv[0], dv[1], dv[4]))
        circles += [tuple(c) for c in self.pillars]
        if s["hand_vis"] > 0.5:
            circles.append((s["hand"][0], s["hand"][1], 0.045))
        if s["poke"] > 0.05:
            pk = s["poke_world"][:2]
            base = np.array([dv[0], dv[1]])
            for a in (0.0, 0.18, 0.36, 0.54):                  # dłoń + przedramię
                c = pk + (base - pk) * a
                circles.append((c[0], c[1], 0.045 if a == 0.0 else 0.04))
        c = np.array(circles)
        oc = -c[:, :2]
        b = d @ oc.T
        cc = (oc ** 2).sum(1) - c[:, 2] ** 2
        disc = b ** 2 - cc[None, :]
        with np.errstate(invalid="ignore"):
            t = -b - np.sqrt(disc)
        t[(disc < 0) | (t <= 0)] = np.inf
        best = t.min(1)
        seg = self.segments
        p, q = seg[:, :2], seg[:, 2:]
        e = q - p
        denom = d[:, 0:1] * e[None, :, 1] - d[:, 1:2] * e[None, :, 0]
        with np.errstate(divide="ignore", invalid="ignore"):
            tt = (p[None, :, 0] * e[None, :, 1] - p[None, :, 1] * e[None, :, 0]) / denom
            ss = (p[None, :, 0] * d[:, 1:2] - p[None, :, 1] * d[:, 0:1]) / denom
        ok = (np.abs(denom) > 1e-9) & (tt > 0) & (ss >= 0) & (ss <= 1)
        best = np.minimum(best, np.where(ok, tt, np.inf).min(1))
        r = best + np.random.normal(0, 0.006, n)
        r[~np.isfinite(best) | (best > 12.0)] = 0.0
        r[np.random.random(n) < 0.02] = 0.0
        return r

    # ---------------------------------------------------------------- kamera
    def _to_cam(self, pts: np.ndarray) -> np.ndarray:
        """Punkty świata (N,3) -> kamera (N,3): x w prawo, y w dół, z do przodu."""
        cx, cy, ch = self.cam
        return np.stack([-(pts[:, 1] - cy), -(pts[:, 2] - ch), pts[:, 0] - cx], 1)

    @staticmethod
    def _body(person: tuple, right_wrist: np.ndarray | None = None,
              left_wrist: np.ndarray | None = None) -> list[tuple]:
        """Postać z kapsuł: (p0, p1, promień, albedo)."""
        x, y, H, alb, _, heading = person
        k = H / 1.75
        fx_, fy_ = math.cos(heading), math.sin(heading)
        lx, ly = -fy_, fx_

        def P(fwd: float, left: float, z: float) -> np.ndarray:
            return np.array([x + fx_ * fwd + lx * left, y + fy_ * fwd + ly * left, z])

        pants, shoe = alb * 0.65, alb * 0.4
        caps: list[tuple] = []
        for sd in (1, -1):
            hip, knee = P(0, 0.09 * sd, 0.92 * k), P(0.02, 0.095 * sd, 0.50 * k)
            ankle, toe = P(0, 0.10 * sd, 0.08 * k), P(0.10, 0.10 * sd, 0.04)
            caps += [(hip, knee, 0.075, pants), (knee, ankle, 0.055, pants), (ankle, toe, 0.042, shoe)]
        caps += [(P(0, 0.07, 0.97 * k), P(0, 0.09, 1.37 * k), 0.115, alb),
                 (P(0, -0.07, 0.97 * k), P(0, -0.09, 1.37 * k), 0.115, alb),
                 (P(0, 0, 1.40 * k), P(0, 0, 1.50 * k), 0.05, handmodel.SKIN * 0.9),
                 (P(0.01, 0, 1.57 * k), P(0.0, 0, 1.65 * k), 0.093, handmodel.SKIN),
                 (P(-0.025, 0, 1.64 * k), P(-0.03, 0, 1.69 * k), 0.088, 0.22)]       # włosy
        for sd, wrist in ((1, left_wrist), (-1, right_wrist)):
            sh = P(0, 0.19 * sd, 1.39 * k)
            if wrist is None:
                el, wr = P(0.0, 0.22 * sd, 1.11 * k), P(0.04, 0.23 * sd, 0.86 * k)
                caps += [(sh, el, 0.046, alb), (el, wr, 0.038, alb),
                         (wr, P(0.05, 0.235 * sd, 0.79 * k), 0.036, handmodel.SKIN)]
            else:
                el = sh + (wrist - sh) * 0.5 + np.array([0.0, 0.0, -0.10])
                wr = wrist + _u(el - wrist) * 0.02
                caps += [(sh, el, 0.046, alb), (el, wr, 0.037, alb)]
        return caps

    def _grids(self, W: int, H: int, f: float) -> tuple:
        key = (W, H, round(f, 3))
        g = self._cache.get(key)
        if g is None:
            xc = ((np.arange(W, dtype=np.float32) - W / 2) / f)[None, :]
            yc = ((np.arange(H, dtype=np.float32) - H / 2) / f)[:, None]
            rng = np.random.default_rng(3)
            dots = (rng.random((H, W)) < 0.012).astype(np.float32)
            dots = cv2.GaussianBlur(dots, (3, 3), 0.5) * 2.2
            xs = (np.arange(W, dtype=np.float32) - W / 2) / (W / 2)
            ys = (np.arange(H, dtype=np.float32) - H / 2) / (H / 2)
            vig = 1.0 - 0.38 * np.clip(xs[None, :] ** 2 * 0.55 + ys[:, None] ** 2 * 0.75, 0, 1)
            g = (xc, yc, dots, vig.astype(np.float32))
            self._cache[key] = g
        return g

    def render(self, W: int = 640, H: int = 360) -> tuple[np.ndarray, dict | None, list[dict]]:
        """Zwraca (obraz IR uint8 HxW, dłoń jak z prawdziwej kamery albo None, osoby jak z detektora)."""
        s = self.snapshot()
        f = (W / 2) / math.tan(math.radians(self.hfov) / 2)
        cxw, cyw, hm = self.cam
        xc, yc, dots, vig = self._grids(W, H, f)

        # --- tło: podłoga hali z fugami płyt + ściana stoiska naprzeciwko ---
        with np.errstate(divide="ignore", invalid="ignore"):
            zf = np.where(yc > 1e-4, hm / yc, np.inf)
        zbuf = np.broadcast_to(zf, (H, W)).astype(np.float32).copy()
        with np.errstate(invalid="ignore"):
            Xw = cxw + zbuf
            Yw = cyw - xc * zbuf
            seam = (np.abs((Xw % 1.0) - 0.5) > 0.485) | (np.abs((Yw % 1.0) - 0.5) > 0.485)
        alb = np.where(np.isfinite(zbuf), np.where(seam, 0.13, 0.21), 0.0).astype(np.float32)
        tw = 5.0 - cxw
        hgt = hm - yc * tw
        yw = cyw - xc * tw
        panel = (np.abs((yw % 1.2) - 0.6) > 0.585) | (np.abs(hgt - 2.2) < 0.02)
        wall = np.broadcast_to((hgt > 0) & (hgt < 2.6), (H, W)) & (tw < zbuf)
        zbuf = np.where(wall, tw, zbuf).astype(np.float32)
        alb = np.where(wall, np.where(panel, 0.3, 0.42), alb).astype(np.float32)

        # --- bryły: przechodnie, kierowca, jego dłonie (ostatni element = numer osoby) ---
        DRIVER = 9
        caps: list[tuple] = []
        for i, w in enumerate(s["walkers"]):
            caps += [(*c, i + 1) for c in self._body(w)]
        lm3 = None
        if s["hand_vis"] > 0.02:
            toward = self.cam - s["hand"]
            lm3 = handmodel.landmarks(s["hand"], toward, s["hand_open"], tilt=0.15)
            caps += [(*c, DRIVER) for c in handmodel.capsules(lm3)]
        poke_wrist = s["poke_world"] if s["poke"] > 0.05 else None
        if poke_wrist is not None:
            caps.append((poke_wrist, poke_wrist + np.array([0.0, 0.0, 0.01]), 0.045, handmodel.SKIN, DRIVER))
        caps += [(*c, DRIVER) for c in
                 self._body(s["driver"], right_wrist=None if lm3 is None else lm3[0], left_wrist=poke_wrist)]
        idbuf = np.zeros((H, W), np.int16)

        cxp, cyp = W / 2, H / 2
        near = 0.12
        for p0, p1, r, a, pid in caps:
            c = self._to_cam(np.array([p0, p1]))
            z0, z1 = c[0, 2], c[1, 2]
            if z0 < near and z1 < near:
                continue
            if z0 < near:
                c[0] = c[0] + (c[1] - c[0]) * ((near - z0) / (z1 - z0))
            if z1 < near:
                c[1] = c[1] + (c[0] - c[1]) * ((near - z1) / (z0 - z1))
            u = cxp + f * c[:, 0] / c[:, 2]
            v = cyp + f * c[:, 1] / c[:, 2]
            R = f * r / c[:, 2]
            x0 = max(0, int(math.floor(min(u - R))))
            x1 = min(W, int(math.ceil(max(u + R))) + 1)
            y0 = max(0, int(math.floor(min(v - R))))
            y1 = min(H, int(math.ceil(max(v + R))) + 1)
            if x0 >= x1 or y0 >= y1:
                continue
            X = np.arange(x0, x1, dtype=np.float32)[None, :]
            Y = np.arange(y0, y1, dtype=np.float32)[:, None]
            du, dv = u[1] - u[0], v[1] - v[0]
            L2 = du * du + dv * dv
            t = np.clip(((X - u[0]) * du + (Y - v[0]) * dv) / L2, 0, 1) if L2 > 1e-6 else np.zeros((1, 1), np.float32)
            d2 = (X - (u[0] + t * du)) ** 2 + (Y - (v[0] + t * dv)) ** 2
            Rt = R[0] + (R[1] - R[0]) * t
            m = d2 < Rt * Rt
            if not np.any(m):
                continue
            zc = c[0, 2] + (c[1, 2] - c[0, 2]) * t
            surf = np.broadcast_to(zc - r * np.sqrt(np.clip(1.0 - d2 / (Rt * Rt), 0, 1)), m.shape)
            sub = zbuf[y0:y1, x0:x1]
            upd = m & (surf < sub)
            sub[upd] = surf[upd]
            alb[y0:y1, x0:x1][upd] = a
            idbuf[y0:y1, x0:x1][upd] = pid

        # --- cieniowanie: oświetlenie hali + oświetlacz IR przy kamerze ---
        valid = np.isfinite(zbuf)
        z = np.where(valid, zbuf, 30.0).astype(np.float32)
        gy, gx = np.gradient(z)
        sc = z / f
        cosv = 1.0 / np.sqrt(1.0 + (gx / sc) ** 2 + (gy / sc) ** 2)
        inv2 = 1.0 / (z * z)
        light = alb * (0.56 * (0.4 + 0.6 * cosv) + 0.42 * cosv * inv2) + dots * alb * 0.16 * inv2
        img = np.clip(light, 0, 1.15) ** 0.8 * vig
        img = cv2.GaussianBlur(img, (3, 3), 0.65)
        img = img + np.random.normal(0, 0.016, img.shape).astype(np.float32)
        gray = np.clip(img * 235 + 6, 0, 255).astype(np.uint8)

        # --- osoby "wykryte przez sieć": ramka + odległość (mediana głębi sylwetki) ---
        people = []
        for pid in np.unique(idbuf):
            if pid == 0:
                continue
            ys_, xs_ = np.nonzero(idbuf == pid)
            if len(xs_) < 350:
                continue
            people.append({
                "bbox": [round(xs_.min() / W, 4), round(ys_.min() / H, 4),
                         round((xs_.max() + 1) / W, 4), round((ys_.max() + 1) / H, 4)],
                "conf": round(0.86 + 0.08 * math.sin(time.monotonic() * 0.7 + int(pid) * 1.7), 2),
                "dist_m": round(float(np.median(z[ys_, xs_])), 2),
            })
        return gray, self._hand_obs(lm3, s, f, W, H), people

    def _hand_obs(self, lm3: np.ndarray | None, s: dict, f: float, W: int, H: int) -> dict | None:
        if lm3 is None or s["hand_vis"] < 0.85:
            return None
        c = self._to_cam(lm3)
        if np.any(c[:, 2] < 0.12):
            return None
        u = (W / 2 + f * c[:, 0] / c[:, 2]) / W
        v = (H / 2 + f * c[:, 1] / c[:, 2]) / H
        inside = (u > 0.01) & (u < 0.99) & (v > 0.01) & (v < 0.99)
        if inside.mean() < 0.9:
            return None
        noise = np.random.normal(0, 0.0015, (21, 2))
        lm2 = np.stack([u, v], 1) + noise
        palm = lm2[list(PALM_IDX)]
        return {"present": True, "gesture": "FIVE" if s["hand_open"] else "FIST",
                "dist_m": round(float(c[0, 2]) + float(np.random.normal(0, 0.004)), 3), "dist_src": "stereo",
                "x_norm": round(float(palm[:, 0].mean()), 4), "y_norm": round(float(palm[:, 1].mean()), 4),
                "landmarks": [[round(float(a), 4), round(float(b), 4)] for a, b in lm2],
                "bbox": None, "score": 0.97}
