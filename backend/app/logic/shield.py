"""Tarcza LiDAR: hamowanie awaryjne, gdy coś wtargnie blisko robota.

* Nauka tła: przez pierwsze SHIELD_LEARN_S sekund zbieramy skany; tło = mediana
  odległości w każdym z 360 koszyków kątowych (brak echa = nieskończoność).
  Dzięki temu monitor, ścianki stoiska i elementy samego robota nigdy nie wyzwalają alarmu.
* Wtargnięcie = co najmniej 2 punkty bliżej niż SHIELD_RADIUS od środka LiDAR-u
  ORAZ co najmniej SHIELD_MARGIN bliżej niż tło, w 2 kolejnych obrotach.
* Zwolnienie: 0,5 s bez wtargnięcia, liczonego z promieniem powiększonym o histerezę.
* Dopóki tło nie jest nauczone, tarcza nie jest gotowa – napęd czeka.

Stoisko żyje (ludzie, rzeczy na stole), więc tło nadąża za zmianami:
* przedmiot postawiony przy robocie i leżący bez ruchu ABSORB_S sekund (gdy koła stoją)
  staje się częścią tła – kubek czy torba nie blokują jazdy na zawsze,
* gdy coś z tła zniknie (zabrany przedmiot), tło "oddala się" po FORGET_S sekundach,
* robot przesunięty / obrócony (dużo punktów naraz bliżej I dalej niż tło) -> nauka tła od nowa
  (koła stoją, dopóki tło nie jest gotowe).
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
    moved: bool = False                   # w tym skanie wykryto przesunięcie robota (tło od nowa)
    absorbed: int = 0                     # ile koszyków właśnie wchłonięto do tła

    def to_dict(self) -> dict:
        return {
            "ready": self.ready, "learn": round(self.learn_progress, 2), "active": self.active,
            "nearest_m": None if self.nearest_m is None else round(self.nearest_m, 3),
            "nearest_deg": self.nearest_deg, "intrusion": self.intrusion_bins,
            "radius_m": self.radius_m,
        }


class Shield:
    def __init__(self, radius_m: float = 0.30, margin_m: float = 0.10, hysteresis_m: float = 0.10,
                 release_s: float = 0.5, learn_s: float = 5.0, confirm_scans: int = 2,
                 absorb_s: float = 45.0, forget_s: float = 5.0) -> None:
        self.absorb_s = absorb_s          # 0 = bez wchłaniania nieruchomych przedmiotów
        self.forget_s = forget_s
        self.tol = 0.04                   # [m] "nieruchomy" = odległość stała z tą dokładnością
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
        self._ref = self._t_near = self._t_far = None
        self._moved_streak = 0
        self.state = ShieldState(radius_m=self.radius)

    @property
    def ready(self) -> bool:
        return self.bg is not None

    def _adapt(self, r: np.ndarray, valid: np.ndarray, fg: np.ndarray, now: float, absorb_ok: bool) -> int:
        """Tło nadąża za stoiskiem: wchłania nieruchome przedmioty, zapomina zabrane."""
        if self._ref is None:
            self._ref = np.full(N_BINS, np.nan)
            self._t_near = np.full(N_BINS, np.nan)
            self._t_far = np.full(N_BINS, np.nan)
        with np.errstate(invalid="ignore"):
            # bliżej niż tło i nieruchomo
            same = fg & (np.abs(r - self._ref) < self.tol)
            new = fg & ~same
            self._ref[new] = r[new]
            self._t_near[new] = now
            self._ref[~fg] = np.nan
            self._t_near[~fg] = np.nan
            absorbed = 0
            if self.absorb_s > 0 and absorb_ok:
                take = fg & (now - self._t_near >= self.absorb_s)
                absorbed = int(np.count_nonzero(take))
                if absorbed:
                    self.bg[take] = r[take]
                    self._t_near[take] = np.nan
            # dalej niż tło (coś zabrano) -> tło się oddala
            far = valid & np.isfinite(self.bg) & (r > self.bg + self.margin)
            start = far & np.isnan(self._t_far)
            self._t_far[start] = now
            self._t_far[~far] = np.nan
            forget = far & (now - self._t_far >= self.forget_s)
            if forget.any():
                self.bg[forget] = r[forget]
                self._t_far[forget] = np.nan
        return absorbed

    def _moved(self, r: np.ndarray, valid: np.ndarray) -> bool:
        """Robot przesunięty / obrócony: naraz dużo punktów bliżej I dalej niż tło (ludzie dają tylko "bliżej")."""
        ref = valid & np.isfinite(self.bg) & (self.bg < 4.0)
        n = int(np.count_nonzero(ref))
        if n < 60:
            self._moved_streak = 0
            return False
        closer = np.count_nonzero(ref & (r < self.bg - 0.15)) / n
        farther = np.count_nonzero(ref & (r > self.bg + 0.15)) / n
        self._moved_streak = self._moved_streak + 1 if closer > 0.2 and farther > 0.2 else 0
        return self._moved_streak >= 8                                   # ~0,8 s przy 10 obr./s

    def update(self, ranges: np.ndarray, now: float, absorb_ok: bool = True) -> ShieldState:
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

        if self._moved(r, valid):
            self.reset()                                                 # nauka tła od nowa
            self.state = ShieldState(radius_m=self.radius, t=now, moved=True)
            return self.state

        fg = valid & (r < self.bg - self.margin)
        absorbed = self._adapt(r, valid, fg, now, absorb_ok)      # koła stoją (stop albo bezczynność)
        if absorbed:
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
            absorbed=absorbed,
        )
        return self.state
