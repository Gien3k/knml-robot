"""Sterownik ESP32 na wirtualnym porcie: 20 Hz, zgoda na ruch, tarcza, stop awaryjny, stop przy wyjściu."""
import dataclasses
import os
import time

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="pty tylko w Linuksie (uruchamiaj w kontenerze)")

from app.config import Settings  # noqa: E402
from app.hub import Hub  # noqa: E402
from app.hw.esp32 import Esp32Driver  # noqa: E402
from app.logic.shield import ShieldState  # noqa: E402

from .fakes import FakeEsp32  # noqa: E402


@pytest.fixture
def rig():
    esp = FakeEsp32(chatty=True).start()
    s = dataclasses.replace(Settings(), max_speed=0.5)
    hub = Hub(s)
    drv = Esp32Driver(hub, esp.path, s)
    drv.start()
    yield esp, hub, drv
    drv.stop()
    drv.join(timeout=2.0)
    esp.close()


def wait_for(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_init_commands_then_zero_heartbeat(rig):
    esp, hub, drv = rig
    assert wait_for(lambda: len(esp.motion()) >= 10)
    types = [o.get("T") for o in esp.lines[:5]]
    assert 131 in types and 136 in types                 # telemetria ciągła + heartbeat firmware
    chassis = [o for o in esp.lines if o.get("T") == 900]
    assert chassis == [{"T": 900, "main": 1, "module": 0}]   # RaspRover, raz po połączeniu
    assert types.index(900) < types.index(131)
    assert all(m["L"] == 0 and m["R"] == 0 for m in esp.motion())
    n0 = len(esp.motion())
    time.sleep(1.0)
    rate = len(esp.motion()) - n0
    assert 15 <= rate <= 25, f"oczekiwano ~20 komend/s, jest {rate}"
    assert wait_for(lambda: hub.esp32.get("voltage") == 12.05)


def test_sends_allowed_command_and_clamps(rig):
    esp, hub, drv = rig
    hub.drive_cmd = {"L": 0.3, "R": 0.9, "t": time.monotonic() + 60, "allowed": True}
    assert wait_for(lambda: esp.motion() and esp.motion()[-1] == {"T": 1, "L": 0.3, "R": 0.5})


def test_not_allowed_or_stale_means_zero(rig):
    esp, hub, drv = rig
    hub.drive_cmd = {"L": 0.3, "R": 0.3, "t": time.monotonic() + 60, "allowed": False}
    time.sleep(0.3)
    assert esp.motion()[-1] == {"T": 1, "L": 0, "R": 0}
    hub.drive_cmd = {"L": 0.3, "R": 0.3, "t": time.monotonic() - 1.0, "allowed": True}    # stara komenda
    time.sleep(0.3)
    assert esp.motion()[-1] == {"T": 1, "L": 0, "R": 0}


def test_shield_overrides_and_estop_sends_three_stops_immediately(rig):
    esp, hub, drv = rig
    hub.drive_cmd = {"L": 0.4, "R": 0.4, "t": time.monotonic() + 60, "allowed": True}
    assert wait_for(lambda: esp.motion() and esp.motion()[-1]["L"] == 0.4)
    n0 = len(esp.motion())
    hub.shield_state = ShieldState(ready=True, active=True)
    t0 = time.monotonic()
    hub.estop_event.set()
    assert wait_for(lambda: len(esp.motion()) >= n0 + 3, timeout=0.5)
    assert time.monotonic() - t0 < 0.1
    assert esp.motion()[n0:n0 + 3] == [{"T": 1, "L": 0, "R": 0}] * 3
    time.sleep(0.2)
    assert all(m["L"] == 0 for m in esp.motion()[n0:])


def test_estop_reports_stop_time_for_brake_measurement(rig):
    esp, hub, drv = rig
    assert wait_for(lambda: len(esp.motion()) >= 2)        # pętla sterownika już działa
    hub.set_device("esp32", "real")
    hub.shield_state = ShieldState(ready=True, active=True)
    t0 = time.monotonic()
    hub._brake_start(t0, t0)
    hub.estop_event.set()
    assert wait_for(lambda: hub.brake["last_s"] is not None, timeout=0.5)
    assert hub.brake["last_s"] < 0.1                      # stop wysłany natychmiast po wykryciu


def test_stop_sent_on_shutdown(rig):
    esp, hub, drv = rig
    hub.drive_cmd = {"L": 0.2, "R": 0.2, "t": time.monotonic() + 60, "allowed": True}
    assert wait_for(lambda: esp.motion() and esp.motion()[-1]["L"] == 0.2)
    drv.stop()
    drv.join(timeout=2.0)
    time.sleep(0.1)
    assert esp.motion()[-3:] == [{"T": 1, "L": 0, "R": 0}] * 3


def test_silent_esp32_is_released():
    esp = FakeEsp32(chatty=False).start()
    esp.on_data = lambda data: None                       # nie odpowiada nawet na zapytania
    s = Settings()
    hub = Hub(s)
    drv = Esp32Driver(hub, esp.path, s)
    try:
        drv.start()
        drv.join(timeout=4.0)
        assert not drv.is_alive()
        assert "nie odpowiada" in (drv.error or "")
        assert waveshare_query_sent(esp)
    finally:
        drv.stop()
        esp.close()


def test_voltage_units():
    from app.protocol.waveshare import voltage
    assert voltage({"v": 59}) == 0.59          # prawdziwy RaspRover bez ogniw: ekranik "V 0.59"
    assert voltage({"v": 1206}) == 12.06       # setne części wolta
    assert voltage({"v": 12.06}) == 12.06      # wolty (starsze firmware)
    assert voltage({"v": True}) is None and voltage({}) is None


def waveshare_query_sent(esp) -> bool:
    return b'{"T":130}' in bytes(esp.received)


def test_drive_flip_reverses_and_swaps_sides():
    """Kamera z tyłu podwozia: jazda "do przodu, w prawo" = koła do tyłu z zamienionymi stronami."""
    esp = FakeEsp32(chatty=True).start()
    s = dataclasses.replace(Settings(), max_speed=0.5, drive_flip=True)
    hub = Hub(s)
    drv = Esp32Driver(hub, esp.path, s)
    drv.start()
    try:
        hub.drive_cmd = {"L": 0.3, "R": 0.1, "t": time.monotonic() + 60, "allowed": True}
        assert wait_for(lambda: esp.motion() and esp.motion()[-1] == {"T": 1, "L": -0.1, "R": -0.3})
    finally:
        drv.stop()
        drv.join(timeout=2.0)
        esp.close()


def test_flip_is_its_own_inverse():
    from app.hw.esp32 import flip
    assert flip(*flip(0.3, -0.1)) == (0.3, -0.1)
