"""Nadzorca kamery OAK-D Pro (w procesie serwera).

* Co DETECT_INTERVAL_S sprawdza obecność kamery: sysfs (VID 03e7) i
  dai.Device.getAllAvailableDevices().
* Kamerę obsługuje OSOBNY proces (camera_worker, multiprocessing "spawn").
* Watchdog: brak wiadomości przez OAK_NO_FRAME_S (po starcie) albo brak startu
  w OAK_BOOT_TIMEOUT_S => proces jest ubijany; ponowny start z opóźnieniem 1 -> 2 -> 5 s.
"""
from __future__ import annotations

import glob
import logging
import multiprocessing as mp
import os
import threading
import time

from ..config import Settings
from ..hub import Hub

log = logging.getLogger("camera")

BACKOFF_S = (1.0, 2.0, 5.0)
LUXONIS_VID = "03e7"

_dai = None
_dai_error: str | None = None


def depthai():
    """Leniwy import DepthAI (cache). None, gdy biblioteki nie ma."""
    global _dai, _dai_error
    if _dai is None and _dai_error is None:
        try:
            import depthai as dai
            _dai = dai
        except Exception as e:
            _dai_error = f"{type(e).__name__}: {e}"
            log.warning("DepthAI niedostępne: %s", _dai_error)
    return _dai


def depthai_version() -> str | None:
    dai = depthai()
    return getattr(dai, "__version__", None) if dai else None


def usb_luxonis_present() -> bool | None:
    """True/False z sysfs; None, gdy sysfs jest niedostępny (np. Windows)."""
    devs = glob.glob("/sys/bus/usb/devices/*/idVendor")
    if not devs and not os.path.isdir("/sys/bus/usb/devices"):
        return None
    for p in devs:
        try:
            with open(p) as f:
                if f.read().strip().lower() == LUXONIS_VID:
                    return True
        except OSError:
            continue
    return False


def list_oak_devices() -> list[dict]:
    dai = depthai()
    if dai is None:
        return []
    out = []
    for d in dai.Device.getAllAvailableDevices():
        out.append({"name": getattr(d, "name", ""), "mxid": d.getMxId() if hasattr(d, "getMxId") else "",
                    "state": str(getattr(d, "state", "")).split(".")[-1]})
    return out


def oak_present() -> tuple[bool, str]:
    usb = usb_luxonis_present()
    if usb is False:
        return False, "brak urządzenia Luxonis (03e7) na USB"
    if usb is None and os.name == "posix":
        # Linux bez /sys/bus/usb (np. Docker Desktop): kontener nie ma dostępu do USB, a OAK-D Pro
        # jest kamerą USB, więc DepthAI i tak jej nie znajdzie. Nie wołamy go: wyszukiwanie
        # trzyma GIL przez ~0,5 s i co kilka sekund zamraża cały serwer (radar, sterowanie, obraz).
        return False, "kontener nie ma dostępu do USB (kamera tylko w symulacji)"
    if depthai() is None:
        return False, f"DepthAI niedostępne ({_dai_error})"
    try:
        devs = list_oak_devices()
    except Exception as e:
        return False, f"getAllAvailableDevices: {e}"
    if not devs:
        return False, "USB widzi kamerę, ale DepthAI nie (zasilanie? uprawnienia /dev/bus/usb?)" if usb \
            else "DepthAI nie widzi żadnej kamery"
    return True, ", ".join(f"{d['mxid'] or d['name']} ({d['state']})" for d in devs)


