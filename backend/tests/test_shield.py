"""Tarcza LiDAR: nauka tła, 2 kolejne obroty, histereza i zwolnienie."""
import numpy as np

from app.logic.shield import Shield


def bg_scan():
    r = np.full(360, 2.0)
    r[80:100] = 0.25          # monitor 25 cm od LiDAR-u – część tła, NIE może hamować
    r[200:210] = 0.0          # brak echa
    return r


def learn(sh, t0=0.0):
    """Podaje tło aż do końca nauki; zwraca czas następnego skanu."""
    t = t0
    for _ in range(200):
        sh.update(bg_scan(), t)
        t += 0.1
        if sh.ready:
            return t
    raise AssertionError("tarcza nie nauczyła się tła")


def test_not_ready_while_learning():
    sh = Shield(learn_s=5.0)
    st = sh.update(bg_scan(), 0.0)
    assert not st.ready and not st.active
    st = sh.update(bg_scan(), 2.5)
    assert 0.4 < st.learn_progress < 0.6
    assert not sh.update(bg_scan(), 4.9).ready
    assert sh.update(bg_scan(), 5.0).ready


def test_background_object_inside_radius_never_triggers():
    sh = Shield()
    t = learn(sh)
    for _ in range(10):
        st = sh.update(bg_scan(), t)
        t += 0.1
        assert not st.active


def intrusion(dist=0.2, bins=range(0, 6)):
    r = bg_scan()
    r[list(bins)] = dist
    return r


def test_requires_two_consecutive_rotations():
    sh = Shield()
    t = learn(sh)
    assert not sh.update(intrusion(), t).active            # 1. obrót – jeszcze nie
    assert not sh.update(bg_scan(), t + 0.1).active        # przerwa zeruje licznik
    assert not sh.update(intrusion(), t + 0.2).active
    st = sh.update(intrusion(), t + 0.3)                    # 2. kolejny obrót – STOP
    assert st.active
    assert st.intrusion_bins == list(range(0, 6))
    assert abs(st.nearest_m - 0.2) < 1e-9


def test_single_point_noise_ignored():
    sh = Shield()
    t = learn(sh)
    for k in range(5):
        st = sh.update(intrusion(bins=[10]), t + 0.1 * k)
    assert not st.active


def test_release_after_half_second_with_hysteresis():
    sh = Shield(radius_m=0.30, hysteresis_m=0.10, release_s=0.5)
    t = learn(sh)
    sh.update(intrusion(), t)
    assert sh.update(intrusion(), t + 0.1).active
    # ręka cofnięta do 0,35 m: poza promieniem, ale w histerezie -> nadal STOP
    for k in range(10):
        assert sh.update(intrusion(dist=0.35), t + 0.2 + 0.1 * k).active
    # ręka całkiem zabrana: zwolnienie dopiero po 0,5 s
    t2 = t + 1.3
    assert sh.update(bg_scan(), t2).active
    assert sh.update(bg_scan(), t2 + 0.3).active
    assert not sh.update(bg_scan(), t2 + 0.65).active


def test_object_closer_than_background_but_outside_radius_is_reported_not_stopped():
    sh = Shield()
    t = learn(sh)
    for k in range(3):
        st = sh.update(intrusion(dist=0.6), t + 0.1 * k)
    assert not st.active
    assert abs(st.nearest_m - 0.6) < 1e-9


def test_reset_relearns():
    sh = Shield()
    learn(sh)
    sh.reset()
    assert not sh.ready
