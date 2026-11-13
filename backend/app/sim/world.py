"""Wspólny, spójny świat symulacji dla wszystkich sensorów w trybie Mock.

Układ współrzędnych świata = pozycja startowa robota:
  X – do przodu (w stronę alejki / odwiedzających), Y – w lewo, Z – w górę.

Wszystkie symulowane sensory (LiDAR, OAK RGB + głębia, FPV) patrzą na TEN SAM
świat, więc radar, wideo i detekcje są ze sobą zgodne – można testować fuzję.
Scenariusz "kierowcy" przechodzi cyklicznie przez wszystkie gesty, a także
"wkłada rękę" pod robota, żeby wyzwolić STOP AWARYJNY z LiDAR-u.
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

# ----------------------------------------------------------------------------
# Scenariusz kierowcy: (czas trwania [s], faza)
# ----------------------------------------------------------------------------
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
HAND_Z = 1.0  # wysokość dłoni nad podłogą [m] – tuż nad kamerą robota stojącego na stole

PERSON_COLORS = [  # BGR, stonowane
    (150, 110, 70), (70, 95, 160), (110, 140, 90), (140, 90, 130),
    (80, 130, 150), (120, 120, 120), (60, 80, 120),
]


@dataclass
class Person:
    pid: int
    x: float
    y: float
    height: float
    color: tuple[int, int, int]
    speed: float
    tx: float = 0.0
    ty: float = 0.0
    wait_until: float = 0.0
    role: str = "walker"
    radius: float = 0.18


class SimWorld:
    def __init__(self, seed: int = 7) -> None:
        self.lock = threading.Lock()
        self.rng = random.Random(seed)
        self.t0 = time.monotonic()
        self._last = self.t0
        self.robot = np.zeros(3)                 # x, y, theta (świat)
        self._wheels = (0.0, 0.0)
        self.motion_enabled = False              # True w trybie "mat"

        # --- statyczne otoczenie (odcinki: x1, y1, x2, y2) ---
        segs = [
            (-1.0, -2.2, -1.0, 2.2),             # tylna ściana stoiska
            (-1.0, 2.2, 0.7, 2.2),               # ścianka lewa
            (-1.0, -2.2, 0.7, -2.2),             # ścianka prawa
            (5.0, -4.5, 5.0, 4.5),               # stoisko naprzeciwko
            (-0.12, 0.42, -0.12, 1.08),          # monitor na stole (front)
            (-0.20, 0.42, -0.20, 1.08),          # monitor (tył)
            (-0.70, -1.75, -0.70, -1.05),        # roll-up
        ]
        self.segments = np.array(segs, dtype=np.float64)
        self.pillars = np.array([[3.3, -2.9, 0.25]], dtype=np.float64)

        # --- ludzie ---
        self.people: list[Person] = []
        for i in range(4):
            p = Person(
                pid=i + 1,
                x=self.rng.uniform(1.5, 4.0), y=self.rng.uniform(-2.5, 2.5),
                height=self.rng.uniform(1.58, 1.88),
                color=PERSON_COLORS[i % len(PERSON_COLORS)],
                speed=self.rng.uniform(0.45, 1.0),
            )
            self._new_target(p, 0.0)
            self.people.append(p)
        self.driver = Person(pid=9, x=2.6, y=1.8, height=1.76, color=(95, 120, 175),
                             speed=0.9, role="driver")
        self.people.append(self.driver)

        # stan dłoni kierowcy
        self.phase = "approach"
        self.phase_t = 0.0
        self.hand_lateral = 0.0
        self.hand_reach = 0.0
        self.hand_visible = False
        self.hand_open = False
        self.hand_world = np.array([0.0, 0.0, HAND_Z])

        self._bg_cache: dict = {}
        self._depth_bg_cache: dict = {}

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
        # nie wchodzimy na stół / za blisko kierowcy
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

        # pozycja ciała
        if phase == "approach":
            a = min(1.0, pt / dur)
            a = a * a * (3 - 2 * a)
            drv.x, drv.y = 2.6 + (1.05 - 2.6) * a, 1.8 + (0.02 - 1.8) * a
        elif phase == "leave":
            a = min(1.0, pt / dur)
            drv.x, drv.y = 1.05 + (3.6 - 1.05) * a, 0.02 + (-2.2 - 0.02) * a
        else:
            drv.x, drv.y = 1.05, 0.02

        # dłoń
        lateral_target = {"open_left": 0.24, "open_right": -0.24}.get(phase, 0.0)
        k = min(1.0, dt * 5.0)
        self.hand_lateral += (lateral_target - self.hand_lateral) * k
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
        px, py = -uy, ux  # prostopadle (w lewo patrząc od kierowcy)
        self.hand_world = np.array([
            drv.x + ux * self.hand_reach + px * self.hand_lateral,
            drv.y + uy * self.hand_reach + py * self.hand_lateral,
            HAND_Z,
        ])

    # ---------------------------------------------------------------- odczyt
    def snapshot(self) -> dict:
        with self.lock:
            return {
                "robot": self.robot.copy(),
                "people": [(p.pid, p.x, p.y, p.height, p.color, p.radius, p.role) for p in self.people],
                "hand": self.hand_world.copy(),
                "hand_visible": self.hand_visible,
                "hand_open": self.hand_open,
                "phase": self.phase,
            }

    # ---------------------------------------------------------------- LiDAR
    def lidar_scan(self, n: int = 360) -> np.ndarray:
        """Zwraca zasięgi [m] dla n kątów w układzie robota (CCW od przodu). 0 = brak echa."""
        s = self.snapshot()
        rx, ry, rth = s["robot"]
        ang = np.deg2rad(np.arange(n) * (360.0 / n)) + rth
        d = np.stack([np.cos(ang), np.sin(ang)], 1)
        o = np.array([rx, ry])
        best = np.full(n, np.inf)

        circles = [(x, y, r) for _, x, y, _, _, r, _ in s["people"]]
        circles += [tuple(c) for c in self.pillars]
        if s["hand_visible"]:
            circles.append((s["hand"][0], s["hand"][1], 0.045))
        if circles:
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
        tt = np.where(ok, tt, np.inf)
        best = np.minimum(best, tt.min(1))

        noise = np.random.normal(0, 0.008, n)
        r = best + noise
        r[~np.isfinite(best) | (best > settings.lidar_max_range_m)] = 0.0
        r[np.random.random(n) < 0.02] = 0.0  # pojedyncze zgubione echa
        return r

    # ------------------------------------------------------------ kamera
    @staticmethod
    def _cam_basis(robot: np.ndarray, yaw: float, pitch: float):
        psi = robot[2] + yaw
        return robot[0], robot[1], math.cos(psi), math.sin(psi), math.cos(pitch), math.sin(pitch)

    @staticmethod
    def _to_cam(pts: np.ndarray, basis, h: float) -> np.ndarray:
        """Punkty świata (N,3) -> kamera (N,3): x w prawo, y w dół, z do przodu."""
        rx, ry, cp, sp, cf, sf = basis
        dx, dy, dz = pts[:, 0] - rx, pts[:, 1] - ry, pts[:, 2] - h
        xf = cp * dx + sp * dy
        yl = -sp * dx + cp * dy
        fwd = xf * cf + dz * sf
        up = -xf * sf + dz * cf
        return np.stack([-yl, -up, fwd], 1)

    def camera_people(self, cam: dict, snap: dict | None = None) -> list[dict]:
        """Ludzie widoczni w kamerze: bbox (znormalizowany), xyz kamery [m] (x prawo, y góra, z przód)."""
        s = snap or self.snapshot()
        w, h = cam["w"], cam["h"]
        fx = (w / 2) / math.tan(math.radians(cam["hfov"]) / 2)
        basis = self._cam_basis(s["robot"], cam.get("yaw", 0.0), cam.get("pitch", 0.0))
        out = []
        for pid, x, y, H, _, _, role in s["people"]:
            pts = np.array([[x, y, 0.0], [x, y, H], [x, y, 1.2]])
            c = self._to_cam(pts, basis, cam["h_m"])
            if c[2, 2] < 0.4:
                continue
            z = c[2, 2]
            u = w / 2 + fx * c[2, 0] / z
            half = fx * 0.26 / z
            v_top = h / 2 + fx * c[1, 1] / max(c[1, 2], 0.05)
            v_bot = h / 2 + fx * c[0, 1] / max(c[0, 2], 0.05)
            x1, x2 = (u - half) / w, (u + half) / w
            y1, y2 = v_top / h, v_bot / h
            cx1, cx2, cy1, cy2 = max(0, x1), min(1, x2), max(0, y1), min(1, y2)
            if cx2 - cx1 <= 0 or cy2 - cy1 <= 0:
                continue
            vis = (cx2 - cx1) * (cy2 - cy1) / max(1e-6, (x2 - x1) * (y2 - y1))
            if vis < 0.25:
                continue
            out.append({
                "pid": pid, "role": role,
                "bbox": [cx1, cy1, cx2, cy2],
                "xyz": [float(c[2, 0]), float(-c[2, 1]), float(z)],
            })
        return out

    def _background(self, w: int, h: int, pitch: float, hfov: float, tint: tuple) -> np.ndarray:
        key = (w, h, round(pitch, 3), hfov, tint)
        bg = self._bg_cache.get(key)
        if bg is not None:
            return bg
        fx = (w / 2) / math.tan(math.radians(hfov) / 2)
        horizon = h / 2 + fx * math.tan(pitch)
        yy = np.arange(h, dtype=np.float32)[:, None]
        top = np.array([22, 17, 14], np.float32) * np.array(tint, np.float32)
        mid = np.array([44, 36, 30], np.float32) * np.array(tint, np.float32)
        floor = np.array([20, 16, 13], np.float32) * np.array(tint, np.float32)
        a = np.clip(yy / max(horizon, 1), 0, 1)
        sky = top[None, None, :] * (1 - a[..., None]) + mid[None, None, :] * a[..., None]
        b = np.clip((yy - horizon) / max(h - horizon, 1), 0, 1)
        grd = mid[None, None, :] * (1 - b[..., None]) + floor[None, None, :] * b[..., None]
        img = np.where((yy < horizon)[..., None], sky, grd)
        img = np.broadcast_to(img, (h, w, 3)).copy()
        # winieta
        xs = (np.arange(w, dtype=np.float32) - w / 2) / (w / 2)
        ys = (np.arange(h, dtype=np.float32) - h / 2) / (h / 2)
        vig = 1.0 - 0.45 * np.clip(xs[None, :] ** 2 * 0.6 + ys[:, None] ** 2 * 0.8, 0, 1)
        img *= vig[..., None]
        bg = np.clip(img, 0, 255).astype(np.uint8)
        self._bg_cache[key] = bg
        return bg

    def render(self, cam: dict, label: str) -> np.ndarray:
        """Renderuje widok kamery (BGR). cam: w, h, hfov, h_m, yaw, pitch, tint."""
        s = self.snapshot()
        w, h = cam["w"], cam["h"]
        pitch, yaw = cam.get("pitch", 0.0), cam.get("yaw", 0.0)
        fx = (w / 2) / math.tan(math.radians(cam["hfov"]) / 2)
        img = self._background(w, h, pitch, cam["hfov"], cam.get("tint", (1, 1, 1))).copy()
        basis = self._cam_basis(s["robot"], yaw, pitch)
        hm = cam["h_m"]

        def proj(c):
            return (w / 2 + fx * c[0] / c[2], h / 2 + fx * c[1] / c[2])

        # --- stoisko naprzeciwko (ściana) ---
        wall = np.array([[5.0, 4.5, 0], [5.0, -4.5, 0], [5.0, -4.5, 2.6], [5.0, 4.5, 2.6]])
        cw = self._to_cam(wall, basis, hm)
        if (cw[:, 2] > 0.3).all():
            poly = np.array([proj(c) for c in cw], np.int32)
            cv2.fillConvexPoly(img, poly, (34, 28, 24), cv2.LINE_AA)
            cv2.polylines(img, [poly], True, (52, 44, 38), 1, cv2.LINE_AA)

        # --- siatka podłogi ---
        lines = [((gx, -4.5, 0.0), (gx, 4.5, 0.0)) for gx in np.arange(0.5, 5.01, 0.5)]
        lines += [((0.3, gy, 0.0), (5.0, gy, 0.0)) for gy in np.arange(-4.5, 4.51, 0.5)]
        for a, b in lines:
            ca, cb = self._to_cam(np.array([a, b]), basis, hm)
            near = 0.25
            if ca[2] < near and cb[2] < near:
                continue
            if ca[2] < near:
                ca = ca + (cb - ca) * ((near - ca[2]) / (cb[2] - ca[2]))
            if cb[2] < near:
                cb = cb + (ca - cb) * ((near - cb[2]) / (ca[2] - cb[2]))
            pa, pb = proj(ca), proj(cb)
            cv2.line(img, (int(pa[0]), int(pa[1])), (int(pb[0]), int(pb[1])), (48, 40, 34), 1, cv2.LINE_AA)

        # --- ludzie (od najdalszego) ---
        order = []
        for pid, x, y, H, color, _, role in s["people"]:
            c = self._to_cam(np.array([[x, y, 1.1]]), basis, hm)[0]
            if c[2] > 0.35:
                order.append((c[2], pid, x, y, H, color, role))
        order.sort(reverse=True)
        for z, pid, x, y, H, color, role in order:
            shade = float(np.clip(1.35 - z / 6.0, 0.35, 1.0))
            col = tuple(int(ch * shade) for ch in color)
            dark = tuple(int(ch * shade * 0.6) for ch in color)
            rim = tuple(min(255, int(ch * shade * 1.35 + 18)) for ch in color)
            # układ "w prawo" względem kamery dla szerokości sylwetki
            rx_, ry_, cp, sp, _, _ = basis
            lx, ly = -sp, cp  # wektor w lewo kamery w świecie
            def P(off, zz):
                pt = np.array([[x + lx * off, y + ly * off, zz]])
                c = self._to_cam(pt, basis, hm)[0]
                c[2] = max(c[2], 0.05)
                q = proj(c)
                return [int(q[0]), int(q[1])]
            legs = np.array([P(0.16, 0.95), P(-0.16, 0.95), P(-0.13, 0.0), P(0.13, 0.0)], np.int32)
            torso = np.array([P(0.23, H - 0.25), P(-0.23, H - 0.25), P(-0.18, 0.92), P(0.18, 0.92)], np.int32)
            cv2.fillConvexPoly(img, legs, dark, cv2.LINE_AA)
            cv2.fillConvexPoly(img, torso, col, cv2.LINE_AA)
            cv2.polylines(img, [torso], True, rim, 1, cv2.LINE_AA)
            head = P(0.0, H - 0.11)
            hr = max(2, int(fx * 0.105 / z))
            cv2.circle(img, (head[0], head[1]), hr, rim, -1, cv2.LINE_AA)
            cv2.circle(img, (head[0], head[1]), hr, col, 1, cv2.LINE_AA)

            if role == "driver" and s["hand_visible"]:
                hand = s["hand"]
                hc = self._to_cam(hand[None, :], basis, hm)[0]
                if hc[2] > 0.15:
                    sh = P(-0.2, H - 0.3)
                    hp = proj(hc)
                    hz = hc[2]
                    skin = (int(130 * shade + 40), int(150 * shade + 40), int(190 * shade + 40))
                    cv2.line(img, tuple(sh), (int(hp[0]), int(hp[1])), col, max(2, int(fx * 0.075 / ((z + hz) / 2))), cv2.LINE_AA)
                    self._draw_hand(img, hp, fx / hz, s["hand_open"], skin)

        if label:
            cv2.putText(img, label, (12, h - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (120, 120, 120), 1, cv2.LINE_AA)
        return img

    @staticmethod
    def _draw_hand(img, center, ppm: float, open_: bool, color, value=None) -> None:
        """ppm = piksele na metr w odległości dłoni. value – dla mapy głębi."""
        c = (int(center[0]), int(center[1]))
        col = color if value is None else value
        if open_:
            palm = max(3, int(0.045 * ppm))
            cv2.circle(img, c, palm, col, -1, cv2.LINE_AA if value is None else cv2.LINE_8)
            flen, fw = 0.085 * ppm, max(2, int(0.018 * ppm))
            for ang in (-62, -28, 0, 26, 52):
                a = math.radians(ang - 90)
                L = flen * (0.8 if ang == 52 else 1.0)
                start = (int(c[0] + math.cos(a) * palm * 0.7), int(c[1] + math.sin(a) * palm * 0.7))
                end = (int(c[0] + math.cos(a) * (palm + L)), int(c[1] + math.sin(a) * (palm + L)))
                cv2.line(img, start, end, col, fw, cv2.LINE_AA if value is None else cv2.LINE_8)
            # kciuk
            a = math.radians(160)
            end = (int(c[0] + math.cos(a) * (palm + flen * 0.7)), int(c[1] + math.sin(a) * (palm + flen * 0.5)))
            cv2.line(img, c, end, col, fw, cv2.LINE_AA if value is None else cv2.LINE_8)
        else:
            r = max(3, int(0.05 * ppm))
            cv2.circle(img, c, r, col, -1, cv2.LINE_AA if value is None else cv2.LINE_8)

    def render_depth(self, cam: dict) -> np.ndarray:
        """Mapa głębi Z [mm] (uint16) wyrównana z kamerą RGB – jak z OAK-D."""
        s = self.snapshot()
        w, h = cam["w"], cam["h"]
        fx = (w / 2) / math.tan(math.radians(cam["hfov"]) / 2)
        hm = cam["h_m"]
        key = (w, h, cam["hfov"], hm)
        bg = self._depth_bg_cache.get(key)
        if bg is None:
            v = np.arange(h, dtype=np.float32)[:, None] - h / 2
            with np.errstate(divide="ignore"):
                z = np.where(v > 0, fx * hm / np.maximum(v, 1e-3), 5000.0 / 1000.0)
            z = np.clip(z, 0.0, 5.0) * 1000.0
            bg = np.broadcast_to(z, (h, w)).astype(np.uint16).copy()
            self._depth_bg_cache[key] = bg
        img = bg.copy()
        basis = self._cam_basis(s["robot"], 0.0, 0.0)

        def proj(c):
            return (w / 2 + fx * c[0] / c[2], h / 2 + fx * c[1] / c[2])

        order = []
        for pid, x, y, H, _, _, role in s["people"]:
            c = self._to_cam(np.array([[x, y, 1.1]]), basis, hm)[0]
            if c[2] > 0.35:
                order.append((c[2], x, y, H, role))
        order.sort(reverse=True)
        rx_, ry_, cp, sp, _, _ = basis
        lx, ly = -sp, cp
        for z, x, y, H, role in order:
            val = int(z * 1000)
            def P(off, zz):
                c = self._to_cam(np.array([[x + lx * off, y + ly * off, zz]]), basis, hm)[0]
                c[2] = max(c[2], 0.05)
                q = proj(c)
                return [int(q[0]), int(q[1])]
            body = np.array([P(0.23, H - 0.25), P(-0.23, H - 0.25), P(-0.13, 0.0), P(0.13, 0.0)], np.int32)
            cv2.fillConvexPoly(img, body, val)
            head = P(0.0, H - 0.11)
            cv2.circle(img, tuple(head), max(2, int(fx * 0.105 / z)), val, -1)
            if role == "driver" and s["hand_visible"]:
                hc = self._to_cam(s["hand"][None, :], basis, hm)[0]
                if hc[2] > 0.15:
                    hp = proj(hc)
                    sh = P(-0.2, H - 0.3)
                    arm_val = int(min(z, hc[2] + 0.17) * 1000)
                    cv2.line(img, tuple(sh), (int(hp[0]), int(hp[1])), arm_val, max(2, int(fx * 0.075 / hc[2])))
                    self._draw_hand(img, hp, fx / hc[2], s["hand_open"], None, value=int(hc[2] * 1000))
        # szum pomiarowy jak w stereo
        noise = (np.random.standard_normal((h, w)) * 6).astype(np.int16)
        img = np.clip(img.astype(np.int32) + noise, 0, 65535).astype(np.uint16)
        return img


_world: SimWorld | None = None
_world_lock = threading.Lock()


def get_world() -> SimWorld:
    global _world
    with _world_lock:
        if _world is None:
            _world = SimWorld()
        return _world
