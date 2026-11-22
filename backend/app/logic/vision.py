"""„Jak widzi robot": obrazy z czujników głębi i podczerwieni dla odwiedzających.

1. colorize_depth – mapa głębi [mm] -> obraz BGR w kolorach stoiska:
   blisko jasno (biel stoiska), dalej niebiesko (kolor "AI"), daleko prawie czerń;
   brak pomiaru (cień stereo, za daleko) = tło ekranu. Bez poziomic: na zaszumionej
   głębi ze stereo migałyby z klatki na klatkę.
2. ir_view – obraz z kamery mono (podczerwień) z kropkami projektora: tylko lekkie
   rozciągnięcie kontrastu, żeby kropki były widoczne także w jasnej hali.
3. LensDirector – co pokazuje ekran: kolor / głębia / podczerwień.
   Ktoś steruje (dłoń, gra, jazda) -> zawsze obraz kolorowy z nałożonym szkieletem dłoni.
   Nikt nie steruje -> cykl "scen" (zaproszenie do jazdy, głębia, zaproszenie do gry, podczerwień),
   a niedostępne sceny (np. brak głębi w trybie zapasowym kamery) są pomijane.
   Obsługa może wybrać soczewkę ręcznie (dotyk / API) na manual_hold_s.

Moduł czysty: numpy + OpenCV, bez wątków; czas podawany jawnie.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

COLOR, DEPTH, IR = "color", "depth", "ir"
LENSES = (COLOR, DEPTH, IR)
LENS_CODE = {COLOR: 0, DEPTH: 1, IR: 2}          # bajt sterujący dla skryptu na VPU

NEAR_M, FAR_M = 0.3, 3.5                         # zakres kolorów (legenda na ekranie)
WORK_W = 640                                     # szerokość, w której liczymy kolory i poziomice
BG_BGR = (18, 17, 15)                            # --pit #0f1112 (BGR), tło bez pomiaru

# przystanki palety (odległość znormalizowana 0..1 -> RGB), zgodne z kolorami ekranu
_STOPS = (
    (0.00, (0xF8, 0xF6, 0xEF)),     # biel stoiska (--paint), lekko rozjaśniona
    (0.16, (0xC9, 0xE2, 0xFB)),
    (0.38, (0x6C, 0xB4, 0xFF)),     # --route
    (0.66, (0x2A, 0x5D, 0x93)),
    (1.00, (0x16, 0x23, 0x31)),     # granat na granicy tła
)


def _build_lut() -> np.ndarray:
    xs = np.linspace(0.0, 1.0, 256)
    out = np.zeros((256, 1, 3), np.uint8)
    for c in range(3):
        stops_x = [s[0] for s in _STOPS]
        stops_c = [s[1][c] for s in _STOPS]
        out[:, 0, 2 - c] = np.clip(np.interp(xs, stops_x, stops_c), 0, 255).astype(np.uint8)   # RGB -> BGR
    return out


LUT = _build_lut()


def palette_css() -> list[list]:
    """Te same przystanki dla legendy w przeglądarce: [[pozycja 0..1, '#rrggbb'], ...]."""
    return [[s[0], "#%02x%02x%02x" % s[1]] for s in _STOPS]


def colorize_depth(depth_mm: np.ndarray, out_w: int | None = None,
                   near_m: float = NEAR_M, far_m: float = FAR_M) -> np.ndarray:
    """Mapa głębi uint16 [mm] (0 = brak pomiaru) albo float [m] (inf/0 = brak) -> BGR uint8."""
    d = np.asarray(depth_mm)
    if d.shape[1] > WORK_W:                             # liczymy w mniejszej rozdzielczości (Raspberry)
        d = cv2.resize(d, (WORK_W, int(round(d.shape[0] * WORK_W / d.shape[1]))), interpolation=cv2.INTER_NEAREST)
    if d.dtype == np.uint16:
        d16 = cv2.medianBlur(d, 5)                      # wygładza szum stereo, zostawia krawędzie
        valid = d16 > 0
        m = d16.astype(np.float32) * 0.001
    else:
        m = d.astype(np.float32)
        valid = np.isfinite(m) & (m > 0)
        m = np.where(valid, m, 0.0).astype(np.float32)
    valid &= m < far_m * 1.6
    t = np.clip((m - near_m) / max(1e-6, far_m - near_m), 0.0, 1.0)
    t = t ** 0.8                                        # trochę więcej odcieni z bliska, gdzie stoi człowiek
    idx = (t * 255.0).astype(np.uint8)
    img = cv2.applyColorMap(idx, LUT)
    img[~valid] = BG_BGR
    if out_w and img.shape[1] != out_w:
        h = int(round(img.shape[0] * out_w / img.shape[1]))
        img = cv2.resize(img, (out_w, h), interpolation=cv2.INTER_AREA if out_w < img.shape[1] else cv2.INTER_LINEAR)
    return img


def ir_view(gray: np.ndarray, out_w: int | None = None) -> np.ndarray:
    """Obraz mono z kropkami projektora: rozciągnięcie 1..99,5 percentyla, bez kolorowania."""
    g = np.asarray(gray)
    if g.ndim == 3:
        g = cv2.cvtColor(g, cv2.COLOR_BGR2GRAY)
    lo, hi = np.percentile(g[::4, ::4], (1.0, 99.5))
    if hi - lo > 8:
        g = np.clip((g.astype(np.float32) - lo) * (235.0 / (hi - lo)) + 8, 0, 255).astype(np.uint8)
    if out_w and g.shape[1] != out_w:
        h = int(round(g.shape[0] * out_w / g.shape[1]))
        g = cv2.resize(g, (out_w, h), interpolation=cv2.INTER_LINEAR)
    return g


# =============================================================================== reżyser ekranu
@dataclass(frozen=True)
class Scene:
    name: str          # drive | depth | game | ir
    lens: str
    dur: float


DEFAULT_SCENES = (Scene("drive", COLOR, 9.0), Scene("depth", DEPTH, 12.0),
                  Scene("game", COLOR, 9.0), Scene("ir", IR, 8.0))
MANUAL_SCENE = {COLOR: "drive", DEPTH: "depth", IR: "ir"}


def parse_scenes(spec: str) -> tuple[Scene, ...]:
    """VISION_SCENES="drive:9,depth:12,game:9,ir:8" (nazwa:sekundy, kolejność = kolejność na ekranie)."""
    lens = {"drive": COLOR, "game": COLOR, "depth": DEPTH, "ir": IR}
    out = []
    for part in (spec or "").split(","):
        name, _, sec = part.strip().partition(":")
        name = name.strip().lower()
        if name not in lens:
            continue
        try:
            dur = float(sec.replace(",", ".")) if sec else 8.0
        except ValueError:
            continue
        if dur > 0:
            out.append(Scene(name, lens[name], dur))
    return tuple(out) or DEFAULT_SCENES


class LensDirector:
    def __init__(self, scenes: tuple[Scene, ...] = DEFAULT_SCENES, idle_after_s: float = 2.0,
                 manual_hold_s: float = 25.0, enabled: bool = True) -> None:
        self.scenes = scenes
        self.idle_after_s = idle_after_s
        self.manual_hold_s = manual_hold_s
        self.enabled = enabled
        self.idle_since: float | None = None
        self.manual: tuple[str, float] | None = None      # (soczewka, do kiedy)

    def choose(self, lens: str, now: float) -> None:
        """Wybór ręczny (dotyk na ekranie / API). "auto" = powrót do cyklu."""
        if lens in LENSES:
            self.manual = (lens, now + self.manual_hold_s)
        else:
            self.manual = None

    def update(self, now: float, busy: bool, vision_ok: bool, game_ok: bool) -> dict:
        if self.manual and busy:
            # wybrany widok zostaje przez całą grę / jazdę i jeszcze manual_hold_s po niej
            self.manual = (self.manual[0], max(self.manual[1], now + self.manual_hold_s))
        if self.manual and now >= self.manual[1]:
            self.manual = None
        if self.manual and not vision_ok and self.manual[0] != COLOR:
            self.manual = None
        if self.manual:
            lens = self.manual[0]
            return {"lens": lens, "scene": "live" if busy else MANUAL_SCENE[lens],
                    "auto": False, "progress": round(1.0 - (self.manual[1] - now) / self.manual_hold_s, 3)}
        if busy or not self.enabled:
            self.idle_since = None
            return {"lens": COLOR, "scene": "live" if busy else "drive", "auto": False, "progress": 0.0}
        if self.idle_since is None:
            self.idle_since = now
        scenes = [s for s in self.scenes
                  if (s.lens == COLOR or vision_ok) and (s.name != "game" or game_ok)]
        el = now - self.idle_since - self.idle_after_s
        if el < 0 or not scenes:
            return {"lens": COLOR, "scene": "drive", "auto": True, "progress": 0.0}
        total = sum(s.dur for s in scenes)
        el %= total
        for s in scenes:
            if el < s.dur:
                return {"lens": s.lens, "scene": s.name, "auto": True, "progress": round(el / s.dur, 3)}
            el -= s.dur
        s = scenes[-1]
        return {"lens": s.lens, "scene": s.name, "auto": True, "progress": 1.0}
