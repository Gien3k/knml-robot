"""Wątek symulacji: kamera (~15 Hz), LiDAR (10 Hz) i koła z bezwładnością."""
from __future__ import annotations

import logging
import math
import threading
import time

import cv2

from ..config import Settings
from ..hub import Hub
from ..logic.vision import colorize_depth, ir_view
from .world import SimWorld

log = logging.getLogger("sim")


class SimRunner(threading.Thread):
    def __init__(self, hub: Hub, settings: Settings) -> None:
        super().__init__(daemon=True, name="sim")
        self.hub, self.s = hub, settings
        self._halt = threading.Event()
        self.world = SimWorld(settings.cam_offset_x, settings.cam_offset_y,
                              settings.sim_camera_height_m, settings.oak_hfov_deg, mirror=settings.oak_mirror)

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        try:
            self._loop()
        except Exception:
            log.exception("Symulacja zakończona błędem")

    def _loop(self) -> None:
        hub, s, world = self.hub, self.s, self.world
        cam_period = 1.0 / max(1.0, s.sim_fps)
        now = time.monotonic()
        next_cam = next_lidar = now
        last = now
        wl = wr = 0.0
        W = s.stream_width
        H = int(round(W * 9 / 16))
        while not self._halt.is_set():
            now = time.monotonic()
            dt = min(0.1, now - last)
            last = now
            world.game = hub.game
            world.screen_sign = 1.0 if hub.mirror else -1.0          # symulowany gracz celuje w tę samą kartę
            world.tick(now)

            if now >= next_cam:
                lens = hub.lens.get("lens") if s.vision_enable else "color"
                extras = lens in ("depth", "ir")
                img, hand, people = world.render(W, H, extras=extras)
                ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, s.jpeg_quality])
                if ok and not self._halt.is_set():
                    hub.on_frame(buf.tobytes(), W, H)
                    t = time.monotonic()
                    hub.on_hand(hand, t, "sim")
                    hub.on_people(people, t, "sim")
                if extras and not self._halt.is_set():
                    vis = (colorize_depth(world.last_depth, out_w=s.vision_width) if lens == "depth"
                           else ir_view(world.last_ir, out_w=s.vision_width))
                    ok, vbuf = cv2.imencode(".jpg", vis, [cv2.IMWRITE_JPEG_QUALITY, max(80, s.jpeg_quality)])
                    if ok:
                        hub.on_vision(vbuf.tobytes(), lens, vis.shape[1], vis.shape[0])
                next_cam += cam_period
                if next_cam < now:
                    next_cam = now + cam_period
            if now >= next_lidar and not self._halt.is_set():
                hub.on_scan(world.lidar_scan(), time.monotonic(), "sim", 10.0)
                next_lidar += 0.1
                if next_lidar < now:
                    next_lidar = now + 0.1

            # koła w powietrzu: inercja pierwszego rzędu
            # (tak jak prawdziwy sterownik ESP32: aktywna tarcza = zero od razu)
            cmd = hub.drive_cmd
            fresh = cmd.get("allowed") and now - cmd.get("t", 0.0) <= s.perception_timeout_s \
                and not hub.shield_state.active
            tl, tr = (cmd["L"], cmd["R"]) if fresh else (0.0, 0.0)
            a = 1.0 - math.exp(-dt / 0.08)
            wl += (tl - wl) * a
            wr += (tr - wr) * a
            hub.on_wheels(round(wl, 4), round(wr, 4), "sim", now)
            hub.esp32 = {"t": now, "voltage": round(12.1 - 0.35 * (abs(wl) + abs(wr)), 2)}

            self._halt.wait(max(0.0, min(next_cam, next_lidar, now + 0.02) - time.monotonic()))
