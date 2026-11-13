"""OAK-D Pro – sterownik prawdziwy (DepthAI v2) i symulowany.

Prawdziwy pipeline (wszystko ciężkie dzieje się na VPU Myriad X, RPi tylko przesyła bajty):
  ColorCamera 1080p -> ISP 1280x720 -> VideoEncoder MJPEG ──────────────> host (bez dekodowania)
               └─ preview 300x300 -> MobileNetSpatialDetectionNetwork ──> detekcje + XYZ [mm]
  MonoL/MonoR 400p -> StereoDepth (wyrównana do RGB, 640x400) ──────────> host: gesty + podgląd głębi
  Projektor kropek IR -> stabilna głębia na gładkich powierzchniach i przy słabym świetle.
"""
from __future__ import annotations

import math
import os
import time

import numpy as np

from ..adas.gestures import GestureFilter, detect_hand
from ..config import settings
from ..hub import Hub
from ..sim.world import get_world
from .common import Driver, colorize_depth, encode_jpeg, publish_frame
from .detect import depthai

VOC_PL = ["tło", "samolot", "rower", "ptak", "łódź", "butelka", "autobus", "samochód", "kot",
          "krzesło", "krowa", "stół", "pies", "koń", "motocykl", "osoba", "roślina", "owca",
          "sofa", "pociąg", "monitor"]


def _fx(width: int, hfov_deg: float) -> float:
    return (width / 2) / math.tan(math.radians(hfov_deg) / 2)


class OakReal(Driver):
    sensor, kind = "oak", "real"

    def build(self, dai, blob: str | None):
        s = settings
        p = dai.Pipeline()
        cam = p.create(dai.node.ColorCamera)
        cam.setBoardSocket(dai.CameraBoardSocket.CAM_A)
        cam.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
        cam.setIspScale(2, 3)                       # 1280x720
        cam.setInterleaved(False)
        cam.setColorOrder(dai.ColorCameraProperties.ColorOrder.BGR)
        cam.setFps(s.oak_fps)
        cam.setPreviewSize(300, 300)
        cam.setPreviewKeepAspectRatio(False)       # bbox znormalizowany = pełny kadr 16:9

        enc = p.create(dai.node.VideoEncoder)
        enc.setDefaultProfilePreset(s.oak_fps, dai.VideoEncoderProperties.Profile.MJPEG)
        enc.setQuality(80)
        cam.video.link(enc.input)
        x_mjpeg = p.create(dai.node.XLinkOut)
        x_mjpeg.setStreamName("mjpeg")
        enc.bitstream.link(x_mjpeg.input)

        mono_l = p.create(dai.node.MonoCamera)
        mono_r = p.create(dai.node.MonoCamera)
        for m, sock in ((mono_l, dai.CameraBoardSocket.CAM_B), (mono_r, dai.CameraBoardSocket.CAM_C)):
            m.setBoardSocket(sock)
            m.setResolution(dai.MonoCameraProperties.SensorResolution.THE_400_P)
            m.setFps(s.oak_fps)
        stereo = p.create(dai.node.StereoDepth)
        pm = dai.node.StereoDepth.PresetMode
        preset = getattr(pm, "HIGH_DENSITY", None) or getattr(pm, "DEFAULT", None)
        if preset is not None:
            stereo.setDefaultProfilePreset(preset)
        stereo.setLeftRightCheck(True)
        stereo.setSubpixel(False)
        stereo.setDepthAlign(dai.CameraBoardSocket.CAM_A)
        stereo.setOutputSize(640, 400)
        mono_l.out.link(stereo.left)
        mono_r.out.link(stereo.right)

        x_depth = p.create(dai.node.XLinkOut)
        x_depth.setStreamName("depth")
        if blob:
            nn = p.create(dai.node.MobileNetSpatialDetectionNetwork)
            nn.setBlobPath(blob)
            nn.setConfidenceThreshold(0.5)
            nn.input.setBlocking(False)
            nn.setBoundingBoxScaleFactor(0.4)
            nn.setDepthLowerThreshold(100)
            nn.setDepthUpperThreshold(8000)
            cam.preview.link(nn.input)
            stereo.depth.link(nn.inputDepth)
            nn.passthroughDepth.link(x_depth.input)
            x_nn = p.create(dai.node.XLinkOut)
            x_nn.setStreamName("nn")
            nn.out.link(x_nn.input)
        else:
            stereo.depth.link(x_depth.input)
        return p

    def loop(self) -> None:
        dai = depthai()
        if dai is None:
            raise RuntimeError("DepthAI niedostępne")
        s = settings
        blob = s.oak_model_path if os.path.isfile(s.oak_model_path) else None
        pipeline = self.build(dai, blob)
        filt = GestureFilter()
        hub = self.hub
        with dai.Device(pipeline) as dev:
            info = dev.getDeviceInfo()
            name = getattr(info, "name", "") or getattr(info, "getMxId", lambda: "")()
            try:
                dev.setIrLaserDotProjectorIntensity(float(s.oak_ir_dot))
            except Exception:
                try:
                    dev.setIrLaserDotProjectorBrightness(float(s.oak_ir_dot) * 1200)
                except Exception:
                    pass
            usb = str(dev.getUsbSpeed()).split(".")[-1]
            hub.set_status("oak", "real", f"{name} · USB {usb}",
                           "VPU: MobileNet-SSD + głębia" if blob else "Brak modelu NN – tylko obraz i głębia")
            hub.event("info", f"OAK-D podłączona ({name}, USB {usb})")
            q_m = dev.getOutputQueue("mjpeg", maxSize=1, blocking=False)
            q_d = dev.getOutputQueue("depth", maxSize=1, blocking=False)
            q_n = dev.getOutputQueue("nn", maxSize=1, blocking=False) if blob else None
            dets: list[dict] = []
            hand = None
            fx_d = _fx(640, s.oak_hfov_deg)
            nd = 0
            while not self.halted:
                got = False
                if q_n is not None:
                    inn = q_n.tryGet()
                    if inn is not None:
                        got = True
                        hub.nn_fps.tick()
                        dets = []
                        for d in inn.detections:
                            sc = d.spatialCoordinates
                            label = VOC_PL[d.label] if 0 <= d.label < len(VOC_PL) else str(d.label)
                            dets.append({
                                "label": label, "conf": round(float(d.confidence), 2),
                                "bbox": [round(float(d.xmin), 4), round(float(d.ymin), 4),
                                         round(float(d.xmax), 4), round(float(d.ymax), 4)],
                                "xyz": [round(sc.x / 1000, 3), round(sc.y / 1000, 3), round(sc.z / 1000, 3)],
                            })
                ind = q_d.tryGet()
                if ind is not None:
                    got = True
                    depth = ind.getFrame()
                    hand = filt.update(detect_hand(depth, fx_d, s.hand_zmin_m, s.hand_zmax_m))
                    nd += 1
                    if nd % 2 == 0:
                        img = colorize_depth(depth)
                        publish_frame(hub, "depth", encode_jpeg(img, 75), img.shape[1], img.shape[0])
                elif hand is not None:
                    hand = filt.update(None)
                inm = q_m.tryGet()
                if inm is not None:
                    got = True
                    lat = (dai.Clock.now() - inm.getTimestamp()).total_seconds() * 1000
                    jpeg = inm.getData().tobytes()
                    hub.oak = {"detections": dets, "hand": hand, "t": time.monotonic(),
                               "latency_ms": round(lat, 1), "nn": bool(blob),
                               "nn_fps": round(hub.nn_fps.value, 1)}
                    publish_frame(hub, "oak", jpeg, 1280, 720, dets=dets, hand=hand,
                                  latency_ms=round(lat, 1), seq=inm.getSequenceNum())
                    hub.status["oak"].fps.tick()
                if not got:
                    self.sleep(0.003)


