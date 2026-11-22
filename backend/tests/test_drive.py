"""Maszyna stanów napędu: rampa, watchdog, priorytet stopu awaryjnego, gesty."""
import pytest

from app.logic.drive import DRIVE, ESTOP, IDLE, REVERSE, DriveInputs, DriveLogic, DriveParams, HandObs, ramp

DT = 0.05


def logic(**kw):
    p = DriveParams(attract=False, **kw)
    return DriveLogic(p), p


def hand(t, gesture="FIVE", dist=0.6, x=0.5):
    return HandObs(t=t, present=True, gesture=gesture, dist_m=dist, x_norm=x)


def run(lg, seconds, t0=0.0, gesture="FIVE", dist=0.6, x=0.5, shield=False, ok=True, hand_ok=True):
    t, out = t0, None
    n = int(round(seconds / DT))
    for _ in range(n):
        t += DT
        h = hand(t, gesture, dist, x) if hand_ok else HandObs(t=t, present=False)
        out = lg.step(DriveInputs(now=t, hand=h, shield_active=shield, actuation_ok=ok))
    return t, out


def test_ramp_function():
    assert ramp(0.0, 0.5, 1.0, 2.0, 0.1) == pytest.approx(0.1)
    assert ramp(0.5, 0.0, 1.0, 2.0, 0.1) == pytest.approx(0.3)
    assert ramp(0.1, -0.5, 1.0, 2.0, 0.1) == pytest.approx(0.0)     # zmiana kierunku: najpierw do zera
    assert ramp(0.0, -0.5, 1.0, 2.0, 0.1) == pytest.approx(-0.1)


def test_soft_start_protects_motors():
    lg, p = logic()
    # dłoń przy samym końcu strefy blisko = pełny gaz, ale silniki ruszają spokojnie
    t, out = run(lg, 0.3, dist=p.hand_near_m)
    assert out.state == DRIVE
    assert 0.0 < out.cmd_l < p.max_speed * 0.25
    prev, jumps = out.cmd_l, []
    for _ in range(50):
        t, out = run(lg, DT, t0=t, dist=p.hand_near_m)
        jumps.append(out.cmd_l - prev)
        prev = out.cmd_l
    assert max(jumps) <= p.max_speed / p.ramp_up_s * DT + 1e-9          # nigdy szybciej niż rampa
    assert out.cmd_l == pytest.approx(p.max_speed, abs=1e-6)            # po ~2 s pełna prędkość


def test_gentle_stop_when_hand_lost():
    lg, p = logic()
    t, out = run(lg, 2.5, dist=p.hand_near_m)
    assert out.cmd_l == pytest.approx(p.max_speed)
    t, out = run(lg, 0.30, t0=t, hand_ok=False)                        # dłoń znika
    assert out.cmd_l > 0.3 * p.max_speed                               # bez gwałtownego hamowania
    t, out = run(lg, 1.0, t0=t, hand_ok=False)
    assert out.cmd_l == 0.0 and out.state == IDLE


def test_reverse_waits_at_zero_before_changing_direction():
    lg, p = logic()
    t, out = run(lg, 2.0, dist=p.hand_near_m)
    assert out.cmd_l > 0.4
    zero_t = None
    for _ in range(80):
        t, out = run(lg, DT, t0=t, gesture="FIST", dist=p.hand_near_m)
        if out.cmd_l == 0.0 and zero_t is None:
            zero_t = t
        if out.cmd_l < 0:
            break
    assert zero_t is not None and out.cmd_l < 0
    assert t - zero_t >= p.reverse_pause_s - 1e-6                      # postój przed jazdą do tyłu


def test_watchdog_stale_perception():
    lg, p = logic()
    t, out = run(lg, 1.0)
    assert out.cmd_l > 0
    stale = hand(t)                                                    # kamera przestaje nadawać
    for k in range(12):
        t += DT
        out = lg.step(DriveInputs(now=t, hand=stale))
    assert out.sub == "stale"
    assert out.cmd_l == 0.0 and out.cmd_r == 0.0


