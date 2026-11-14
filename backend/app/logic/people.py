"""Ludzie przy stoisku: obiekty z LiDAR-u śledzone w czasie + potwierdzenie kamerą (fuzja).

* Wejście z LiDAR-u: punkty "nowe" względem nauczonego tła (te same, których używa tarcza).
* Grupowanie: sąsiednie kąty i bliskie punkty -> obiekty; szerokość >= PERSON_MIN_W => "osoba?",
  węższe (dłoń, ręka) => "small" (rysowane jako punkty, nie liczone jako ludzie).
* Śledzenie: dopasowanie najbliższego sąsiada, stałe numery, prędkość, ślad ruchu.
* Fuzja: osoba wykryta kamerą (OAK-D, pozycja 3D) dopasowana do obiektu z LiDAR-u
  => obiekt "potwierdzony przez 2 czujniki".
LiDAR 2D widzi tylko płaszczyznę na wysokości głowicy – nie odróżni człowieka od przesuwanej
torby; dlatego bez potwierdzenia kamerą obiekt jest tylko "obiektem".
"""
from __future__ import annotations

import math
from collections import deque

import numpy as np

N_BINS = 360
_ANG = np.deg2rad(np.arange(N_BINS, dtype=np.float64))
COS, SIN = np.cos(_ANG), np.sin(_ANG)

PERSON_MIN_W = 0.15        # [m] węższe obiekty to dłoń / ręka
PERSON_MIN_D = 0.6         # [m] bliżej LiDAR-u to ręka sięgająca do robota, nie tułów
MAX_W = 1.0                # [m] szersze to fragment ściany, nie człowiek
BIN_RAD = math.radians(1.0)


def clusters(ranges: np.ndarray, fg_bins, max_range: float = 4.0) -> list[dict]:
    """Punkty pierwszego planu -> obiekty {x, y, w, d, n} w układzie robota."""
    r = np.asarray(ranges, dtype=np.float64)
    idx = sorted(int(i) for i in fg_bins if 0.0 < r[int(i)] <= max_range)
    if not idx:
        return []
    pts = {i: (r[i] * COS[i], r[i] * SIN[i]) for i in idx}

    def near(a: int, b: int) -> bool:
        return math.dist(pts[a], pts[b]) < 0.25

    groups: list[list[int]] = [[idx[0]]]
    for a, b in zip(idx, idx[1:]):
        if b - a <= 3 and near(a, b):
            groups[-1].append(b)
        else:
            groups.append([b])
    # zawinięcie 359° -> 0°
    if len(groups) > 1 and groups[0][0] <= 2 and groups[-1][-1] >= N_BINS - 3 and near(groups[-1][-1], groups[0][0]):
        groups[0] = groups.pop() + groups[0]

    out = []
    for g in groups:
        if len(g) < 2:
            continue
        p = np.array([pts[i] for i in g])
        c = p.mean(0)
        # rozstaw skrajnych punktów + jeden krok kątowy (z daleka punkty są rzadkie)
        w = max(float(np.linalg.norm(p[0] - p[-1])) + float(np.hypot(*c)) * BIN_RAD, 0.05)
        if w > MAX_W:
            continue
        out.append({"x": float(c[0]), "y": float(c[1]), "w": w, "d": float(math.hypot(*c)), "n": len(g)})

    # wyciągnięta ręka zasłania środek tułowia -> echo ciała rozpada się na kawałki
    # w tej samej odległości; scal je z powrotem w jedną sylwetkę
    merged = True
    while merged:
        merged = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                if abs(a["d"] - b["d"]) < 0.15 and math.hypot(a["x"] - b["x"], a["y"] - b["y"]) < 0.45:
                    n = a["n"] + b["n"]
                    x = (a["x"] * a["n"] + b["x"] * b["n"]) / n
                    y = (a["y"] * a["n"] + b["y"] * b["n"]) / n
                    w = min(MAX_W, math.hypot(a["x"] - b["x"], a["y"] - b["y"]) + (a["w"] + b["w"]) / 2)
                    out[i] = {"x": x, "y": y, "w": w, "d": math.hypot(x, y), "n": n}
                    out.pop(j)
                    merged = True
                    break
            if merged:
                break
    return out