class OakMock(Driver):
    sensor, kind = "oak", "mock"

    def __init__(self, hub: Hub, message: str = "") -> None:
        super().__init__(hub, "sim")
        self.message = message

    def loop(self) -> None:
        s = settings
        world = get_world()
        hub = self.hub
        hub.set_status("oak", "mock", "symulacja", self.message or "Nie wykryto OAK-D – obraz i głębia symulowane")
        cam = {"w": 960, "h": 540, "hfov": s.oak_hfov_deg, "h_m": s.camera_height_m, "yaw": 0.0, "pitch": 0.0}
        cam_d = {"w": 640, "h": 360, "hfov": s.oak_hfov_deg, "h_m": s.camera_height_m}
        fx_d = _fx(640, s.oak_hfov_deg)
        filt = GestureFilter()
        period = 1.0 / max(5, s.mock_fps)
        nxt = time.monotonic()
        i = 0
        while not self.halted:
            t0 = time.monotonic()
            world.tick()
            snap = world.snapshot()
            img = world.render(cam, "")  # podgląd jest lustrzany – bez napisu w obrazie
            jpeg = encode_jpeg(img, 80)
            depth = world.render_depth(cam_d)
            hand = filt.update(detect_hand(depth, fx_d, s.hand_zmin_m, s.hand_zmax_m))
            dets = []
            for p in world.camera_people(cam, snap):
                conf = 0.86 + 0.08 * math.sin(t0 * 0.7 + p["pid"] * 1.7)
                dets.append({"label": "osoba", "conf": round(conf, 2),
                             "bbox": [round(v, 4) for v in p["bbox"]],
                             "xyz": [round(v, 3) for v in p["xyz"]]})
            hub.nn_fps.tick()
            lat = (time.monotonic() - t0) * 1000 + 18.0
            hub.oak = {"detections": dets, "hand": hand, "t": time.monotonic(), "latency_ms": round(lat, 1),
                       "nn": True, "nn_fps": round(hub.nn_fps.value, 1), "sim_phase": snap["phase"]}
            publish_frame(hub, "oak", jpeg, cam["w"], cam["h"], dets=dets, hand=hand,
                          latency_ms=round(lat, 1), seq=i)
            if i % 2 == 0:
                dimg = colorize_depth(depth)
                publish_frame(hub, "depth", encode_jpeg(dimg, 75), dimg.shape[1], dimg.shape[0])
            hub.status["oak"].fps.tick()
            i += 1
            nxt += period
            if self.sleep(nxt - time.monotonic()):
                break
            if nxt < time.monotonic() - 0.5:
                nxt = time.monotonic()
