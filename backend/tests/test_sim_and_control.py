"""Symulacja (spójność kamery i LiDAR-u) oraz zasady dopuszczenia ruchu kół."""
import dataclasses
import time

import numpy as np

from app.config import Settings
from app.control import ControlLoop
from app.hub import Hub
from app.logic.shield import Shield
from app.sim.world import SCRIPT, SimWorld


def at_phase(world: SimWorld, name: str, extra: float = 2.0) -> float:
    """Przewija świat (od jego startu) do danej fazy scenariusza i zwraca bieżący czas."""
    rel = 0.0
    for p in SCRIPT:
        if p.name == name:
            rel += min(p.dur * 0.8, extra)
            break
        rel += p.dur
    if world.t0 is None:
        world.tick(100.0)
    now = world._last
    while now - world.t0 < rel:
        now += 0.05
        world.tick(now)
    return now


def test_open_hand_visible_with_landmarks_in_zone():
    w = SimWorld()
    at_phase(w, "drive_near", extra=3.0)
    img, hand, people = w.render(640, 360)
    assert img.shape == (360, 640)
    assert hand is not None and hand["gesture"] == "FIVE"
    assert 0.35 <= hand["dist_m"] <= 0.7
    assert len(hand["landmarks"]) == 21
    # kierowca jest też "wykryty jako osoba"
    assert any(0.6 < p["dist_m"] < 1.4 and p["conf"] > 0.5 for p in people)
    assert all(0 <= v <= 1 for p in people for v in p["bbox"])


def test_fist_phase():
    w = SimWorld()
    at_phase(w, "fist", extra=2.5)
    _, hand, _ = w.render(640, 360)
    assert hand is not None and hand["gesture"] == "FIST"


def test_poke_enters_shield_and_hand_stays_out():
    w = SimWorld()
    sh = Shield(learn_s=0.5)
    # tło uczone na początku scenariusza (kierowca jeszcze daleko)
    t = 100.0
    for _ in range(10):
        w.tick(t)
        sh.update(w.lidar_scan(), t)
        t += 0.1
    assert sh.ready
    tp = at_phase(w, "poke", extra=3.0)
    st = None
    for k in range(4):
        st = sh.update(w.lidar_scan(), tp + 0.1 * k)
    assert st.active, "ręka w tarczy nie zatrzymała kół"
    _, hand, _ = w.render(640, 360)
    assert hand is not None and hand["gesture"] == "FIVE"   # kamera nadal "chce jechać" -> fuzja


def test_sim_fusion_marks_driver_as_person():
    """Kamera i LiDAR w symulacji widzą tę samą osobę -> obiekt "potwierdzony przez 2 czujniki"."""
    s = dataclasses.replace(Settings(), shield_learn_s=0.5)
    hub = Hub(s)
    w = SimWorld(s.cam_offset_x, s.cam_offset_y, s.sim_camera_height_m, s.oak_hfov_deg)
    t = 100.0
    for _ in range(8):                                     # tło: kierowca jeszcze daleko
        w.tick(t)
        hub.on_scan(w.lidar_scan(), t, "sim")
        t += 0.1
    tp = at_phase(w, "drive_far", extra=2.5)
    for _ in range(8):
        _, _, people = w.render(640, 360)
        hub.on_people(people, tp, "sim")
        hub.on_scan(w.lidar_scan(), tp, "sim")
        tp += 0.1
    fused = [tr for tr in hub.scan["tracks"] if tr["fused"]]
    assert fused, hub.scan["tracks"]
    assert any(0.9 < tr["d"] < 1.6 for tr in fused)
    assert hub.scan["count"] >= 1


def test_lidar_scan_shape_and_background():
    w = SimWorld()
    w.tick(1.0)
    r = w.lidar_scan()
    assert r.shape == (360,)
    assert np.count_nonzero(r) > 250                        # ściany stoiska wokół


# --------------------------------------------------------------------------- zasady ruchu
def make(**kw):
    s = dataclasses.replace(Settings(), **{"shield_learn_s": 0.2, "attract": False, **kw})
    hub = Hub(s)
    return hub, ControlLoop(hub, s), s


def feed_lidar(hub, source, n=5):
    now = time.monotonic()
    for k in range(n):
        hub.on_scan(np.full(360, 2.0), now - 0.05 * (n - k), source)


def open_hand(hub, dist=0.5):
    now = time.monotonic()
    hub.on_hand({"present": True, "gesture": "FIVE", "dist_m": dist, "x_norm": 0.5}, now, "oak")


def test_real_esp32_with_sim_camera_never_moves():
    hub, ctl, _ = make()
    hub.set_device("esp32", "real", "/dev/ttyAMA0")
    open_hand(hub)
    out = ctl.step(time.monotonic())
    assert hub.drive_cmd["allowed"] is False and out.cmd_l == 0
    assert hub.drive["lock"] == "no_camera"


def test_real_camera_requires_fresh_real_lidar():
    hub, ctl, s = make()
    for k in ("oak", "lidar", "esp32"):
        hub.set_device(k, "real")
    feed_lidar(hub, "lidar")
    open_hand(hub)
    now = time.monotonic()
    for k in range(10):
        open_hand(hub)
        feed_lidar(hub, "lidar", 1)
        out = ctl.step(now + 0.02 * k)
    assert hub.drive["lock"] == ""
    assert out.cmd_l > 0
    # LiDAR milknie > LIDAR_STALE_S -> natychmiastowe zero
    later = time.monotonic() + s.lidar_stale_s + 0.2
    open_hand(hub)
    hub.cam_t = later
    hub.hand["t"] = later
    out = ctl.step(later)
    assert hub.drive["lock"] == "lidar_stale"
    assert out.cmd_l == 0.0 and hub.drive_cmd["allowed"] is False


def test_no_lidar_blocks_unless_opted_in():
    hub, ctl, _ = make()
    hub.set_device("oak", "real")
    hub.set_device("esp32", "real")
    open_hand(hub)
    ctl.step(time.monotonic())
    assert hub.drive["lock"] == "no_lidar"
    hub, ctl, _ = make(drive_without_lidar=True)
    hub.set_device("oak", "real")
    hub.set_device("esp32", "real")
    open_hand(hub)
    ctl.step(time.monotonic())
    assert hub.drive["lock"] == ""


def test_texts_for_visitors():
    hub, ctl, _ = make()
    hub.sim_active = True
    feed_lidar(hub, "sim", 10)
    hub.on_hand({"present": True, "gesture": "FIVE", "dist_m": 0.5, "x_norm": 0.5}, time.monotonic(), "sim")
    ctl.step(time.monotonic())
    d = hub.drive
    assert d["label"] == "JAZDA"
    assert d["decision"]["camera"]["text"].startswith("JEDŹ")
    assert d["decision"]["lidar"]["text"] == "CZYSTO"
    assert d["decision"]["decision"]["text"] == "JEDŹ"
    assert "bliżej" in d["header"]["text"]
