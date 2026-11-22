"""Syntetyczna dłoń: 21 punktów w układzie MediaPipe Hands + bryły do renderu.

Indeksy: 0 nadgarstek; 1-4 kciuk; 5-8 wskazujący; 9-12 środkowy; 13-16 serdeczny;
17-20 mały (kolejno: podstawa, staw środkowy, staw końcowy, czubek).
Te same punkty służą do (1) narysowania dłoni na symulowanym obrazie i (2) jako
"wynik sieci" – szkielet na ekranie idealnie pokrywa się z dłonią na obrazie.
"""
from __future__ import annotations

import math

import numpy as np

SKIN = 0.92                          # skóra jest jasna w bliskiej podczerwieni

# (przesunięcie w stronę kciuka, wysokość podstawy palca, rozwarcie [°], długości paliczków)
FINGERS = (
    (0.025, 0.085, 7.0, (0.039, 0.023, 0.020)),     # wskazujący
    (0.005, 0.090, 0.0, (0.044, 0.027, 0.021)),     # środkowy
    (-0.014, 0.084, -7.0, (0.040, 0.026, 0.020)),   # serdeczny
    (-0.031, 0.074, -16.0, (0.031, 0.019, 0.018)),  # mały
)
BONES = ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
         (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20))


def _u(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-9 else v


POSES = {                                   # cztery palce: wyprostowany? (wskazujący..mały)
    "open": (True, True, True, True),
    "fist": (False, False, False, False),
    "scissors": (True, True, False, False),
    "thumb": (False, False, False, False),  # kciuk w górę
}


def landmarks(palm_center: np.ndarray, toward_cam: np.ndarray, pose: str | bool = "open",
              tilt: float = 0.0) -> np.ndarray:
    """21 punktów 3D [m] prawej dłoni zwróconej wnętrzem do kamery, palce do góry.

    toward_cam – kierunek, w który patrzy wnętrze dłoni (poziomo, do kamery).
    pose – open | fist | scissors | thumb (True/False = open/fist).
    """
    if isinstance(pose, bool):
        pose = "open" if pose else "fist"
    fingers_open = POSES[pose]
    up = np.array([0.0, 0.0, 1.0])
    F = _u(np.array([toward_cam[0], toward_cam[1], 0.0]))
    U = _u(up + F * tilt)                       # lekkie pochylenie palców
    T = _u(np.cross(U, F))                      # w stronę kciuka (prawa dłoń)
    wrist = palm_center - U * 0.045
    pts = [wrist]

    # kciuk
    cmc = wrist + U * 0.022 + T * 0.022 + F * 0.008
    if pose == "open":
        dirs = (_u(U * 0.45 + T * 0.85), _u(U * 0.6 + T * 0.75), _u(U * 0.75 + T * 0.6))
    elif pose == "thumb":
        cmc = wrist + U * 0.045 + T * 0.03 + F * 0.012
        dirs = (_u(U * 0.75 + T * 0.4), _u(U * 0.95 + T * 0.1), _u(U))
    else:
        dirs = (_u(U * 0.5 + T * 0.5 + F * 0.35), _u(-T * 0.6 + U * 0.3 + F * 0.6), _u(-T * 0.95 + F * 0.15))
    p = cmc
    pts.append(p)
    for d, ln in zip(dirs, (0.034, 0.030, 0.025)):
        p = p + d * ln
        pts.append(p)

    # cztery palce
    for k, ((side, base_h, spread, lens), straight) in enumerate(zip(FINGERS, fingers_open)):
        mcp = wrist + U * base_h + T * side
        if pose == "scissors" and k < 2:
            spread = (15.0, -7.0)[k]                    # nożyce: wskazujący i środkowy w "V"
        a = math.radians(spread)
        if straight:
            d0 = _u(U * math.cos(a) + T * math.sin(a))
            dirs = (d0, d0, d0)
            lens = lens
        else:
            dirs = (_u(F * 0.8 + U * 0.25), _u(-U * 0.85 + F * 0.25), _u(-U * 0.3 - F * 0.75))
            lens = (lens[0] * 0.95, lens[1] * 0.9, lens[2] * 0.85)
        p = mcp
        pts.append(p)
        for d, ln in zip(dirs, lens):
            p = p + d * ln
            pts.append(p)
    return np.array(pts)


def capsules(lm: np.ndarray) -> list[tuple]:
    """Bryły (p0, p1, promień, albedo) obrysowujące dłoń zbudowaną na punktach lm."""
    caps: list[tuple] = []
    w = lm[0]
    # śródręcze: wachlarz od nadgarstka do podstaw palców + łączenia podstaw
    for k, r in ((5, 0.014), (9, 0.016), (13, 0.016), (17, 0.013)):
        caps.append((w, lm[k], r, SKIN))
    for a, b in ((5, 9), (9, 13), (13, 17)):
        caps.append((lm[a], lm[b], 0.012, SKIN))
    caps.append((w, (lm[9] + lm[13]) / 2, 0.022, SKIN))
    caps.append((lm[1], lm[5], 0.013, SKIN))
    # palce
    radii = {1: 0.012, 2: 0.0105, 3: 0.0095}
    for base in (1, 5, 9, 13, 17):
        for j in range(3):
            r = radii[j + 1] * (1.0 if base == 1 else 0.82)
            caps.append((lm[base + j], lm[base + j + 1], r, SKIN))
    return caps
