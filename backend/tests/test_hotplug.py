"""Hot-plug na działającym serwerze (HW_MODE=auto): wirtualny LiDAR pojawia się i znika.

Oczekiwane: symulacja całego demo gaśnie, gdy pojawia się prawdziwe urządzenie (brak fałszywych
zdarzeń), LiDAR jest "real", a po wypięciu port jest zwalniany i symulacja wraca – bez restartu.
"""
import importlib
import os
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="pty tylko w Linuksie (uruchamiaj w kontenerze)")

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def wait_state(client, pred, timeout=12.0):
    end = time.time() + timeout
    s = None
    while time.time() < end:
        s = client.get("/api/state").json()
        if pred(s):
            return s
        time.sleep(0.2)
    raise AssertionError(f"stan nie osiągnięty: {s and {k: v['state'] for k, v in s['devices'].items()}}")


def test_lidar_hotplug_both_ways():
    from .fakes import FakeLidar
    lidar = FakeLidar(obstacle_m=1.8).start()
    env = {"HW_MODE": "auto", "LIDAR_PORT": lidar.path, "DETECT_INTERVAL_S": "0.5", "SHIELD_LEARN_S": "0.5"}
    if not os.path.isdir("/app/static"):
        env["STATIC_DIR"] = os.path.join(ROOT, "frontend")
    os.environ.update(env)
    try:
        import app.config
        import app.main
        importlib.reload(app.config)
        importlib.reload(app.main)
        from fastapi.testclient import TestClient
        with TestClient(app.main.app) as client:
            s = wait_state(client, lambda s: s["devices"]["lidar"]["state"] == "real" and not s["sim"])
            assert s["devices"]["oak"]["state"] == "sim"
            s = wait_state(client, lambda s: s["shield"]["live"] and s["shield"]["ready"])
            assert s["hand"] is None                                   # brak fałszywej dłoni z symulacji
            assert s["drive"]["lock"] == "no_camera"                   # bez prawdziwej kamery koła stoją
            assert bytes(lidar.received) == b""

            lidar.close()                                              # "wypięcie kabla"
            s = wait_state(client, lambda s: s["devices"]["lidar"]["state"] == "sim" and s["sim"])
            assert client.get("/api/devices").json()["supervisor"]["assigned"] == {}
    finally:
        lidar.close()
        for k in env:
            os.environ.pop(k, None)
