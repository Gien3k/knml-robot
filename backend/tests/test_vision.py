"""„Jak widzi robot": kolorowanie głębi, podczerwień i wybór soczewki na ekranie."""
import numpy as np

from app.logic.vision import (BG_BGR, COLOR, DEPTH, IR, LensDirector, Scene, colorize_depth, ir_view,
                              palette_css, parse_scenes)


def test_colorize_depth_near_bright_far_dark_invalid_background():
    d = np.zeros((360, 640), np.uint16)
    d[:, :200] = 500            # 0,5 m
    d[:, 200:400] = 3000        # 3 m
    img = colorize_depth(d, out_w=960)
    assert img.shape == (540, 960, 3) and img.dtype == np.uint8
    near = img[270, 100].astype(int).sum()
    far = img[270, 450].astype(int).sum()
    assert near > far + 200                                   # bliżej = jaśniej
    assert tuple(int(v) for v in img[270, 900]) == BG_BGR     # brak pomiaru = tło ekranu


def test_colorize_float_meters_and_large_input():
    m = np.full((540, 960), np.inf, np.float32)
    m[100:200, 100:200] = 1.0
    img = colorize_depth(m)
    assert img.shape[1] <= 960
    big = np.full((1080, 1920), 1200, np.uint16)
    assert colorize_depth(big, out_w=960).shape == (540, 960, 3)


def test_ir_view_stretches_contrast():
    g = np.full((400, 640), 40, np.uint8)
    g[::20, ::20] = 120                                       # "kropki projektora" na ciemnym tle
    out = ir_view(g, out_w=960)
    assert out.shape == (600, 960) and out.dtype == np.uint8
    assert out.max() > 200                                    # kropki wyraźne mimo ciemnego obrazu


def test_palette_for_legend():
    p = palette_css()
    assert p[0][0] == 0.0 and p[-1][0] == 1.0
    assert all(c.startswith("#") and len(c) == 7 for _, c in p)


def test_parse_scenes():
    sc = parse_scenes("depth:5, game:3,bogus:4,ir:0,drive")
    assert [s.name for s in sc] == ["depth", "game", "drive"]
    assert sc[0].lens == DEPTH and sc[2].dur == 8.0
    assert parse_scenes("") and parse_scenes("nic:1")             # domyślny cykl


def test_ir_projector_only_for_depth_view():
    from app.hw.camera_worker import ir_level
    cfg = {"ir_dot": 0.3}
    assert ir_level(cfg, "color") == 0.0 and ir_level(cfg, "depth") == 0.3
    assert ir_level({**cfg, "ir_always": True}, "color") == 0.3


def test_lens_director_cycle_busy_and_manual():
    scenes = (Scene("drive", COLOR, 2.0), Scene("depth", DEPTH, 3.0), Scene("game", COLOR, 2.0), Scene("ir", IR, 1.0))
    L = LensDirector(scenes, idle_after_s=1.0, manual_hold_s=5.0)
    assert L.update(0.0, busy=True, vision_ok=True, game_ok=True)["lens"] == COLOR
    seq = [L.update(t, busy=False, vision_ok=True, game_ok=True)["scene"] for t in (0.5, 1.5, 3.5, 6.5, 8.5, 9.5)]
    assert seq == ["drive", "drive", "depth", "game", "ir", "drive"]
    # ktoś podchodzi z dłonią -> od razu kolor, a potem cykl od początku
    assert L.update(10.0, busy=True, vision_ok=True, game_ok=True)["scene"] == "live"
    assert L.update(10.5, busy=False, vision_ok=True, game_ok=True)["scene"] == "drive"
    # bez głębi i bez gry: tylko sceny kolorowe
    names = {L.update(11 + t * 0.25, busy=False, vision_ok=False, game_ok=False)["scene"] for t in range(80)}
    assert names == {"drive"}
    # wybór ręczny trzyma się przez całą grę / jazdę i jeszcze manual_hold_s po niej
    L.choose(IR, 30.0)
    r = L.update(31.0, busy=True, vision_ok=True, game_ok=True)
    assert r["lens"] == IR and r["auto"] is False and r["scene"] == "live"
    assert L.update(36.5, busy=True, vision_ok=True, game_ok=True)["lens"] == IR
    assert L.update(40.0, busy=False, vision_ok=True, game_ok=True)["lens"] == IR
    assert L.update(42.0, busy=False, vision_ok=True, game_ok=True)["lens"] == COLOR
    L.choose(DEPTH, 43.0)
    assert L.update(43.1, busy=False, vision_ok=False, game_ok=True)["lens"] == COLOR   # brak głębi
    L.choose("auto", 44.0)
    assert L.manual is None
