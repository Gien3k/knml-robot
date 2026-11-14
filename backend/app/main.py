"""FastAPI: ekran kiosku (statyczny), WebSocket stanu, strumień MJPEG, API diagnostyczne."""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

from . import branding
from .calibration import Calibrator, load_geom
from .config import settings
from .control import ControlLoop
from .hub import DEVICES, Hub
from .hw.camera import depthai_version
from .protocol import waveshare
from .supervisor import Supervisor

log = logging.getLogger("app")

hub = Hub(settings)
load_geom(hub, settings)                 # zapisana kalibracja LiDAR-u (data/calibration.json)
control = ControlLoop(hub, settings)
supervisor = Supervisor(hub, settings)
calibrator = Calibrator(hub, settings)
_shutting_down = False

GESTURE_PL = {"FIVE": "Otwarta dłoń", "FOUR": "Otwarta dłoń", "FIST": "Pięść", "ONE": "Jeden palec",
              "TWO": "Dwa palce", "THREE": "Trzy palce", "OK": "Kciuk w górę", "PEACE": "Znak pokoju"}


def emergency_stop() -> None:
    """Natychmiastowe zero dla kół (sygnał zamknięcia, wyjątek)."""
    global _shutting_down
    _shutting_down = True
    hub.drive_cmd = {"L": 0.0, "R": 0.0, "t": time.monotonic(), "allowed": False}
    hub.estop_event.set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    hub.bind(asyncio.get_running_loop())
    log.info("Start: HW_MODE=%s", settings.hw_mode)
    control.start()
    supervisor.start()
    calibrator.start()
    try:
        yield
    finally:
        emergency_stop()
        calibrator.stop()
        control.stop()
        await asyncio.to_thread(supervisor.shutdown)
        log.info("Zatrzymano")


app = FastAPI(title="KN ML · Autonomiczny Łazik 4WD", lifespan=lifespan)


# --------------------------------------------------------------------------- stan
def hand_payload(now: float) -> dict | None:
    h = hub.hand
    if not h or not h.get("present") or now - h["t"] > 0.5:
        return None
    s = settings
    dist, x = h.get("dist_m"), h.get("x_norm")
    pos = None
    if dist is not None and x is not None:
        xc = (x - 0.5) * 2.0 * dist * math.tan(math.radians(s.oak_hfov_deg) / 2)
        pos = [round(dist + s.cam_offset_x, 3), round(-xc + s.cam_offset_y, 3)]
    g = h.get("gesture")
    return {"gesture": g, "gesture_pl": GESTURE_PL.get(g, "Nie rozpoznaję gestu"), "dist_m": dist,
            "x_norm": x, "y_norm": h.get("y_norm"), "landmarks": h.get("landmarks"),
            "bbox": h.get("bbox"), "map": pos, "source": h.get("source")}


def build_state() -> dict:
    now = time.monotonic()
    tel = hub.esp32
    wheels = hub.wheels
    if wheels is not None and now - wheels.get("t", 0.0) > 1.0:
        wheels = None
    return {
        "type": "state", "t": time.time(),
        "devices": {k: hub.devices[k].to_dict() for k in DEVICES},
        "sim": hub.sim_active,
        "cam": {"live": bool(hub.drive.get("cam_live")), "mode": "sim" if hub.sim_active else hub.cam_info.get("mode"),
                "fps": hub.cam_info.get("fps")},
        "hand": hand_payload(now),
        "shield": {**hub.shield_state.to_dict(), "live": bool(hub.drive.get("lidar_live"))},
        "drive": hub.drive,
        "wheels": None if wheels is None else {"L": wheels["L"], "R": wheels["R"], "source": wheels["source"]},
        # bez ogniw (łazik na samej ładowarce) napięcie koszyka ~0 V – nie pokazujemy go odwiedzającym
        "voltage": tel.get("voltage") if tel.get("t") and now - tel["t"] < 3.0
        and (tel.get("voltage") or 0) >= waveshare.NO_BATTERY_V else None,
        "people": people_payload(now),
        "people_count": (hub.scan or {}).get("count", 0) if hub.drive.get("lidar_live") else None,
        "brake": {k: hub.brake[k] for k in ("last_s", "wheels_s", "record_s", "count", "t")},
        "calib": calibrator.status(),
    }


def people_payload(now: float) -> list[dict] | None:
    """Osoby wykryte kamerą (do ramek na obrazie); None = detekcja nieaktywna."""
    if not hub.drive.get("cam_live"):
        return None
    if not (hub.sim_active or hub.cam_info.get("persons")):
        return None
    cp = hub.cam_people
    return cp["list"] if now - cp["t"] <= 0.6 else []


