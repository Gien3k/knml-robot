"""Tarcza LiDAR: hamowanie awaryjne, gdy coś wtargnie blisko robota.

* Nauka tła: przez pierwsze SHIELD_LEARN_S sekund zbieramy skany; tło = mediana
  odległości w każdym z 360 koszyków kątowych (brak echa = nieskończoność).
  Dzięki temu monitor, ścianki stoiska i elementy samego robota nigdy nie wyzwalają alarmu.
* Wtargnięcie = co najmniej 2 punkty bliżej niż SHIELD_RADIUS od środka LiDAR-u
  ORAZ co najmniej SHIELD_MARGIN bliżej niż tło, w 2 kolejnych obrotach.
* Zwolnienie: 0,5 s bez wtargnięcia, liczonego z promieniem powiększonym o histerezę.
* Dopóki tło nie jest nauczone, tarcza nie jest gotowa – napęd czeka.
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field

import numpy as np

N_BINS = 360
MIN_POINTS = 2


@dataclass
class ShieldState:
    ready: bool = False
    learn_progress: float = 0.0
    active: bool = False
    nearest_m: float | None = None        # najbliższy "nowy" obiekt (bliżej niż tło)
    nearest_deg: int | None = None
    intrusion_bins: list[int] = field(default_factory=list)
    fg_bins: list[int] = field(default_factory=list)
    radius_m: float = 0.30
    t: float = 0.0
    trigger_t: float | None = None        # pierwszy skan z wtargnięciem, które włączyło STOP

    def to_dict(self) -> dict:
        return {
            "ready": self.ready, "learn": round(self.learn_progress, 2), "active": self.active,
            "nearest_m": None if self.nearest_m is None else round(self.nearest_m, 3),
            "nearest_deg": self.nearest_deg, "intrusion": self.intrusion_bins,
            "radius_m": self.radius_m,
        }


class Shield:
    def __init__(self, radius_m: float = 0.30, margin_m: float = 0.10, hysteresis_m: float = 0.10,
                 release_s: float = 0.5, learn_s: float = 5.0, confirm_scans: int = 2) -> None:
        self.radius = radius_m
        self.margin = margin_m
        self.hyst = hysteresis_m
        self.release_s = release_s
        self.learn_s = learn_s
        self.confirm = confirm_scans
        self.reset()

    def reset(self) -> None:
        self._learn_t0: float | None = None
        self._learn: list[np.ndarray] = []
        self.bg: np.ndarray | None = None
        self._streak = 0
        self._active = False
        self._last_hit = -math.inf
        self._first_hit = 0.0
        self._trigger_t: float | None = None
        self.state = ShieldState(radius_m=self.radius)

    @property
    def ready(self) -> bool:
        return self.bg is not None

    def update(self, ranges: np.ndarray, now: float) -> ShieldState:
        r = np.asarray(ranges, dtype=np.float64)
        valid = r > 0

        if self.bg is None:
            if self._learn_t0 is None:
                self._learn_t0 = now
            self._learn.append(np.where(valid, r, np.nan))
            elapsed = now - self._learn_t0
            if elapsed >= self.learn_s and len(self._learn) >= 3:
                with np.errstate(all="ignore"), warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    bg = np.nanmedian(np.array(self._learn), axis=0)
                self.bg = np.where(np.isnan(bg), np.inf, bg)
                self._learn = []
            self.state = ShieldState(ready=self.bg is not None,
                                     learn_progress=min(1.0, elapsed / max(self.learn_s, 1e-6)),
                                     radius_m=self.radius, t=now)
            return self.state

        fg = valid & (r < self.bg - self.margin)
        radius = self.radius + (self.hyst if self._active else 0.0)
        hit = fg & (r < radius)
        n_hit = int(np.count_nonzero(hit))

        if n_hit >= MIN_POINTS:
            if self._streak == 0:
                self._first_hit = now
            self._streak += 1
            if self._streak >= self.confirm and not self._active:
                self._active = True
                self._trigger_t = self._first_hit
            if self._active:
                self._last_hit = now
        else:
            self._streak = 0
            if self._active and now - self._last_hit >= self.release_s:
                self._active = False

        nearest_m = nearest_deg = None
        if fg.any():
            idx = np.flatnonzero(fg)
            k = int(idx[np.argmin(r[idx])])
            nearest_m, nearest_deg = float(r[k]), k
        self.state = ShieldState(
            ready=True, learn_progress=1.0, active=self._active,
            nearest_m=nearest_m, nearest_deg=nearest_deg,
            intrusion_bins=[int(i) for i in np.flatnonzero(hit)],
            fg_bins=[int(i) for i in np.flatnonzero(fg)],
            radius_m=self.radius, t=now, trigger_t=self._trigger_t if self._active else None,
        )
        return self.state