def test_estop_has_absolute_priority_and_no_ramp():
    lg, p = logic()
    t, out = run(lg, 2.5, dist=p.hand_near_m)
    assert out.cmd_l == pytest.approx(p.max_speed)
    t += DT
    out = lg.step(DriveInputs(now=t, hand=hand(t, dist=p.hand_near_m), shield_active=True))
    assert out.state == ESTOP and out.estop_edge
    assert out.cmd_l == 0.0 and out.cmd_r == 0.0                        # natychmiast, bez rampy
    assert out.request == "drive"                                      # kamera "chciała" jechać
    t += DT
    out = lg.step(DriveInputs(now=t, hand=hand(t), shield_active=True))
    assert not out.estop_edge
    # po zwolnieniu tarczy rusza od zera rampą
    t, out = run(lg, 0.1, t0=t)
    assert 0.0 <= out.cmd_l < 0.05


def test_throttle_grows_when_hand_gets_closer():
    lg, p = logic()
    assert lg.throttle_for(p.hand_far_m) == pytest.approx(p.min_throttle)
    assert lg.throttle_for(p.hand_near_m) == pytest.approx(1.0)
    assert lg.throttle_for(0.6) > lg.throttle_for(0.9)


def test_out_of_zone_and_other_gesture_stop():
    lg, p = logic()
    _, out = run(lg, 0.5, dist=0.2)
    assert out.sub == "too_close" and out.cmd_l == 0.0
    lg, p = logic()
    _, out = run(lg, 0.5, dist=1.6)
    assert out.sub == "too_far" and out.cmd_l == 0.0
    lg, p = logic()
    _, out = run(lg, 0.5, gesture="PEACE")
    assert out.sub == "other_gesture" and out.cmd_l == 0.0


def test_fist_reverses():
    lg, p = logic()
    _, out = run(lg, 1.5, gesture="FIST")
    assert out.state == REVERSE
    assert out.cmd_l < 0 and out.cmd_r < 0
    lg, p = logic(enable_reverse=False)
    _, out = run(lg, 1.0, gesture="FIST")
    assert out.state == IDLE and out.cmd_l == 0.0


def test_always_straight_regardless_of_hand_position():
    # bez skrętów: dłoń z lewej, na środku i z prawej = obie strony z tą samą prędkością
    for x in (0.05, 0.5, 0.95):
        lg, p = logic()
        _, out = run(lg, 1.5, x=x)
        assert out.state == DRIVE and out.cmd_l > 0 and out.cmd_l == out.cmd_r
        lg, p = logic()
        _, out = run(lg, 1.5, x=x, gesture="FIST")
        assert out.state == REVERSE and out.cmd_l < 0 and out.cmd_l == out.cmd_r


def test_actuation_lock_forces_zero_immediately():
    lg, p = logic()
    t, out = run(lg, 1.0)
    assert out.cmd_l > 0
    t += DT
    out = lg.step(DriveInputs(now=t, hand=hand(t), actuation_ok=False))
    assert out.cmd_l == 0.0 and out.cmd_r == 0.0


def test_attract_wiggle_only_when_idle_and_allowed():
    lg = DriveLogic(DriveParams(attract=True, attract_interval_s=2.0, attract_duration_s=1.0))
    t, seen = 0.0, []
    for _ in range(80):
        t += DT
        out = lg.step(DriveInputs(now=t, hand=HandObs(t=t, present=False)))
        seen.append((out.sub, out.target_l, out.target_r))
    wig = [s for s in seen if s[0] == "attract"]
    assert wig, "brak przywołania kołami"
    assert any(l > 0 > r for _, l, r in wig) and any(l < 0 < r for _, l, r in wig)
    # zablokowany napęd -> żadnego ruchu
    lg = DriveLogic(DriveParams(attract=True, attract_interval_s=2.0))
    t = 0.0
    for _ in range(80):
        t += DT
        out = lg.step(DriveInputs(now=t, hand=HandObs(t=t, present=False), actuation_ok=False))
        assert out.cmd_l == 0.0 and out.sub != "attract"
