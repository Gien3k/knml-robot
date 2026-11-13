"""FastAPI: dashboard (statyczny build), WebSockety telemetrii i wideo, API operatora."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import struct
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .adas.controller import AdasController
from .adas.lidar_proc import LidarProcessor
from .config import settings
from .drivers import detect
from .hub import Hub
from .supervisor import Supervisor

log = logging.getLogger("app")

hub = Hub()
lidar_proc = LidarProcessor()
controller = AdasController(hub, lidar_proc)
supervisor = Supervisor(hub, lidar_proc)


# --------------------------------------------------------------------------- perf
class _Perf:
    def __init__(self) -> None:
        self.prev: tuple[int, int] | None = None

    def sample(self) -> dict:
        out: dict = {}
        try:
            with open("/proc/stat") as f:
                parts = [int(x) for x in f.readline().split()[1:]]
            idle, total = parts[3] + parts[4], sum(parts)
            if self.prev:
                di, dt = idle - self.prev[0], total - self.prev[1]
                out["cpu"] = round(100.0 * (1 - di / dt), 1) if dt else None
            self.prev = (idle, total)
        except (OSError, ValueError, IndexError):
            pass
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                out["temp_c"] = round(int(f.read().strip()) / 1000.0, 1)
        except (OSError, ValueError):
            out["temp_c"] = None
        try:
            mem = {}
            with open("/proc/meminfo") as f:
                for ln in f:
                    k, v = ln.split(":", 1)
                    mem[k] = int(v.split()[0])
            out["mem_used_pct"] = round(100.0 * (1 - mem["MemAvailable"] / mem["MemTotal"]), 1)
        except (OSError, KeyError, ValueError):
            pass
        out["cores"] = os.cpu_count()
        return out


async def perf_loop() -> None:
    p = _Perf()
    while True:
        hub.perf = p.sample()
        await asyncio.sleep(1.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    hub.bind(asyncio.get_running_loop())
    log.info("Start KN ML ADAS 4WD – HW_MODE=%s", settings.hw_mode)
    supervisor.start()
    controller.start()
    task = asyncio.create_task(perf_loop())
    try:
        yield
    finally:
        task.cancel()
        hub.set_drive_cmd(0.0, 0.0, 0.0, settings.gimbal_tilt_deg)
        controller.stop()
        await asyncio.to_thread(supervisor.shutdown)
        log.info("Zatrzymano")


app = FastAPI(title="KN ML · ADAS 4WD", lifespan=lifespan)


# --------------------------------------------------------------------------- stan
def wheel_view(left: float, right: float) -> dict:
    circ = math.pi * settings.wheel_diameter_m
    rpm = lambda v: round(v / circ * 60.0, 1)  # noqa: E731
    return {
        "fl": {"v": left, "rpm": rpm(left)}, "rl": {"v": left, "rpm": rpm(left)},
        "fr": {"v": right, "rpm": rpm(right)}, "rr": {"v": right, "rpm": rpm(right)},
    }


def build_state() -> dict:
    rv = hub.rover
    w = rv.get("wheels", {"L": 0.0, "R": 0.0})
    oak = hub.oak
    adas = dict(hub.adas)
    return {
        "type": "state",
        "t": time.time(),
        "sensors": {k: hub.status[k].to_dict() for k in hub.SENSORS},
        "oak": {k: oak.get(k) for k in ("detections", "hand", "latency_ms", "nn", "nn_fps", "sim_phase")},
        "lidar": {k: hub.lidar.get(k) for k in ("tracks", "safety", "pose", "hz")},
        "drive": {
            "wheels": wheel_view(float(w.get("L", 0.0)), float(w.get("R", 0.0))),
            "source": w.get("source"),
            "battery_v": rv.get("battery_v"), "gimbal": rv.get("gimbal"), "imu": rv.get("imu"),
        },
        "adas": adas,
        "estop": hub.manual_estop,
        "perf": hub.perf,
        "events": list(hub.events)[-8:],
    }


def _json(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=float)


# --------------------------------------------------------------------------- API
@app.get("/api/health")
def health():
    return {"ok": True, "t": time.time()}


@app.get("/api/state")
def state():
    return JSONResponse(json.loads(_json(build_state())))


@app.get("/api/config")
def config():
    s = settings
    return {
        "hw_mode": s.hw_mode, "safety_m": s.safety_distance_m, "estop_m": s.estop_distance_m,
        "geofence_m": s.geofence_radius_m, "half_length": s.robot_half_length_m,
        "half_width": s.robot_half_width_m, "track_width": s.track_width_m,
        "wheel_diameter": s.wheel_diameter_m, "oak_mirror": s.oak_mirror, "oak_hfov": s.oak_hfov_deg,
        "max_speed": s.max_wheel_speed, "mat_max_speed": s.mat_max_wheel_speed,
    }


@app.get("/api/devices")
def devices():
    """Diagnostyka: co widzi kontener (USB, porty szeregowe, kamery, DepthAI)."""
    return {**detect.diagnostics(),
            "drivers": {k: (None if d is None else {"kind": d.kind, "target": d.target,
                                                     "alive": d.is_alive(), "error": d.error})
                        for k, d in supervisor.drivers.items()}}


class EstopBody(BaseModel):
    active: bool | None = None


@app.post("/api/estop")
def estop(body: EstopBody | None = None):
    hub.manual_estop = (not hub.manual_estop) if body is None or body.active is None else body.active
    hub.event("critical" if hub.manual_estop else "info",
              "STOP operatora" if hub.manual_estop else "Wznowiono po STOP operatora")
    return {"estop": hub.manual_estop}


@app.post("/api/recalibrate")
def recalibrate():
    hub.recalibrate_request.set()
    return {"ok": True}


class DriveTest(BaseModel):
    left: float
    right: float
    duration: float = 1.5


@app.post("/api/drive/test")
def drive_test(body: DriveTest):
    """Test silników przez operatora. Bezpieczeństwo LiDAR i E-STOP nadal obowiązują."""
    lim = settings.max_wheel_speed
    dur = max(0.1, min(5.0, body.duration))
    hub.manual_drive = {"L": max(-lim, min(lim, body.left)), "R": max(-lim, min(lim, body.right)),
                        "until": time.monotonic() + dur}
    hub.event("info", f"Test napędu L={body.left:+.2f} R={body.right:+.2f} m/s przez {dur:.1f} s")
    return {"ok": True, **hub.manual_drive}


LOGOS = {
    "kolo": ["logo_kola", "logo_kolo", "logo-kola", "logo-kolo", "kolo", "kn"],
    "rzeszow": ["logo_rzeszow", "logo-rzeszow", "rzeszow"],
}


@app.get("/branding/{name}")
def branding(name: str):
    if name not in LOGOS:
        return Response(status_code=404)
    base = Path(settings.assets_dir)
    for stem in LOGOS[name]:
        for ext in (".svg", ".png", ".webp", ".jpg", ".jpeg"):
            p = base / f"{stem}{ext}"
            if p.is_file():
                return FileResponse(p, headers={"Cache-Control": "no-cache", "X-Logo-Source": "assets"})
    ph = Path(settings.static_dir) / "placeholders" / f"logo-{name}.svg"
    if ph.is_file():
        return FileResponse(ph, media_type="image/svg+xml",
                            headers={"Cache-Control": "no-cache", "X-Logo-Source": "placeholder"})
    return Response(status_code=404)


# --------------------------------------------------------------------------- WebSockety
@app.websocket("/ws/telemetry")
async def ws_telemetry(ws: WebSocket):
    await ws.accept()
    scan_seq = 0
    try:
        while True:
            await ws.send_text(_json(build_state()))
            seq, scan = hub.scan_slot.get()
            if scan is not None and seq != scan_seq:
                scan_seq = seq
                await ws.send_text(_json(scan))
            await asyncio.sleep(0.05)
    except (WebSocketDisconnect, RuntimeError):
        pass


@app.websocket("/ws/video/{name}")
async def ws_video(ws: WebSocket, name: str):
    slot = hub.video.get(name)
    if slot is None:
        await ws.close(code=4404)
        return
    await ws.accept()
    seq = 0
    try:
        while True:
            # tylko najnowsza klatka: podczas wysyłania poprzedniej slot jest nadpisywany
            seq, item = await slot.next(seq)
            header, jpeg = item
            hb = _json(header).encode()
            await ws.send_bytes(struct.pack(">I", len(hb)) + hb + jpeg)
    except (WebSocketDisconnect, RuntimeError):
        pass


# --------------------------------------------------------------------------- frontend
if os.path.isdir(settings.static_dir):
    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="static")
else:
    log.warning("Brak katalogu frontendu %s", settings.static_dir)