class CameraManager(threading.Thread):
    def __init__(self, hub: Hub, settings: Settings) -> None:
        super().__init__(daemon=True, name="camera-mgr")
        self.hub, self.s = hub, settings
        self._halt = threading.Event()
        self.proc = None
        self.fails = 0
        self.next_try = 0.0
        self.last_check: dict = {}
        self.mode: str | None = None
        # detekcja osób: wyłączana na stałe (do restartu kontenera), gdy kamera z nią nie wytrzymuje
        self.person_enabled = settings.oak_person_detect
        self.person_strikes = 0
        self.persons_active = False
        # obraz głębi / podczerwieni na ekran: tak samo wyłączany, gdy kamera z nim nie wytrzymuje
        self.vision_enabled = settings.vision_enable
        self.vision_strikes = 0
        self.vision_active = False

    def stop(self) -> None:
        self._halt.set()

    def _cfg(self) -> dict:
        s = self.s
        return {"ht_dir": os.path.join(s.vendor_dir, "depthai_hand_tracker"), "lm_model": s.oak_lm_model,
                "ir_dot": s.oak_ir_dot, "ir_always": s.oak_ir_always,
                "stream_fps": s.oak_stream_fps, "stream_width": s.stream_width,
                "jpeg_quality": s.jpeg_quality, "internal_fps": s.oak_internal_fps,
                "force_fallback": s.oak_force_fallback, "hfov": s.oak_hfov_deg,
                "near": s.hand_near_m, "far": s.hand_far_m,
                "persons": self.person_enabled, "person_model": s.person_model,
                "person_every_n": s.oak_person_every_n, "person_conf": s.oak_person_conf,
                "vision": self.vision_enabled, "vision_every_n": s.oak_vision_every_n,
                "vision_width": s.vision_width, "ir_view_dot": s.oak_ir_view_dot,
                "world_landmarks": s.oak_world_landmarks}

    def run(self) -> None:
        while not self._halt.is_set():
            try:
                now = time.monotonic()
                if now < self.next_try:
                    self._halt.wait(min(0.25, self.next_try - now))
                    continue
                present, info = oak_present()
                self.last_check = {"t": time.time(), "present": present, "info": info}
                if present:
                    self._run_worker()
                else:
                    self.fails = 0
                    msg = f"Nie wykryto kamery: {info}"
                    if self.hub.devices["oak"].state != "sim" or self.hub.devices["oak"].starting:
                        self.hub.set_device("oak", "sim", message=msg)
                    else:
                        self.hub.devices["oak"].message = msg
                    self._halt.wait(self.s.detect_interval_s)
            except Exception:
                log.exception("Błąd nadzorcy kamery")
                self._halt.wait(2.0)

    def _run_worker(self) -> None:
        from . import camera_worker

        hub, s = self.hub, self.s
        ctx = mp.get_context("spawn")
        recv, send = ctx.Pipe(duplex=False)
        ctrl_recv, ctrl_send = ctx.Pipe(duplex=False)          # rodzic -> kamera: wybór soczewki
        proc = ctx.Process(target=camera_worker.main, args=(send, self._cfg(), ctrl_recv), daemon=True, name="oak")
        hub.set_device("oak", "sim", message="Uruchamiam kamerę…", starting=True)
        proc.start()
        send.close()
        ctrl_recv.close()
        sent_lens = None
        sent_persons = None
        self.proc = proc
        spawn_t = time.monotonic()
        ready_t = 0.0
        last_msg = 0.0
        ready = False
        reason = ""
        self.persons_active = False
        self.vision_active = False
        try:
            while not self._halt.is_set():
                # widok (bramka głębi + projektor IR); projektor gaśnie poza widokiem głębi także bez bramki
                if ready and hub.lens.get("lens") != sent_lens:
                    sent_lens = hub.lens.get("lens")
                    try:
                        ctrl_send.send({"lens": sent_lens})
                    except (BrokenPipeError, OSError):
                        pass
                # wykrywanie osób: wstrzymane w grze (tam nikogo nie zaznaczamy), poza grą działa
                want = not bool((hub.game or {}).get("active"))
                if ready and self.persons_active and want != sent_persons:
                    sent_persons = want
                    try:
                        ctrl_send.send({"persons": want})
                    except (BrokenPipeError, OSError):
                        pass
                if recv.poll(0.25):
                    try:
                        msg = recv.recv()
                    except (EOFError, OSError):
                        reason = f"proces kamery zakończył się (kod {proc.exitcode})"
                        break
                    now = time.monotonic()
                    last_msg = now
                    typ = msg.get("type")
                    if typ == "ready":
                        ready, ready_t = True, now
                        self.mode = msg.get("mode")
                        self.persons_active = bool(msg.get("persons"))
                        self.vision_active = bool(msg.get("vision"))
                        sent_lens = sent_persons = None
                        self.fails = 0
                        hub.cam_info = {"mode": self.mode, "name": msg.get("name"), "usb": msg.get("usb"),
                                        "persons": self.persons_active, "vision": self.vision_active}
                        hub.set_device("oak", "real", detail=f"{msg.get('name', '')} · USB {msg.get('usb', '?')}",
                                       message=msg.get("detail", ""))
                        hub.event("info" if self.mode == "nn" else "warn",
                                  f"OAK-D Pro: {msg.get('detail', '')}")
                    elif typ == "data":
                        hub.on_hand(msg.get("hand"), now, "oak")
                        hub.on_people(msg.get("people"), now, "oak")
                        hub.cam_info["fps"] = msg.get("fps")
                        hub.cam_info["chip_c"] = msg.get("chip_c")
                        if msg.get("jpeg"):
                            hub.on_frame(msg["jpeg"], msg.get("w", 640), msg.get("h", 360))
                    elif typ == "vision":
                        if msg.get("jpeg"):
                            hub.on_vision(msg["jpeg"], msg.get("lens", "depth"), msg.get("w", 960), msg.get("h", 540))
                    elif typ == "fatal":
                        reason = str(msg.get("msg", "błąd kamery"))[:200]
                        break
                now = time.monotonic()
                if not proc.is_alive() and not recv.poll(0):
                    reason = reason or f"proces kamery zakończył się (kod {proc.exitcode})"
                    break
                if ready and now - last_msg > s.oak_no_frame_s:
                    reason = f"brak klatki od {s.oak_no_frame_s:.0f} s"
                    break
                if not ready and now - spawn_t > s.oak_boot_timeout_s:
                    reason = f"kamera nie wystartowała w {s.oak_boot_timeout_s:.0f} s"
                    break
        finally:
            self._kill(proc)
            for c in (recv, ctrl_send):
                try:
                    c.close()
                except Exception:
                    pass
            self.proc = None
            hub.clear_camera()
        if self._halt.is_set():
            return
        if "No available devices" not in reason:     # wypięty kabel to nie wina detektora
            self._person_strike(ready, ready_t)
            self._vision_strike(ready, ready_t)
        self.fails += 1
        delay = BACKOFF_S[min(self.fails - 1, len(BACKOFF_S) - 1)]
        self.next_try = time.monotonic() + delay
        log.warning("OAK-D: %s – ponowna próba za %.0f s", reason, delay)
        hub.set_device("oak", "error", message=f"{reason} – ponowna próba za {delay:.0f} s")
        hub.event("warn", f"OAK-D Pro: {reason}")

    def _person_strike(self, ready: bool, ready_t: float) -> None:
        """Kamera padła wkrótce po starcie z detekcją osób -> po 2 razach wyłącz detekcję."""
        if not self.person_enabled:
            return
        uptime = time.monotonic() - ready_t if ready else 0.0
        if ready and not self.persons_active:
            return                                  # i tak działała bez detekcji
        if uptime >= 300:
            self.person_strikes = 0
            return
        if uptime < 60:
            self.person_strikes += 1
        if self.person_strikes >= 2:
            self.person_enabled = False
            self.hub.event("warn", "OAK-D Pro: detekcja osób wyłączona – kamera z nią się nie utrzymuje")

    def _vision_strike(self, ready: bool, ready_t: float) -> None:
        """Kamera padła wkrótce po starcie z obrazem głębi -> po 2 razach wyłącz go (do restartu)."""
        if not self.vision_enabled or not (ready and self.vision_active):
            return
        uptime = time.monotonic() - ready_t
        if uptime >= 300:
            self.vision_strikes = 0
            return
        if uptime < 60:
            self.vision_strikes += 1
        if self.vision_strikes >= 2:
            self.vision_enabled = False
            self.hub.event("warn", "OAK-D Pro: obraz głębi na ekran wyłączony – kamera z nim się nie utrzymuje")

    @staticmethod
    def _kill(proc) -> None:
        if proc.is_alive():
            proc.terminate()
            proc.join(1.5)
        if proc.is_alive():
            proc.kill()
            proc.join(1.0)