class PeopleTracker:
    def __init__(self, gate: float = 0.5, confirm_hits: int = 3, confirm_s: float = 0.3,
                 drop_s: float = 1.0, show_s: float = 0.5, trail_s: float = 2.5) -> None:
        self.gate, self.confirm_hits, self.confirm_s = gate, confirm_hits, confirm_s
        self.drop_s, self.show_s, self.trail_s = drop_s, show_s, trail_s
        self.reset()

    def reset(self) -> None:
        self.tracks: list[dict] = []
        self._next = 1

    def _new_id(self) -> int:
        used = {t["id"] for t in self.tracks}
        for _ in range(99):
            i = self._next
            self._next = self._next % 99 + 1
            if i not in used:
                return i
        return self._next

    def update(self, dets: list[dict], now: float) -> list[dict]:
        pairs = sorted(
            (math.hypot(d["x"] - t["x"], d["y"] - t["y"]), ti, di)
            for ti, t in enumerate(self.tracks) for di, d in enumerate(dets))
        used_t: set[int] = set()
        used_d: set[int] = set()
        for dist, ti, di in pairs:
            if dist > self.gate or ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            t, d = self.tracks[ti], dets[di]
            dt = max(1e-3, now - t["t"])
            t["vx"] += 0.3 * ((d["x"] - t["x"]) / dt - t["vx"])
            t["vy"] += 0.3 * ((d["y"] - t["y"]) / dt - t["vy"])
            t["x"] += 0.6 * (d["x"] - t["x"])
            t["y"] += 0.6 * (d["y"] - t["y"])
            t["w"] += 0.3 * (d["w"] - t["w"])
            t["t"] = now
            t["hits"] += 1
            if not t["trail"] or now - t["trail"][-1][0] >= 0.12:
                t["trail"].append((now, t["x"], t["y"]))
        for di, d in enumerate(dets):
            if di not in used_d:
                self.tracks.append({"id": self._new_id(), "x": d["x"], "y": d["y"], "w": d["w"],
                                    "vx": 0.0, "vy": 0.0, "t": now, "born": now, "hits": 1,
                                    "trail": deque([(now, d["x"], d["y"])], maxlen=40),
                                    "fused_until": 0.0, "cam_conf": None})
        self.tracks = [t for t in self.tracks if now - t["t"] < self.drop_s]
        for t in self.tracks:
            while t["trail"] and now - t["trail"][0][0] > self.trail_s:
                t["trail"].popleft()
        return self.visible(now)

    def visible(self, now: float) -> list[dict]:
        return [t for t in self.tracks
                if t["hits"] >= self.confirm_hits and now - t["born"] >= self.confirm_s
                and now - t["t"] <= self.show_s]

    def fuse(self, cam_people: list[tuple[float, float, float]], now: float, gate: float = 0.6) -> list[int]:
        """Dopasowuje osoby z kamery (X, Y, pewność) do obiektów; zwraca indeksy niedopasowanych."""
        cands = [t for t in self.visible(now) if math.hypot(t["x"], t["y"]) >= PERSON_MIN_D]
        pairs = sorted((math.hypot(t["x"] - X, t["y"] - Y), ti, ci)
                       for ci, (X, Y, _) in enumerate(cam_people) for ti, t in enumerate(cands))
        used_t: set[int] = set()
        used_c: set[int] = set()
        for dist, ti, ci in pairs:
            if dist > gate or ti in used_t or ci in used_c:
                continue
            used_t.add(ti)
            used_c.add(ci)
            cands[ti]["fused_until"] = now + 0.6
            cands[ti]["cam_conf"] = cam_people[ci][2]
        return [ci for ci in range(len(cam_people)) if ci not in used_c]

    def export(self, now: float, max_range: float = 3.0) -> tuple[list[dict], int]:
        out, count = [], 0
        for t in self.visible(now):
            d = math.hypot(t["x"], t["y"])
            fused = now < t["fused_until"] and d >= PERSON_MIN_D
            # kamera potwierdza osobę także wtedy, gdy z daleka radar widzi tylko kilka punktów
            person = d >= PERSON_MIN_D and (t["w"] >= PERSON_MIN_W or fused)
            if person and d <= max_range:
                count += 1
            out.append({"id": t["id"], "x": round(t["x"], 3), "y": round(t["y"], 3), "d": round(d, 2),
                        "w": round(t["w"], 2), "kind": "person" if person else "small", "fused": fused,
                        "trail": [[round(x, 2), round(y, 2)] for _, x, y in t["trail"]]})
        return out, count
