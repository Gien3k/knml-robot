"""Serwer w trybie symulacji: strona, logotypy, WebSocket, MJPEG, pełne demo bez sprzętu."""
import importlib
import json
import os
import time

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


@pytest.fixture(scope="module")
def client():
    os.environ["HW_MODE"] = "sim"
    os.environ["SHIELD_LEARN_S"] = "1.0"
    if not os.path.isdir("/app/static"):
        os.environ["STATIC_DIR"] = os.path.join(ROOT, "frontend")
        os.environ["ASSETS_DIR"] = os.path.join(ROOT, "assets")
    import app.config
    import app.main
    importlib.reload(app.config)
    importlib.reload(app.main)
    from fastapi.testclient import TestClient
    with TestClient(app.main.app) as c:
        yield c
    for k in ("HW_MODE", "SHIELD_LEARN_S", "STATIC_DIR", "ASSETS_DIR"):
        os.environ.pop(k, None)


def test_index_and_static(client):
    r = client.get("/")
    assert r.status_code == 200
    html = r.text
    assert "Człowiek kontra AI" in html
    assert "http://" not in html and "https://" not in html          # zero CDN
    css = client.get("/styles.css").text
    assert "#16181a" in css and "#0f1112" in css and "Barlow Condensed" in css
    assert "#logoKolo { height: 88px" in css and "#logoRzeszow { height: 64px" in css  # jak na drugim monitorze
    assert "@import" not in css and "https://" not in css
    for font in ("barlow-latin-ext-500-normal.woff2", "barlow-condensed-latin-ext-600-normal.woff2"):
        assert client.get(f"/fonts/{font}").status_code == 200
    assert "https://" not in client.get("/app.js").text


def test_logos(client):
    for name in ("kolo", "rzeszow"):
        r = client.get(f"/branding/{name}")
        assert r.status_code == 200
        assert r.headers["x-logo-source"] in ("assets", "placeholder")
    assert client.get("/branding/nieznane").status_code == 404


def test_full_sim_demo_over_websocket(client):
    seen_states, scans, cfg = set(), 0, None
    with client.websocket_connect("/ws") as ws:
        end = time.time() + 4.0
        while time.time() < end:
            m = json.loads(ws.receive_text())
            if m["type"] == "config":
                cfg = m
            elif m["type"] == "scan":
                scans += 1
                assert len(m["r"]) == 360
                assert isinstance(m["tracks"], list) and isinstance(m["count"], int)
            else:
                assert set(m["devices"]) == {"oak", "lidar", "esp32"}
                assert all(d["state"] == "sim" for d in m["devices"].values())
                assert m["sim"] is True
                seen_states.add(m["drive"].get("label"))
    assert cfg and cfg["max_speed"] > 0
    assert scans >= 10
    assert seen_states & {"CZEKAM", "JAZDA", "WSTECZNY", "STOP"}


def test_state_and_devices_endpoints(client):
    s = client.get("/api/state").json()
    assert s["sim"] is True and s["shield"]["live"] is True
    assert isinstance(s["people"], list)                       # symulowany detektor osób działa
    assert s["people_count"] is not None
    assert set(s["brake"]) == {"last_s", "wheels_s", "record_s", "count", "t"}
    d = client.get("/api/devices").json()
    assert d["supervisor"]["hw_mode"] == "sim"
    assert client.post("/api/recalibrate").json() == {"ok": True}
