"""FastAPI: ekran kiosku (statyczny), WebSocket stanu, strumień MJPEG, API diagnostyczne."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

from . import branding
from .calibration import Calibrator, load_geom
from .config import settings
from .control import ControlLoop
from .hub import DEVICES, Hub
from .hw.camera import depthai_version
from .logic import vision
from .logic.rps import classify_hand
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
    return {"gesture": g, "gesture_pl": GESTURE_PL.get(g, "Nie rozpoznaję gestu"),
            "rps": classify_hand(h), "dist_m": dist,
            "x_norm": x, "y_norm": h.get("y_norm"), "landmarks": h.get("landmarks"),
            "bbox": h.get("bbox"), "map": pos, "source": h.get("source")}


_temp_cache = {"t": 0.0, "v": None}


def cpu_temp() -> float | None:
    """Temperatura procesora Raspberry [°C] (odczyt co 5 s); None poza Raspberry."""
    now = time.monotonic()
    if now - _temp_cache["t"] > 5.0:
        _temp_cache["t"] = now
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                _temp_cache["v"] = round(int(f.read().strip()) / 1000.0, 1)
        except (OSError, ValueError):
            _temp_cache["v"] = None
    return _temp_cache["v"]


def sim_temp() -> float:
    """Symulacja: udawana temperatura Raspberry (jak udawane napięcie akumulatorów), żeby podgląd ją pokazywał."""
    return round(54.0 + 3.0 * math.sin(time.monotonic() / 90.0), 1)


def frontend_build(root: str) -> str:
    """Krótki odcisk plików ekranu (nazwa, rozmiar, czas zmiany). Inny po wgraniu nowej wersji:
    otwarta strona kiosku sama się wtedy przeładuje (bez tego pokazywałaby stary kod do ręcznego F5)."""
    h = hashlib.sha1()
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "archiwum")
        for name in sorted(files):
            if name.endswith((".js", ".css", ".html")):
                st = os.stat(os.path.join(base, name))
                h.update(f"{os.path.relpath(os.path.join(base, name), root)}|{st.st_size}|{st.st_mtime_ns}".encode())
    return h.hexdigest()[:12]


FRONTEND_BUILD = frontend_build(settings.static_dir) if os.path.isdir(settings.static_dir) else "none"


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
                "fps": hub.cam_info.get("fps"), "chip_c": hub.cam_info.get("chip_c")},
        # co pokazuje ekran (kolor / głębia / podczerwień) i z czego są świeże klatki
        "lens": {**hub.lens, "shown": hub.vision_live(now) or "color"},
        "mode": hub.mode,
        "mirror": hub.mirror,
        "battery": hub.battery,
        "temp_c": cpu_temp() if not hub.sim_active else (cpu_temp() or sim_temp()),
        "moved_ago": round(time.monotonic() - hub.moved_t, 1) if hub.moved_t else None,
        "game": hub.game,
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
            "hand_near_m": s.hand_near_m, "hand_far_m": s.hand_far_m, "mirror": hub.mirror,
            "cam_offset": [s.cam_offset_x, s.cam_offset_y], "hw_mode": s.hw_mode, "build": FRONTEND_BUILD,
            "depth": {"near_m": vision.NEAR_M, "far_m": vision.FAR_M, "palette": vision.palette_css()}}


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


class LensChoice(BaseModel):
    lens: str = "auto"


@app.post("/api/lens")
def choose_lens(body: LensChoice):
    """Co pokazuje ekran: color | depth | ir (na ~25 s) albo auto (cykl, gdy nikt nie steruje)."""
    lens = body.lens if body.lens in vision.LENSES else "auto"
    hub.lens_request = lens
    return {"ok": True, "lens": lens}


@app.post("/api/mode/{mode}")
def choose_mode(mode: str, scene: int | None = None):
    """Ekran startowy: drive (jazda dłonią) | game (papier, kamień, nożyce) | menu (powrót).
    scene: numer ekranu, na którym wybrano (state.mode.scene); spóźniony wybór z innej strony jest pomijany."""
    if mode not in ("drive", "game", "menu"):
        return JSONResponse({"ok": False, "error": "drive | game | menu"}, status_code=404)
    hub.mode_request = (mode, scene)
    return {"ok": True}


@app.post("/api/game/{action}")
def game(action: str, scene: int | None = None):
    """Papier, kamień, nożyce: start (jak kciuk w górę albo "Zagraj jeszcze raz") lub stop."""
    if action not in ("start", "stop"):
        return JSONResponse({"ok": False, "error": "start | stop"}, status_code=404)
    hub.game_request = (action, scene)
    return {"ok": True}


@app.get("/api/game/debug")
def game_debug():
    """Ostatnie klatki z okien "Teraz!": rozpoznany gest i wyprostowanie palców (0..1), do strojenia."""
    return {"frames": list(control.game.debug)}


@app.post("/api/mirror")
def mirror(on: int | None = None):
    """Lustro obrazu dla wszystkich ekranów naraz (Shift+M). Bez parametru: przełącz."""
    hub.mirror = (not hub.mirror) if on is None else bool(on)
    return {"ok": True, "mirror": hub.mirror}


class Hello(BaseModel):
    id: str = ""
    build: str = ""


@app.post("/api/hello")
def hello(h: Hello, request: Request):
    """Otwarta strona zgłasza się co kilkanaście sekund: która wersja ekranu działa w której przeglądarce."""
    now = time.monotonic()
    hub.clients[h.id[:40]] = {"build": h.build[:20], "ua": request.headers.get("user-agent", "")[:160], "t": now}
    for k in [k for k, v in hub.clients.items() if now - v["t"] > 60]:
        del hub.clients[k]
    return {"ok": True, "current": h.build == FRONTEND_BUILD}


@app.get("/api/clients")
def clients():
    """Lista otwartych stron (ostatnia minuta) i czy mają aktualną wersję ekranu."""
    now = time.monotonic()
    return {"build": FRONTEND_BUILD, "clients": [
        {"id": k, "build": v["build"], "current": v["build"] == FRONTEND_BUILD, "ago_s": round(now - v["t"], 1),
         "ua": v["ua"]} for k, v in hub.clients.items() if now - v["t"] <= 60]}


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


def _mjpeg(slot) -> StreamingResponse:
    async def gen():
        # granica PO każdej klatce – przeglądarka pokazuje klatkę od razu, bez czekania na następną
        yield b"--frame\r\n"
        seq = 0
        while not _shutting_down:
            new_seq, item = await slot.next(seq, timeout=1.0)
            if item is None or new_seq == seq:
                continue
            seq = new_seq
            jpeg, _meta = item
            yield (b"Content-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg)).encode()
                   + b"\r\n\r\n" + jpeg + b"\r\n--frame\r\n")

    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame",
                             headers={"Cache-Control": "no-cache, no-store", "Pragma": "no-cache"})


@app.get("/stream.mjpg")
async def stream():
    return _mjpeg(hub.frame_slot)


@app.get("/vision.mjpg")
async def vision_stream():
    """"Jak widzi robot": pokolorowana głębia albo podczerwień (klatki tylko, gdy ekran je pokazuje)."""
    return _mjpeg(hub.vision_slot)


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
                                          "cam_only": sc["cam_only"], "hz": sc.get("hz")}))
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