def ui_config() -> dict:
    s = settings
    return {"max_speed": s.max_speed, "shield_radius_m": s.shield_radius_m, "hfov_deg": s.oak_hfov_deg,
            "hand_near_m": s.hand_near_m, "hand_far_m": s.hand_far_m, "mirror": s.oak_mirror,
            "cam_offset": [s.cam_offset_x, s.cam_offset_y], "hw_mode": s.hw_mode}


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
    return ui_config()


@app.get("/api/devices")
def devices():
    return JSONResponse(json.loads(_json({
        "devices": {k: hub.devices[k].to_dict() for k in DEVICES},
        "depthai": depthai_version(),
        "supervisor": supervisor.diagnostics(),
        "events": list(hub.events)[-20:],
    })))


@app.post("/api/recalibrate")
def recalibrate():
    hub.recalibrate_request.set()
    return {"ok": True}


# ------------------------------------------------- kreator kalibracji (ukryty: 5x logo + hasło)
class CalibStart(BaseModel):
    password: str = ""


@app.post("/api/calibration/start")
def calibration_start(body: CalibStart):
    if not calibrator.check_password(body.password):
        return JSONResponse({"ok": False, "error": "Złe hasło"}, status_code=403)
    calibrator.begin()
    return {"ok": True}


@app.post("/api/calibration/cancel")
def calibration_cancel():
    calibrator.cancel()
    return {"ok": True}


@app.post("/api/calibration/skip")
def calibration_skip():
    calibrator.skip()
    return {"ok": True}


@app.post("/api/calibration/close")
def calibration_close():
    calibrator.close()
    return {"ok": True}


@app.get("/api/calibration")
def calibration_status():
    return JSONResponse(json.loads(_json({**calibrator.status(),
                                          "geom": {"offset_deg": hub.lidar_geom.offset_deg,
                                                   "clockwise": hub.lidar_geom.clockwise}})))


@app.get("/branding/{name}")
def logo(name: str):
    if name not in branding.LOGOS:
        return Response(status_code=404)
    p = branding.find_logo(settings.assets_dir, name)
    if p is not None:
        return FileResponse(p, headers={"Cache-Control": "no-cache", "X-Logo-Source": "assets"})
    return Response(branding.PLACEHOLDERS[name], media_type="image/svg+xml",
                    headers={"Cache-Control": "no-cache", "X-Logo-Source": "placeholder"})


@app.get("/stream.mjpg")
async def stream():
    async def gen():
        # granica PO każdej klatce – przeglądarka pokazuje klatkę od razu, bez czekania na następną
        yield b"--frame\r\n"
        seq = 0
        while not _shutting_down:
            new_seq, item = await hub.frame_slot.next(seq, timeout=1.0)
            if item is None or new_seq == seq:
                continue
            seq = new_seq
            jpeg, _meta = item
            yield (b"Content-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode()
                   + b"\r\n\r\n" + jpeg + b"\r\n--frame\r\n")

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-cache, no-store", "Pragma": "no-cache"})


@app.websocket("/ws")
async def ws_state(ws: WebSocket):
    await ws.accept()
    scan_seq = 0
    try:
        await ws.send_text(_json({"type": "config", **ui_config()}))
        while not _shutting_down:
            await ws.send_text(_json(build_state()))
            sc = hub.scan
            if sc is not None and sc["seq"] != scan_seq:
                scan_seq = sc["seq"]
                await ws.send_text(_json({"type": "scan", "seq": sc["seq"], "r": sc["r"], "fg": sc["fg"],
                                          "tracks": sc["tracks"], "count": sc["count"],
                                          "cam_only": sc["cam_only"]}))
            await asyncio.sleep(0.05)
    except (WebSocketDisconnect, RuntimeError):
        pass


@app.websocket("/{rest:path}")
async def ws_unknown(ws: WebSocket, rest: str):
    """Nieznana ścieżka WebSocket (np. karta ze starą wersją strony) – grzeczne zamknięcie."""
    await ws.close(code=4404)


# --------------------------------------------------------------------------- frontend
class NoCacheStatic(StaticFiles):
    """Pliki ekranu zawsze sprawdzane u serwera (ETag -> 304), żeby kiosk po aktualizacji
    nie pokazywał starej wersji z pamięci podręcznej przeglądarki."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


if os.path.isdir(settings.static_dir):
    app.mount("/", NoCacheStatic(directory=settings.static_dir, html=True), name="static")
else:
    log.warning("Brak katalogu frontendu %s", settings.static_dir)
