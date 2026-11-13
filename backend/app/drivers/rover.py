"""Podwozie RaspRover (ESP32, protokół JSON Waveshare) – sterownik prawdziwy i symulowany.

Komendy (jedna linia JSON zakończona \\n, 115200 bodów):
  {"T":1,"L":0.2,"R":0.2}                    prędkość lewej/prawej strony [m/s], ±0.5
  {"T":133,"X":30,"Y":10,"SPD":0,"ACC":0}    głowica pan-tilt [°]
  {"T":131,"cmd":1}                          ciągła telemetria
  {"T":130}                                  jednorazowe zapytanie o telemetrię
Odpowiedź telemetrii: {"T":1001,"L":..,"R":..,"v":..,...}

Koła po każdej stronie są sterowane parami (L = przód-lewe + tył-lewe).
"""
from __future__ import annotations

import json
import math
import time

from ..config import settings
from ..hub import Hub
from ..sim.world import get_world
from .common import Driver
from .detect import open_serial_noreset


def _wheels_dict(left: float, right: float, source: str) -> dict:
    return {"L": round(left, 3), "R": round(right, 3), "source": source}


class RoverReal(Driver):
    sensor, kind = "rover", "real"

    def loop(self) -> None:
        ser = open_serial_noreset(self.target, settings.rover_baud, 0)
        hub = self.hub

        def send(obj: dict) -> None:
            ser.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())

        hub.set_status("rover", "real", self.target, "Połączono z ESP32")
        hub.event("info", f"Sterownik ESP32 podłączony ({self.target})")
        send({"T": 131, "cmd": 1})
        buf = b""
        started = last_fb = time.monotonic()
        last_cmd = last_gimbal = 0.0
        gimbal_sent: tuple[float, float] | None = None
        fb: dict = {}
        try:
            while not self.halted:
                now = time.monotonic()
                n = ser.in_waiting
                if n:
                    buf += ser.read(n)
                    *lines, buf = buf.split(b"\n")
                    for ln in lines:
                        ln = ln.strip()
                        if not ln.startswith(b"{"):
                            continue
                        try:
                            obj = json.loads(ln)
                        except ValueError:
                            continue
                        if obj.get("T") == 1001:
                            fb, last_fb = obj, now
                            hub.status["rover"].fps.tick()

                cmd = hub.get_drive_cmd(settings.cmd_timeout_s)
                if now - last_cmd >= 0.05:                      # 20 Hz = też heartbeat
                    lim = 0.5
                    send({"T": 1, "L": round(max(-lim, min(lim, cmd["L"])), 3),
                          "R": round(max(-lim, min(lim, cmd["R"])), 3)})
                    last_cmd = now
                if settings.gimbal_enabled and now - last_gimbal >= 0.1:
                    pan = max(-180.0, min(180.0, cmd["pan"]))
                    tilt = max(-30.0, min(90.0, cmd["tilt"]))
                    if gimbal_sent is None or abs(pan - gimbal_sent[0]) > 0.8 or \
                            abs(tilt - gimbal_sent[1]) > 0.8 or now - last_gimbal > 1.0:
                        send({"T": 133, "X": round(pan, 1), "Y": round(tilt, 1), "SPD": 0, "ACC": 0})
                        gimbal_sent = (pan, tilt)
                    last_gimbal = now

                fresh = now - last_fb < 1.0
                if fresh:
                    v = fb.get("v")
                    if isinstance(v, (int, float)) and v > 100:
                        v = v / 100.0
                    wheels = _wheels_dict(float(fb.get("L", cmd["L"])), float(fb.get("R", cmd["R"])), "telemetria")
                    hub.rover = {
                        "wheels": wheels, "battery_v": v,
                        "gimbal": {"pan": gimbal_sent[0] if gimbal_sent else 0.0,
                                   "tilt": gimbal_sent[1] if gimbal_sent else 0.0},
                        "imu": {k: fb.get(k) for k in ("r", "p", "y") if k in fb},
                        "t": now,
                    }
                else:
                    hub.rover = {**hub.rover, "wheels": _wheels_dict(cmd["L"], cmd["R"], "komenda"), "t": now}
                    if now - started > 3.0:
                        hub.status["rover"].message = "Brak telemetrii T:1001 – komendy są wysyłane"
                self.sleep(0.005)
        finally:
            try:
                for _ in range(3):
                    send({"T": 1, "L": 0, "R": 0})
                    time.sleep(0.02)
                ser.close()
            except Exception:
                pass


class RoverMock(Driver):
    sensor, kind = "rover", "mock"

    def __init__(self, hub: Hub, message: str = "") -> None:
        super().__init__(hub, "sim")
        self.message = message

    def loop(self) -> None:
        world = get_world()
        hub = self.hub
        hub.set_status("rover", "mock", "symulacja", self.message or "Nie wykryto ESP32 – napęd symulowany")
        left = right = pan = tilt = 0.0
        tau = 0.15
        last = time.monotonic()
        t0 = last
        while not self.halted:
            now = time.monotonic()
            dt = now - last
            last = now
            cmd = hub.get_drive_cmd(settings.cmd_timeout_s)
            a = 1 - math.exp(-dt / tau)
            left += (cmd["L"] - left) * a
            right += (cmd["R"] - right) * a
            step = 200.0 * dt                       # °/s serwa
            pan += max(-step, min(step, cmd["pan"] - pan))
            tilt += max(-step, min(step, cmd["tilt"] - tilt))
            world.motion_enabled = hub.adas.get("mode") == "mat"
            world.set_wheels(left, right)
            load = abs(left) + abs(right)
            hub.rover = {
                "wheels": _wheels_dict(left, right, "symulacja"),
                "battery_v": round(12.15 - 0.25 * ((now - t0) / 600 % 1) - 0.4 * load + 0.01 * math.sin(now * 3), 2),
                "gimbal": {"pan": round(pan, 1), "tilt": round(tilt, 1)},
                "imu": {"y": round(math.degrees(world.robot[2]), 1)},
                "t": now,
            }
            hub.status["rover"].fps.tick()
            self.sleep(0.02)
