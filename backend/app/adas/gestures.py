"""Rozpoznawanie gestów dłoni z mapy głębi (OAK-D Pro, projektor IR).

Pipeline (działa identycznie na prawdziwej i symulowanej głębi):
 1. strefa interakcji: piksele w zakresie [zmin, zmax],
 2. najbliższy obiekt = wyciągnięta w stronę robota dłoń (1. percentyl głębi),
 3. "plaster" głębi ~11 cm za najbliższym punktem -> maska dłoni,
 4. największa spójna składowa + filtr rozmiaru w METRACH (dzięki głębi
    działa tak samo z 40 cm i z 1,2 m),
 5. cechy kształtu: solidność (pole / pole otoczki wypukłej) i liczba
    wcięć między palcami (convexity defects),
 6. klasyfikacja: OTWARTA_DLON / PIESC + wygładzanie czasowe (głosowanie).

Pozycja dłoni w poziomie steruje skrętem (offset -1..1).
"""
from __future__ import annotations

import math
import time
from collections import Counter, deque

import cv2
import numpy as np

OPEN = "OPEN_PALM"
FIST = "FIST"
UNKNOWN = "UNKNOWN"

_K3 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))


def detect_hand(depth_mm: np.ndarray, fx: float, zmin_m: float, zmax_m: float,
                roi_bottom: float = 0.95, slab_mm: float = 110.0) -> dict | None:
    """Zwraca surową obserwację dłoni dla pojedynczej klatki albo None."""
    h, w = depth_mm.shape[:2]
    d = depth_mm[: int(h * roi_bottom)]
    zmin, zmax = zmin_m * 1000.0, zmax_m * 1000.0
    valid = (d > zmin) & (d < zmax)
    n_valid = int(np.count_nonzero(valid))
    if n_valid < 60:
        return None
    near = float(np.percentile(d[valid], 1.0))
    mask = ((d >= near - 25) & (d <= near + slab_mm)).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, _K3)
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        return None
    # składowe dotykające lewej/prawej krawędzi to zwykle przechodnie, nie wyciągnięta dłoń
    ok = [i for i in range(1, n)
          if stats[i, cv2.CC_STAT_LEFT] > 1 and stats[i, cv2.CC_STAT_LEFT] + stats[i, cv2.CC_STAT_WIDTH] < w - 1]
    if not ok:
        return None
    idx = max(ok, key=lambda i: stats[i, cv2.CC_STAT_AREA])
    area_px = int(stats[idx, cv2.CC_STAT_AREA])
    z_m = (near + slab_mm * 0.4) / 1000.0
    m_per_px = z_m / fx
    area_m2 = area_px * m_per_px * m_per_px
    if not (0.0025 <= area_m2 <= 0.06):
        return None

    comp = (labels == idx).astype(np.uint8) * 255
    contours, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    cnt = max(contours, key=cv2.contourArea)
    c_area = cv2.contourArea(cnt)
    hull = cv2.convexHull(cnt)
    hull_area = cv2.contourArea(hull)
    solidity = c_area / hull_area if hull_area > 0 else 1.0

    gaps = 0
    try:
        hull_idx = cv2.convexHull(cnt, returnPoints=False)
        if hull_idx is not None and len(hull_idx) > 3 and len(cnt) > 5:
            defects = cv2.convexityDefects(cnt, hull_idx)
            if defects is not None:
                min_depth_px = 0.014 / m_per_px
                for s, e, f, dep in defects[:, 0]:
                    if dep / 256.0 < min_depth_px:
                        continue
                    ps, pe, pf = cnt[s][0], cnt[e][0], cnt[f][0]
                    a = np.linalg.norm(ps - pf)
                    b = np.linalg.norm(pe - pf)
                    c = np.linalg.norm(ps - pe)
                    if a * b == 0:
                        continue
                    ang = math.degrees(math.acos(np.clip((a * a + b * b - c * c) / (2 * a * b), -1, 1)))
                    if ang < 95:
                        gaps += 1
    except cv2.error:
        pass

    if gaps >= 3 or (gaps >= 2 and solidity < 0.80):
        gesture = OPEN
    elif solidity >= 0.84 and gaps <= 1:
        gesture = FIST
    else:
        gesture = UNKNOWN

    x, y, bw, bh = (int(v) for v in stats[idx, :4])
    cx, cy = cents[idx]
    return {
        "gesture": gesture,
        "bbox": [x / w, y / h, (x + bw) / w, (y + bh) / h],
        "center": [float(cx / w), float(cy / h)],
        "z": round(z_m, 3),
        "area_cm2": round(area_m2 * 1e4, 1),
        "solidity": round(float(solidity), 3),
        "gaps": gaps,
    }


class GestureFilter:
    """Wygładzanie czasowe: głosowanie większościowe z ostatnich N obserwacji."""

    def __init__(self, window: int = 7, hold_s: float = 0.35) -> None:
        self.hist: deque[str] = deque(maxlen=window)
        self.last_obs: dict | None = None
        self.last_t = 0.0
        self.hold_s = hold_s
        self.offset = 0.0

    def update(self, obs: dict | None, now: float | None = None) -> dict | None:
        now = time.monotonic() if now is None else now
        if obs is None:
            if now - self.last_t > self.hold_s:
                self.hist.clear()
                self.last_obs = None
                return None
            return self._result(self.last_obs, now) if self.last_obs else None
        self.hist.append(obs["gesture"])
        self.last_obs, self.last_t = obs, now
        # offset steru z wygładzaniem (EMA)
        raw = (obs["center"][0] - 0.5) * 2.0
        self.offset += 0.45 * (raw - self.offset)
        return self._result(obs, now)

    def _result(self, obs: dict, now: float) -> dict:
        votes = Counter(self.hist)
        label, cnt = votes.most_common(1)[0] if votes else (UNKNOWN, 0)
        conf = cnt / max(1, len(self.hist))
        return {**obs, "gesture": label, "raw": obs["gesture"], "conf": round(conf, 2),
                "offset": round(float(np.clip(self.offset, -1, 1)), 3), "t": now}
