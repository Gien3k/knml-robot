"""Wspólny, spójny świat symulacji dla sensorów w trybie Mock.

Układ współrzędnych świata = pozycja startowa robota:
  X – do przodu (w stronę odwiedzających), Y – w lewo, Z – w górę.

LiDAR i OAK-D patrzą na TEN SAM świat, więc radar, obraz i detekcje są zgodne.
Kamera jest symulowana fizycznie: postacie z brył 3D (kapsuł) trafiają do bufora
głębi, z którego powstaje (1) mapa głębi jak ze stereo OAK-D – ta sama, na której
działa prawdziwy klasyfikator gestów – oraz (2) obraz w podczerwieni z cieniowaniem,
wzorem projektora kropek i szumem matrycy (OAK-D Pro ma oświetlacz IR).
"""
from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

from ..config import settings

# Scenariusz "kierowcy": (czas trwania [s], faza)
SCRIPT: list[tuple[float, str]] = [
    (4.0, "approach"),
    (2.0, "idle"),
    (5.0, "open_center"),
    (3.0, "open_left"),
    (3.0, "open_right"),
    (3.0, "fist"),
    (2.0, "idle"),
    (3.5, "poke"),
    (4.0, "open_center"),
    (5.0, "leave"),
]
SCRIPT_LEN = sum(d for d, _ in SCRIPT)
HAND_Z = 1.0          # wysokość dłoni [m] – tuż nad kamerą robota stojącego na stole
SKIN = 0.92           # skóra jest jasna w bliskiej podczerwieni


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
    role: str = "walker"
    radius: float = 0.18
    heading: float = math.pi


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


