"""Ludzie z LiDAR-u (grupowanie, śledzenie, fuzja z kamerą) i pomiar czasu reakcji hamulca."""
import dataclasses
import math

import numpy as np
import pytest

from app.config import Settings
from app.hub import Hub
from app.logic.people import PeopleTracker, clusters


def blob(r, center_deg, dist, width_m):
    """Wstawia do skanu obiekt o szerokości width_m w odległości dist (kierunek center_deg)."""
    half = math.degrees(math.atan2(width_m / 2, dist))
    for a in range(int(round(center_deg - half)), int(round(center_deg + half)) + 1):
        r[a % 360] = dist
    return r


def fg_of(r, bg=3.5):
    return [i for i in range(360) if 0 < r[i] < bg - 0.1]


def test_clusters_person_hand_and_wraparound():
    r = np.full(360, 3.5)
    blob(r, 0, 1.2, 0.40)            # osoba na wprost – przez 359°/0°
    blob(r, 60, 0.5, 0.07)           # dłoń z boku
    cl = clusters(r, fg_of(r))
    assert len(cl) == 2
    person = max(cl, key=lambda c: c["w"])
    hand = min(cl, key=lambda c: c["w"])
    assert person["w"] == pytest.approx(0.40, abs=0.06)
    assert person["x"] == pytest.approx(1.2, abs=0.05) and abs(person["y"]) < 0.05
    assert hand["w"] < 0.15


def test_wall_like_objects_ignored():
    r = np.full(360, 3.5)
    blob(r, 90, 1.0, 1.6)            # szeroka przeszkoda (np. przestawiony roll-up)
    assert clusters(r, fg_of(r)) == []


def track_walk(tr, t0=0.0, steps=10, x0=1.6, x1=1.0, y=0.3):
    out = None
    for k in range(steps):
        x = x0 + (x1 - x0) * k / (steps - 1)
        out = tr.update([{"x": x, "y": y, "w": 0.4, "d": math.hypot(x, y), "n": 10}], t0 + 0.1 * k)
    return out


def test_tracker_keeps_id_velocity_and_trail():
    tr = PeopleTracker()
    assert tr.update([{"x": 1.6, "y": 0.3, "w": 0.4, "d": 1.6, "n": 10}], 0.0) == []    # jeszcze niepotwierdzony
    vis = track_walk(tr)
    assert len(vis) == 1
    t = vis[0]
    assert t["id"] == 1
    assert t["vx"] < -0.3                                   # idzie w stronę robota
    assert len(t["trail"]) >= 5
    exported, count = tr.export(1.0)
    assert count == 1 and exported[0]["kind"] == "person" and not exported[0]["fused"]


def test_tracker_drops_lost_objects():
    tr = PeopleTracker()
    track_walk(tr)
    assert tr.update([], 1.5) == []                       # niewidoczny > 0,5 s – nie pokazujemy
    tr.update([], 2.5)
    assert tr.tracks == []                                # po 1 s usunięty


def test_fusion_with_camera():
    tr = PeopleTracker()
    track_walk(tr, x1=1.2, y=0.0)
    unmatched = tr.fuse([(1.25, 0.05, 0.9), (2.6, -1.5, 0.8)], 1.0)
    assert unmatched == [1]                                # druga osoba z kamery nie ma pary w radarze
    out, _ = tr.export(1.0)
    assert out[0]["fused"] is True
    out, _ = tr.export(2.0)
    assert out == [] or out[0]["fused"] is False           # potwierdzenie wygasa


def test_hub_people_pipeline_with_camera():
    s = dataclasses.replace(Settings(), shield_learn_s=0.3)
    hub = Hub(s)
    bg = np.full(360, 3.5)
    t = 10.0
    for _ in range(5):
        hub.on_scan(bg.copy(), t, "lidar")
        t += 0.1
    # osoba 1,4 m przed robotem; kamera widzi ją na środku kadru w odległości 1,3 m od kamery
    for _ in range(6):
        hub.on_people([{"bbox": [0.4, 0.1, 0.6, 0.9], "conf": 0.9, "dist_m": 1.3}], t, "oak")
        hub.on_scan(blob(bg.copy(), 0, 1.4, 0.45), t, "lidar")
        t += 0.1
    sc = hub.scan
    assert sc["count"] == 1
    assert sc["tracks"][0]["fused"] is True
    assert sc["cam_only"] == []


# --------------------------------------------------------------------------- hamulec
def learned_hub(**kw):
    s = dataclasses.replace(Settings(), shield_learn_s=0.3, **kw)
    hub = Hub(s)
    t = 100.0
    for _ in range(5):
        hub.on_scan(np.full(360, 2.0), t, "sim")
        t += 0.1
    return hub, t


def poke(t):
    r = np.full(360, 2.0)
    r[0:8] = 0.2
    return r


def test_brake_reaction_time_in_simulation():
    hub, t = learned_hub()
    hub.on_wheels(0.3, 0.3, "sim", t)                    # koła się kręcą
    hub.on_scan(poke(t), t, "sim")                       # 1. skan z ręką
    assert hub.brake["last_s"] is None
    hub.on_scan(poke(t + 0.1), t + 0.1, "sim")           # 2. skan -> STOP
    assert hub.shield_state.active
    assert hub.brake["last_s"] == pytest.approx(0.1, abs=1e-6)
    assert hub.brake["record_s"] == pytest.approx(0.1, abs=1e-6)
    hub.on_wheels(0.1, 0.1, "sim", t + 0.2)
    assert hub.brake["wheels_s"] is None
    hub.on_wheels(0.005, -0.004, "sim", t + 0.35)        # koła stanęły
    assert hub.brake["wheels_s"] == pytest.approx(0.35, abs=1e-6)
    assert hub.brake["count"] == 1


def test_brake_waits_for_real_esp32_confirmation():
    hub, t = learned_hub()
    hub.set_device("esp32", "real")
    hub.on_scan(poke(t), t, "sim")
    hub.on_scan(poke(t + 0.1), t + 0.1, "sim")
    assert hub.brake["last_s"] is None                   # czekamy, aż ESP32 wyśle stop
    hub.on_stop_sent(t + 0.112)
    assert hub.brake["last_s"] == pytest.approx(0.112, abs=1e-6)
    hub.on_stop_sent(t + 0.5)                            # drugie potwierdzenie nic nie zmienia
    assert hub.brake["last_s"] == pytest.approx(0.112, abs=1e-6)


def test_brake_record_keeps_best():
    hub, t = learned_hub()
    for gap in (0.2, 0.1, 0.3):                           # odstęp między 1. a 2. skanem z ręką
        hub.on_scan(poke(t), t, "sim")
        t += gap
        hub.on_scan(poke(t), t, "sim")
        t += 1.0
        hub.on_scan(np.full(360, 2.0), t, "sim")         # ręka zabrana – zwolnienie tarczy
        t += 0.6
        hub.on_scan(np.full(360, 2.0), t, "sim")
        t += 0.1
    assert hub.brake["count"] == 3
    assert hub.brake["record_s"] == pytest.approx(0.1, abs=1e-6)
