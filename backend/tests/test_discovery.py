"""Auto-detekcja portów na wirtualnych portach (pty): LiDAR vs ESP32, dowolna kolejność.

Kluczowe gwarancje:
  * do portu LiDAR-u nie trafia ANI JEDEN bajt,
  * do ESP32 nie trafia żadna komenda ruchu, zanim rola nie zostanie potwierdzona,
  * milczący ESP32 dostaje dokładnie jedno zapytanie {"T":130}.
"""
import dataclasses
import os
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="pty tylko w Linuksie (uruchamiaj w kontenerze)")

from app.config import Settings  # noqa: E402
from app.hub import Hub  # noqa: E402
from app.hw.discovery import PortInfo, _mostly_binary, parse_extra_ids, probe  # noqa: E402
from app.protocol import waveshare  # noqa: E402

from .fakes import FakeEsp32, FakeLidar, FakeNoise, FakePort  # noqa: E402

FAST = dict(lidar_window=0.4, passive_window=0.6, query_wait=0.5)


@pytest.fixture
def devices():
    made = []

    def make(cls, **kw):
        d = cls(**kw).start()
        made.append(d)
        return d

    yield make
    for d in made:
        d.close()


def test_lidar_identified_and_never_written(devices):
    lidar = devices(FakeLidar)
    res = probe(lidar.path, **FAST)
    assert res.role == "lidar"
    assert res.frames >= 3
    assert res.wrote == 0
    time.sleep(0.1)
    assert bytes(lidar.received) == b""


def test_chatty_esp32_identified_passively(devices):
    esp = devices(FakeEsp32, chatty=True, boot_garbage=True)
    res = probe(esp.path, **FAST)
    assert res.role == "esp32"
    assert res.wrote == 0
    assert bytes(esp.received) == b""


def test_silent_esp32_gets_exactly_one_query(devices):
    esp = devices(FakeEsp32, chatty=False)
    res = probe(esp.path, **FAST)
    assert res.role == "esp32"
    time.sleep(0.1)
    assert bytes(esp.received) == waveshare.QUERY
    assert esp.motion() == []


def test_binary_noise_is_unknown_and_not_queried(devices):
    noise = devices(FakeNoise)
    res = probe(noise.path, **FAST)
    assert res.role == "unknown"
    assert res.wrote == 0
    assert bytes(noise.received) == b""


def test_silent_unknown_device(devices):
    dead = devices(FakePort)
    res = probe(dead.path, **FAST)
    assert res.role == "unknown"
    assert bytes(dead.received) == waveshare.QUERY


def test_query_disabled_when_esp32_already_found(devices):
    dead = devices(FakePort)
    res = probe(dead.path, allow_query=False, **FAST)
    assert res.role == "unknown" and res.wrote == 0
    assert bytes(dead.received) == b""


def test_busy_port_reported(devices):
    from app.hw.serial_util import open_port
    esp = devices(FakeEsp32)
    held = open_port(esp.path, 115200)
    try:
        assert probe(esp.path, **FAST).role == "busy"
    finally:
        held.close()


def test_helpers():
    assert (0x10C4, 0xEA60) in parse_extra_ids("10c4:ea60, 303a:*")
    assert (0x303A, None) in parse_extra_ids("303a:*")
    assert _mostly_binary(bytes(range(128, 256)))
    assert not _mostly_binary(b'{"T":1001}\n' * 10)


# --------------------------------------------------------------------------- nadzorca
@pytest.mark.parametrize("order", ["lidar_first", "esp_first"])
@pytest.mark.parametrize("chatty", [True, False])
def test_supervisor_assigns_roles_in_any_order(devices, monkeypatch, order, chatty):
    import app.hw.discovery as disc
    import app.supervisor as sup_mod

    lidar = devices(FakeLidar, obstacle_m=1.5)
    esp = devices(FakeEsp32, chatty=chatty)
    ports = [PortInfo(lidar.path, os.path.realpath(lidar.path), 0x10C4, 0xEA60, "CP2102", "whitelist"),
             PortInfo(esp.path, os.path.realpath(esp.path), 0x10C4, 0xEA60, "CP2102", "whitelist")]
    if order == "esp_first":
        ports.reverse()
    monkeypatch.setattr(sup_mod, "candidate_ports", lambda *a, **k: list(ports))
    monkeypatch.setattr(disc, "ESP_PASSIVE_S", 0.6)
    real_probe = disc.probe
    monkeypatch.setattr(sup_mod, "probe", lambda path, **kw: real_probe(path, passive_window=0.6, **kw))

    s = dataclasses.replace(Settings(), hw_mode="auto", shield_learn_s=0.5)
    hub = Hub(s)
    sup = sup_mod.Supervisor(hub, s)
    try:
        sup._discover()
        assert sup.lidar is not None and sup.lidar.port == lidar.path
        assert sup.esp32 is not None and sup.esp32.port == esp.path
        # ESP32 dostaje komendy (same zera – nikt nie pozwolił jechać), LiDAR nic
        time.sleep(0.8)
        assert hub.scan is not None and hub.scan["source"] == "lidar"
        motion = esp.motion()
        assert len(motion) >= 8, "pętla 20 Hz nie wysyła komend"
        assert all(m["L"] == 0 and m["R"] == 0 for m in motion)
        assert bytes(lidar.received) == b""
        sup._check_drivers()
        assert hub.devices["lidar"].state == "real"
        assert hub.devices["esp32"].state == "real"
        sup._update_sim()
        assert sup.sim is None and not hub.sim_active      # prawdziwy sprzęt = brak symulacji
    finally:
        sup.shutdown()
    # przy zamknięciu ESP32 dostaje stop, LiDAR nadal nic
    assert esp.motion()[-1] == {"T": 1, "L": 0, "R": 0}
    assert bytes(lidar.received) == b""


def test_unplugged_lidar_released_and_falls_back(devices, monkeypatch):
    import app.supervisor as sup_mod

    lidar = devices(FakeLidar)
    ports = [PortInfo(lidar.path, os.path.realpath(lidar.path), 0x10C4, 0xEA60, "", "whitelist")]
    monkeypatch.setattr(sup_mod, "candidate_ports", lambda *a, **k: list(ports))
    s = dataclasses.replace(Settings(), hw_mode="auto")
    hub = Hub(s)
    sup = sup_mod.Supervisor(hub, s)
    try:
        sup._discover()
        assert sup.lidar is not None
        time.sleep(0.3)
        lidar.close()                                 # "wypięcie kabla": brak danych
        sup.lidar.join(timeout=4.0)
        assert not sup.lidar.is_alive()
        ports.clear()
        sup._check_drivers()
        assert sup.lidar is None
        assert hub.devices["lidar"].state == "sim"
        assert hub.scan is None
        assert sup.assigned == {}
    finally:
        sup.shutdown()