class SimWorld:
    def __init__(self, seed: int = 7) -> None:
        self.lock = threading.Lock()
        self.rng = random.Random(seed)
        self.t0 = time.monotonic()
        self._last = self.t0
        self.robot = np.zeros(3)                 # x, y, theta (świat)
        self._wheels = (0.0, 0.0)
        self.motion_enabled = False              # True w trybie "mat"

        segs = [
            (-1.0, -2.2, -1.0, 2.2),             # tylna ściana stoiska
            (-1.0, 2.2, 0.7, 2.2),               # ścianka lewa
            (-1.0, -2.2, 0.7, -2.2),             # ścianka prawa
            (5.0, -4.5, 5.0, 4.5),               # stoisko naprzeciwko
            (-0.12, 0.42, -0.12, 1.08),          # monitor na stole
            (-0.20, 0.42, -0.20, 1.08),
            (-0.70, -1.75, -0.70, -1.05),        # roll-up
        ]
        self.segments = np.array(segs, dtype=np.float64)
        self.pillars = np.array([[3.3, -2.9, 0.25]], dtype=np.float64)

        self.people: list[Person] = []
        for i in range(4):
            p = Person(pid=i + 1, x=self.rng.uniform(1.5, 4.0), y=self.rng.uniform(-2.5, 2.5),
                       height=self.rng.uniform(1.6, 1.88), albedo=self.rng.uniform(0.32, 0.7),
                       speed=self.rng.uniform(0.45, 1.0))
            self._new_target(p, 0.0)
            self.people.append(p)
        self.driver = Person(pid=9, x=2.6, y=1.8, height=1.76, albedo=0.5, speed=0.9, role="driver")
        self.people.append(self.driver)

        self.phase = "approach"
        self.phase_t = 0.0
        self.hand_lateral = 0.0
        self.hand_reach = 0.0
        self.hand_visible = False
        self.hand_open = False
        self.hand_world = np.array([0.0, 0.0, HAND_Z])
        self._cache: dict = {}

    # ------------------------------------------------------------------ czas
    def _new_target(self, p: Person, now: float) -> None:
        p.tx = self.rng.uniform(0.95, 4.3)
        p.ty = self.rng.uniform(-3.0, 3.0)
        p.wait_until = now + self.rng.uniform(0.0, 3.5)

    def set_wheels(self, left: float, right: float) -> None:
        self._wheels = (left, right)

    def tick(self) -> None:
        with self.lock:
            now = time.monotonic()
            dt = min(0.1, now - self._last)
            if dt <= 0:
                return
            self._last = now
            t = now - self.t0
            self._step_robot(dt)
            for p in self.people:
                if p.role == "walker":
                    self._step_walker(p, dt, t)
            self._step_driver(dt, t)

    def _step_robot(self, dt: float) -> None:
        if not self.motion_enabled:
            return
        left, right = self._wheels
        v = (left + right) / 2.0
        w = (right - left) / settings.track_width_m
        th = self.robot[2]
        self.robot[0] += v * math.cos(th) * dt
        self.robot[1] += v * math.sin(th) * dt
        self.robot[2] = (th + w * dt + math.pi) % (2 * math.pi) - math.pi

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
        p.x = max(p.x, 0.9)
        ddx, ddy = p.x - self.driver.x, p.y - self.driver.y
        dd = math.hypot(ddx, ddy)
        if 0 < dd < 0.55:
            p.x += ddx / dd * (0.55 - dd)
            p.y += ddy / dd * (0.55 - dd)

    def _step_driver(self, dt: float, t: float) -> None:
        tc = t % SCRIPT_LEN
        acc = 0.0
        phase, pt, dur = "idle", 0.0, 1.0
        for d, ph in SCRIPT:
            if tc < acc + d:
                phase, pt, dur = ph, tc - acc, d
                break
            acc += d
        self.phase, self.phase_t = phase, pt
        drv = self.driver
        if phase == "approach":
            a = min(1.0, pt / dur)
            a = a * a * (3 - 2 * a)
            drv.x, drv.y = 2.6 + (1.05 - 2.6) * a, 1.8 + (0.02 - 1.8) * a
        elif phase == "leave":
            a = min(1.0, pt / dur)
            drv.x, drv.y = 1.05 + (3.6 - 1.05) * a, 0.02 + (-2.2 - 0.02) * a
        else:
            drv.x, drv.y = 1.05, 0.02
        drv.heading = math.atan2(self.robot[1] - drv.y, self.robot[0] - drv.x)

        lateral_target = {"open_left": 0.24, "open_right": -0.24}.get(phase, 0.0)
        self.hand_lateral += (lateral_target - self.hand_lateral) * min(1.0, dt * 5.0)
        self.hand_visible = phase not in ("approach", "leave", "idle")
        self.hand_open = phase.startswith("open") or phase == "poke"

        rx, ry = self.robot[0], self.robot[1]
        ux, uy = rx - drv.x, ry - drv.y
        dist = math.hypot(ux, uy) or 1.0
        ux, uy = ux / dist, uy / dist
        if phase == "poke":
            target_reach = dist - (settings.robot_half_length_m + 0.07)
        else:
            target_reach = 0.42 if self.hand_visible else 0.15
        self.hand_reach += (target_reach - self.hand_reach) * min(1.0, dt * 3.0)
        px, py = -uy, ux
        self.hand_world = np.array([drv.x + ux * self.hand_reach + px * self.hand_lateral,
                                    drv.y + uy * self.hand_reach + py * self.hand_lateral, HAND_Z])

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "robot": self.robot.copy(),
                "people": [(p.pid, p.x, p.y, p.height, p.albedo, p.radius, p.role, p.heading) for p in self.people],
                "hand": self.hand_world.copy(),
                "hand_visible": self.hand_visible,
                "hand_open": self.hand_open,
                "phase": self.phase,
            }

    # ---------------------------------------------------------------- LiDAR
    def lidar_scan(self, n: int = 360) -> np.ndarray:
        """Zasięgi [m] dla n kątów w układzie robota (CCW od przodu). 0 = brak echa."""
        s = self.snapshot()
        rx, ry, rth = s["robot"]
        ang = np.deg2rad(np.arange(n) * (360.0 / n)) + rth
        d = np.stack([np.cos(ang), np.sin(ang)], 1)
        o = np.array([rx, ry])
        best = np.full(n, np.inf)
        circles = [(p[1], p[2], p[5]) for p in s["people"]]
        circles += [tuple(c) for c in self.pillars]
        if s["hand_visible"]:
            circles.append((s["hand"][0], s["hand"][1], 0.045))
        c = np.array(circles)
        oc = o[None, :] - c[:, :2]
        b = d @ oc.T
        cc = (oc ** 2).sum(1) - c[:, 2] ** 2
        disc = b ** 2 - cc[None, :]
        with np.errstate(invalid="ignore"):
            t = -b - np.sqrt(disc)
        t[(disc < 0) | (t <= 0)] = np.inf
        best = np.minimum(best, t.min(1))
        seg = self.segments
        p, q = seg[:, :2], seg[:, 2:]
        e = q - p
        w = p - o[None, :]
        denom = d[:, 0:1] * e[None, :, 1] - d[:, 1:2] * e[None, :, 0]
        with np.errstate(divide="ignore", invalid="ignore"):
            tt = (w[None, :, 0] * e[None, :, 1] - w[None, :, 1] * e[None, :, 0]) / denom
            ss = (w[None, :, 0] * d[:, 1:2] - w[None, :, 1] * d[:, 0:1]) / denom
        ok = (np.abs(denom) > 1e-9) & (tt > 0) & (ss >= 0) & (ss <= 1)
        best = np.minimum(best, np.where(ok, tt, np.inf).min(1))
        r = best + np.random.normal(0, 0.008, n)
        r[~np.isfinite(best) | (best > settings.lidar_max_range_m)] = 0.0
        r[np.random.random(n) < 0.02] = 0.0
        return r

    # ---------------------------------------------------------------- kamera
    @staticmethod
    def _to_cam(pts: np.ndarray, robot: np.ndarray, h: float) -> np.ndarray:
        """Punkty świata (N,3) -> kamera (N,3): x w prawo, y w dół, z do przodu."""
        cp, sp = math.cos(robot[2]), math.sin(robot[2])
        dx, dy, dz = pts[:, 0] - robot[0], pts[:, 1] - robot[1], pts[:, 2] - h
        return np.stack([-(-sp * dx + cp * dy), -dz, cp * dx + sp * dy], 1)

    def _body(self, p, s: dict, cam_pos: np.ndarray) -> list[tuple]:
        """Postać z kapsuł: (p0, p1, promień, albedo)."""
        _, x, y, H, alb, _, role, heading = p
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
                 (P(0, 0, 1.40 * k), P(0, 0, 1.50 * k), 0.05, SKIN * 0.9),
                 (P(0.01, 0, 1.57 * k), P(0.0, 0, 1.65 * k), 0.093, SKIN),
                 (P(-0.025, 0, 1.64 * k), P(-0.03, 0, 1.69 * k), 0.088, 0.22)]   # włosy
        sl, sr = P(0, 0.19, 1.39 * k), P(0, -0.19, 1.39 * k)
        el, wl = P(0.0, 0.22, 1.11 * k), P(0.04, 0.23, 0.86 * k)
        caps += [(sl, el, 0.046, alb), (el, wl, 0.038, alb), (wl, P(0.05, 0.235, 0.79 * k), 0.036, SKIN)]
        if role == "driver" and s["hand_visible"]:
            hw = s["hand"]
            e = sr + (hw - sr) * 0.5 + np.array([0.0, 0.0, -0.10])
            wr = hw + _unit(e - hw) * 0.065
            caps += [(sr, e, 0.046, alb), (e, wr, 0.037, alb)]
            caps += self._hand(hw, cam_pos, s["hand_open"])
        else:
            er, wr = P(0.0, -0.22, 1.11 * k), P(0.04, -0.23, 0.86 * k)
            caps += [(sr, er, 0.046, alb), (er, wr, 0.038, alb), (wr, P(0.05, -0.235, 0.79 * k), 0.036, SKIN)]
        return caps

    @staticmethod
    def _hand(hw: np.ndarray, cam_pos: np.ndarray, open_: bool) -> list[tuple]:
        v = cam_pos - hw
        v[2] = 0.0
        v = _unit(v)
        lat = np.array([-v[1], v[0], 0.0])
        up = np.array([0.0, 0.0, 1.0])
        if not open_:
            return [(hw + up * 0.008, hw - up * 0.012, 0.047, SKIN)]
        caps = [(hw - up * 0.028 + lat * 0.013, hw + up * 0.022 + lat * 0.013, 0.03, SKIN),
                (hw - up * 0.028 - lat * 0.013, hw + up * 0.022 - lat * 0.013, 0.03, SKIN)]
        for i, (ang, length) in enumerate(((-27, 0.068), (-9, 0.08), (9, 0.077), (27, 0.062))):
            a = math.radians(ang)
            d = up * math.cos(a) + lat * math.sin(a)
            base = hw + up * 0.035 + lat * (-0.027 + i * 0.018)
            caps.append((base, base + d * length, 0.0088, SKIN))
        a = math.radians(-64)
        d = up * math.cos(a) + lat * math.sin(a)
        base = hw - lat * 0.03 - up * 0.008
        caps.append((base, base + d * 0.056, 0.0105, SKIN))
        return caps

    def _grids(self, W: int, H: int, f: float) -> tuple:
        key = ("g", W, H, round(f, 3))
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

    def render_ir(self, cam: dict) -> tuple[np.ndarray, np.ndarray, list[dict]]:
        """Zwraca (obraz IR uint8 HxW, głębia Z [mm] uint16 HxW, widoczne osoby)."""
        s = self.snapshot()
        W, H = cam["w"], cam["h"]
        f = (W / 2) / math.tan(math.radians(cam["hfov"]) / 2)
        hm = cam["h_m"]
        rob = s["robot"]
        xc, yc, dots, vig = self._grids(W, H, f)
        cp, sp = math.cos(rob[2]), math.sin(rob[2])

        # --- tło: podłoga hali z fugami płyt + ściana stoiska naprzeciwko ---
        with np.errstate(divide="ignore", invalid="ignore"):
            zf = np.where(yc > 1e-4, hm / yc, np.inf)
        zbuf = np.broadcast_to(zf, (H, W)).astype(np.float32).copy()
        with np.errstate(invalid="ignore"):
            Xw = rob[0] + zbuf * (cp + xc * sp)
            Yw = rob[1] + zbuf * (sp - xc * cp)
            seam = (np.abs((Xw % 1.0) - 0.5) > 0.485) | (np.abs((Yw % 1.0) - 0.5) > 0.485)
        alb = np.where(np.isfinite(zbuf), np.where(seam, 0.13, 0.21), 0.0).astype(np.float32)
        den = cp + xc * sp
        with np.errstate(divide="ignore", invalid="ignore"):
            tw = np.where(den > 0.05, (5.0 - rob[0]) / den, np.inf)
            hgt = hm - yc * tw
            yw = rob[1] + tw * (sp - xc * cp)
            panel = (np.abs((yw % 1.2) - 0.6) > 0.585) | (np.abs(hgt - 2.2) < 0.02)
        wall = (hgt > 0) & (hgt < 2.6) & (tw < zbuf)
        zbuf = np.where(wall, tw, zbuf).astype(np.float32)
        alb = np.where(wall, np.where(panel, 0.3, 0.42), alb).astype(np.float32)
        idbuf = np.zeros((H, W), np.int16)

        # --- postacie z kapsuł (z-bufor) ---
        cam_pos = np.array([rob[0], rob[1], hm])
        cx, cy = W / 2, H / 2
        for p in s["people"]:
            for p0, p1, r, a in self._body(p, s, cam_pos):
                c = self._to_cam(np.array([p0, p1]), rob, hm)
                z0, z1 = c[0, 2], c[1, 2]
                near = 0.12
                if z0 < near and z1 < near:
                    continue
                if z0 < near:
                    c[0] = c[0] + (c[1] - c[0]) * ((near - z0) / (z1 - z0))
                if z1 < near:
                    c[1] = c[1] + (c[0] - c[1]) * ((near - z1) / (z0 - z1))
                u = cx + f * c[:, 0] / c[:, 2]
                v = cy + f * c[:, 1] / c[:, 2]
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
                if L2 > 1e-6:
                    t = np.clip(((X - u[0]) * du + (Y - v[0]) * dv) / L2, 0, 1)
                else:
                    t = np.zeros((1, 1), np.float32)
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
                idbuf[y0:y1, x0:x1][upd] = p[0]

        # --- cieniowanie (oświetlenie hali + oświetlacz IR przy kamerze) ---
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

        # --- głębia jak ze stereo (szum rośnie z kwadratem odległości) ---
        dz = np.random.standard_normal((H, W)).astype(np.float32) * (1.5 + 2.5 * z * z)
        depth = np.where(valid & (z < 8.0), z * 1000.0 + dz, 0.0)
        depth = np.clip(depth, 0, 65535).astype(np.uint16)

        # --- widoczne osoby (jak detekcje sieci) ---
        people = []
        for p in s["people"]:
            ys_, xs_ = np.nonzero(idbuf == p[0])
            if len(xs_) < 350:
                continue
            zz = float(np.median(z[ys_, xs_]))
            uc, vc = float(xs_.mean()), float(ys_.mean())
            people.append({
                "pid": p[0], "role": p[6],
                "bbox": [xs_.min() / W, ys_.min() / H, (xs_.max() + 1) / W, (ys_.max() + 1) / H],
                "xyz": [(uc - cx) / f * zz, -(vc - cy) / f * zz, zz],
            })
        return gray, depth, people


_world: SimWorld | None = None
_world_lock = threading.Lock()


def get_world() -> SimWorld:
    global _world
    with _world_lock:
        if _world is None:
            _world = SimWorld()
        return _world
