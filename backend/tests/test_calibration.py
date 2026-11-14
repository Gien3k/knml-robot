"""Kreator kalibracji radaru: obliczenia kąta i kierunku, hasło, blokada kół."""
import time

from app.calibration import solve, wrap
from tests.test_api import client  # noqa: F401  (wspólny serwer testowy)


def make_samples(true_offset, true_cw, cur_offset, cur_cw, cams):
    """Para (kierunek z kamery, kierunek z LiDAR-u przy obecnym, błędnym ustawieniu)."""
    s_t = -1 if true_cw else 1
    s_c = -1 if cur_cw else 1
    out = []
    for bc in cams:
        raw = s_t * (bc - true_offset)              # surowy kąt czujnika dla tej osoby
        out.append((bc, wrap(s_c * raw + cur_offset)))
    return out


def test_solve_rotated_lidar():
    cams = [0, 2, -3, 5, -8, 10, 12, -12, 15, -15]
    res = solve(make_samples(-88, True, 0, True, cams), 0, True)
    assert abs(wrap(res["offset_deg"] - (-88))) < 0.5
    assert res["clockwise"] is True and res["flipped"] is False
    assert res["residual_deg"] < 1


def test_solve_mirrored_lidar():
    cams = [0, 4, -4, 8, -8, 12, -12, 16, -16]
    res = solve(make_samples(30, False, 0, True, cams), 0, True)
    assert res["flipped"] is True and res["clockwise"] is False
    assert abs(wrap(res["offset_deg"] - 30)) < 0.5


def test_password_and_wheel_lock(client):  # noqa: F811
    r = client.post("/api/calibration/start", json={"password": "zle"})
    assert r.status_code == 403
    r = client.post("/api/calibration/start", json={"password": "knml"})   # wielkość liter bez znaczenia
    assert r.status_code == 200
    st = client.get("/api/calibration").json()
    assert st["active"] is True and st["index"] == 1 and 0 <= st["progress"] <= 20
    time.sleep(0.4)
    assert client.get("/api/state").json()["drive"]["lock"] == "calibration"    # koła zablokowane
    client.post("/api/calibration/cancel")
    assert client.get("/api/calibration").json()["active"] is False
    time.sleep(0.4)
    assert client.get("/api/state").json()["drive"]["lock"] != "calibration"
