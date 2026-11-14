"""Proces kamery OAK-D Pro (uruchamiany przez multiprocessing "spawn").

Biblioteka natywna DepthAI działa tu, w OSOBNYM procesie: jej zawieszenie się
(np. blokujące get() po wypięciu kabla) nie blokuje serwera – nadzorca w procesie
głównym po prostu ubija ten proces i uruchamia go ponownie.

Tryb główny: geaxgx/depthai_hand_tracker w trybie Edge (MediaPipe Hands na VPU,
model landmarków "lite", xyz z głębi stereo, gesty). Zapas: gdy model się nie
załaduje – kolor + głębia stereo, a dłoń = najbliższy obiekt w strefie interakcji.

Do rodzica wysyłane są słowniki przez Pipe:
  {"type": "ready", "mode": "nn"|"depth", "name": .., "usb": ..}
  {"type": "data", "hand": {...} | None, "jpeg": bytes | None, "w":, "h":, "fps":}
  {"type": "fatal", "msg": ..}
"""
from __future__ import annotations

import logging
import math
import os
import sys
import time

log = logging.getLogger("camera")

PALM_IDX = (0, 5, 9, 13, 17)
PALM_LEN_M = 0.09            # nadgarstek -> podstawa palca środkowego, do awaryjnej odległości


class ParentGone(Exception):
    """Proces główny zniknął – kończymy bez prób trybu zapasowego."""


def _device_name(device) -> str:
    for fn in ("getMxId", "getDeviceId"):
        try:
            return str(getattr(device, fn)())
        except Exception:
            continue
    return ""


def _models_present(ht_dir: str) -> bool:
    need = ("models/palm_detection_sh4.blob", "models/hand_landmark_lite_sh4.blob",
            "custom_models/PDPostProcessing_top2_sh1.blob")
    return all(os.path.isfile(os.path.join(ht_dir, n)) for n in need)


def _set_ir(device, level: float) -> str:
    level = max(0.0, min(1.0, float(level)))
    try:
        device.setIrLaserDotProjectorIntensity(level)
        return f"projektor IR {level:.0%}"
    except Exception:
        pass
    try:
        device.setIrLaserDotProjectorBrightness(level * 1200.0)
        return f"projektor IR {level * 1200:.0f} mA"
    except Exception as e:
        return f"projektor IR niedostępny ({type(e).__name__})"


class _Sender:
    def __init__(self, conn, cfg: dict) -> None:
        import cv2
        self.cv2 = cv2
        self.conn = conn
        self.width = int(cfg.get("stream_width", 640))
        self.quality = int(cfg.get("jpeg_quality", 78))
        self.period = 1.0 / max(1.0, float(cfg.get("stream_fps", 15.0)))
        self.last_jpeg = 0.0
        self.ppid = os.getppid()
        self._fps_t = time.monotonic()
        self._fps_n = 0
        self.fps = 0.0

    def send(self, hand: dict | None, frame, people: list[dict] | None = None) -> None:
        if os.getppid() != self.ppid:       # rodzic zginął – kończymy
            raise ParentGone()
        now = time.monotonic()
        self._fps_n += 1
        if now - self._fps_t >= 1.0:
            self.fps = self._fps_n / (now - self._fps_t)
            self._fps_t, self._fps_n = now, 0
        msg = {"type": "data", "hand": hand, "jpeg": None, "fps": round(self.fps, 1), "people": people}
        if frame is not None and now - self.last_jpeg >= self.period:
            self.last_jpeg = now
            h, w = frame.shape[:2]
            if w != self.width:
                nh = int(round(h * self.width / w))
                frame = self.cv2.resize(frame, (self.width, nh), interpolation=self.cv2.INTER_AREA)
            ok, buf = self.cv2.imencode(".jpg", frame, [self.cv2.IMWRITE_JPEG_QUALITY, self.quality])
            if ok:
                msg["jpeg"] = buf.tobytes()
                msg["h"], msg["w"] = frame.shape[:2]
        self.conn.send(msg)


def _hand_from_tracker(hand, img_w: int, img_h: int, hfov_deg: float) -> dict:
    lm = hand.landmarks.astype(float)
    norm = [[round(float(x) / img_w, 4), round(float(y) / img_h, 4)] for x, y in lm]
    palm = lm[list(PALM_IDX)]
    x_norm = float(palm[:, 0].mean()) / img_w
    y_norm = float(palm[:, 1].mean()) / img_h
    dist = None
    src = "stereo"
    xyz = getattr(hand, "xyz", None)
    if xyz is not None and len(xyz) == 3 and float(xyz[2]) > 50:
        dist = float(xyz[2]) / 1000.0
    else:
        # awaryjnie: odległość z rozmiaru dłoni w pikselach (model otworkowy)
        palm_px = math.hypot(*(lm[9] - lm[0]))
        if palm_px > 4:
            fx = (img_w / 2) / math.tan(math.radians(hfov_deg) / 2)
            dist = fx * PALM_LEN_M / palm_px
            src = "rozmiar"
    return {"present": True, "gesture": getattr(hand, "gesture", None),
            "dist_m": None if dist is None else round(dist, 3), "dist_src": src,
            "x_norm": round(x_norm, 4), "y_norm": round(y_norm, 4),
            "landmarks": norm, "bbox": None, "score": round(float(getattr(hand, "lm_score", 0.0)), 2)}


PERSON_LABEL = 15            # PASCAL VOC: "person"
PERSON_STREAM = "persons"


def add_person_detection(dai, pipeline, blob_path: str, every_n: int = 4, conf: float = 0.5) -> None:
    """Dokleja detektor osób do istniejącego potoku (kamera kolorowa + opcjonalnie głębia stereo).

    Co N-ta klatka podglądu -> Script (przepuszcza) -> ImageManip 300x300 -> MobileNet-SSD
    (4 SHAVE, z odległością 3D z głębi stereo, jeśli jest) -> XLinkOut "persons".
    Ograniczenie częstotliwości chroni śledzenie dłoni, które dzieli ten sam VPU.
    """
    nodes = pipeline.getAllNodes()
    cam = next(n for n in nodes if isinstance(n, dai.node.ColorCamera))
    stereo = next((n for n in nodes if isinstance(n, dai.node.StereoDepth)), None)

    throttle = pipeline.create(dai.node.Script)
    throttle.setScript(
        "n = 0\n"
        "while True:\n"
        "    f = node.io['in'].get()\n"
        "    n += 1\n"
        f"    if n % {int(every_n)} == 0:\n"
        "        node.io['out'].send(f)\n")
    throttle.inputs["in"].setBlocking(False)
    throttle.inputs["in"].setQueueSize(1)
    cam.preview.link(throttle.inputs["in"])

    manip = pipeline.create(dai.node.ImageManip)
    manip.initialConfig.setResize(300, 300)
    manip.initialConfig.setKeepAspectRatio(False)          # cały kadr -> ramki w układzie pełnego obrazu
    manip.initialConfig.setFrameType(dai.ImgFrame.Type.BGR888p)
    manip.setMaxOutputFrameSize(300 * 300 * 3)
    manip.inputImage.setBlocking(False)
    manip.inputImage.setQueueSize(1)
    throttle.outputs["out"].link(manip.inputImage)

    if stereo is not None:
        nn = pipeline.create(dai.node.MobileNetSpatialDetectionNetwork)
        nn.setBoundingBoxScaleFactor(0.3)
        nn.setDepthLowerThreshold(200)
        nn.setDepthUpperThreshold(8000)
        nn.inputDepth.setBlocking(False)
        nn.inputDepth.setQueueSize(1)
        stereo.depth.link(nn.inputDepth)
    else:
        nn = pipeline.create(dai.node.MobileNetDetectionNetwork)
    nn.setBlobPath(blob_path)
    nn.setConfidenceThreshold(conf)
    nn.setNumInferenceThreads(1)
    nn.input.setBlocking(False)
    nn.input.setQueueSize(1)
    manip.out.link(nn.input)

    xout = pipeline.create(dai.node.XLinkOut)
    xout.setStreamName(PERSON_STREAM)
    xout.input.setBlocking(False)
    xout.input.setQueueSize(1)
    nn.out.link(xout.input)


def people_from_detections(msg) -> list[dict]:
    out = []
    for d in msg.detections:
        if int(d.label) != PERSON_LABEL:
            continue
        sc = getattr(d, "spatialCoordinates", None)
        z = float(sc.z) / 1000.0 if sc is not None and float(sc.z) > 0 else None
        c = lambda v: round(min(1.0, max(0.0, float(v))), 4)  # noqa: E731
        out.append({"bbox": [c(d.xmin), c(d.ymin), c(d.xmax), c(d.ymax)], "conf": round(float(d.confidence), 2),
                    "dist_m": None if z is None else round(z, 2)})
    return out


class CameraStarted:
    """Flaga: czy kamera zdążyła wystartować (błąd PO starcie = awaria, nie zły model)."""

    def __init__(self) -> None:
        self.ok = False


def _run_tracker(dai, conn, cfg: dict, started: CameraStarted, persons: bool, note: str = "") -> None:
    ht_dir = cfg["ht_dir"]
    if ht_dir not in sys.path:
        sys.path.insert(0, ht_dir)
    from HandTrackerEdge import HandTracker  # type: ignore  # noqa: E402

    class SafeHandTracker(HandTracker):
        """Zamyka urządzenie, jeśli konstruktor padnie w połowie (np. zły blob, brak zasobów VPU)."""

        def __init__(self, *a, **k):
            try:
                super().__init__(*a, **k)
            except BaseException:
                dev = getattr(self, "device", None)
                if dev is not None:
                    try:
                        dev.close()
                    except Exception:
                        pass
                raise

        def create_pipeline(self):
            p = super().create_pipeline()
            if persons:
                add_person_detection(dai, p, cfg["person_model"], cfg.get("person_every_n", 4),
                                     cfg.get("person_conf", 0.5))
            return p

    kwargs = dict(input_src="rgb", lm_model=cfg.get("lm_model", "lite"), xyz=True, use_gesture=True,
                  solo=True, resolution="full", internal_frame_height=540, stats=False, trace=0)
    if cfg.get("internal_fps"):
        kwargs["internal_fps"] = int(cfg["internal_fps"])
    tracker = SafeHandTracker(**kwargs)
    try:
        ir = _set_ir(tracker.device, cfg.get("ir_dot", 0.3))
        name = _device_name(tracker.device)
        usb = str(tracker.device.getUsbSpeed()).split(".")[-1]
        pq = tracker.device.getOutputQueue(PERSON_STREAM, maxSize=1, blocking=False) if persons else None
        started.ok = True
        conn.send({"type": "ready", "mode": "nn", "name": name, "usb": usb, "persons": persons,
                   "detail": f"MediaPipe Hands na VPU · {ir} · xyz={'tak' if tracker.xyz else 'nie'} · "
                             f"detekcja osób: {'tak' if persons else 'nie'}{(' (' + note + ')') if note else ''}"})
        sender = _Sender(conn, cfg)
        img_w, img_h = tracker.img_w, tracker.img_h
        while True:
            frame, hands, _ = tracker.next_frame()
            if frame is None:
                raise RuntimeError("kamera zakończyła strumień")
            hand = None
            if hands:
                best = max(hands, key=lambda h: float(getattr(h, "lm_score", 0.0)))
                hand = _hand_from_tracker(best, img_w, img_h, cfg.get("hfov", 69.0))
            people = None
            if pq is not None:
                det = pq.tryGet()
                if det is not None:
                    people = people_from_detections(det)
            sender.send(hand, frame, people)
    finally:
        try:
            tracker.exit()
        except Exception:
            pass


FB_W, FB_H = 640, 360


def build_fallback_pipeline(dai):
    """Kolor 640x360 + głębia stereo wyrównana do koloru (bez sieci neuronowej)."""
    W, H = FB_W, FB_H
    p = dai.Pipeline()
    cam = p.create(dai.node.ColorCamera)
    cam.setBoardSocket(dai.CameraBoardSocket.CAM_A)
    cam.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
    cam.setIspScale(1, 3)
    cam.setVideoSize(W, H)
    cam.setInterleaved(False)
    cam.setFps(20)
    xv = p.create(dai.node.XLinkOut)
    xv.setStreamName("video")
    xv.input.setBlocking(False)
    xv.input.setQueueSize(1)
    cam.video.link(xv.input)

    mono_l = p.create(dai.node.MonoCamera)
    mono_r = p.create(dai.node.MonoCamera)
    for m, sock in ((mono_l, dai.CameraBoardSocket.CAM_B), (mono_r, dai.CameraBoardSocket.CAM_C)):
        m.setBoardSocket(sock)
        m.setResolution(dai.MonoCameraProperties.SensorResolution.THE_400_P)
        m.setFps(20)
    stereo = p.create(dai.node.StereoDepth)
    preset = getattr(dai.node.StereoDepth.PresetMode, "HIGH_DENSITY", None)
    if preset is not None:
        stereo.setDefaultProfilePreset(preset)
    stereo.setLeftRightCheck(True)
    stereo.setSubpixel(False)
    stereo.setDepthAlign(dai.CameraBoardSocket.CAM_A)
    stereo.setOutputSize(W, H)
    mono_l.out.link(stereo.left)
    mono_r.out.link(stereo.right)
    xd = p.create(dai.node.XLinkOut)
    xd.setStreamName("depth")
    xd.input.setBlocking(False)
    xd.input.setQueueSize(1)
    stereo.depth.link(xd.input)
    return p


def _run_depth_fallback(dai, conn, cfg: dict, reason: str) -> None:
    from ..logic.depth_hand import GestureFilter, detect_hand

    p = build_fallback_pipeline(dai)
    fx = (FB_W / 2) / math.tan(math.radians(cfg.get("hfov", 69.0)) / 2)
    near, far = float(cfg.get("near", 0.35)), float(cfg.get("far", 1.2))
    filt = GestureFilter()
    with dai.Device(p) as dev:
        ir = _set_ir(dev, max(0.3, float(cfg.get("ir_dot", 0.3))))   # głębia potrzebuje projektora
        name = _device_name(dev)
        usb = str(dev.getUsbSpeed()).split(".")[-1]
        log.error("!!! TRYB ZAPASOWY KAMERY: %s – gaz z najbliższego obiektu w strefie głębi", reason)
        conn.send({"type": "ready", "mode": "depth", "name": name, "usb": usb, "persons": False,
                   "detail": f"TRYB ZAPASOWY (bez sieci NN): {reason} · {ir}"})
        qv = dev.getOutputQueue("video", maxSize=1, blocking=False)
        qd = dev.getOutputQueue("depth", maxSize=1, blocking=False)
        sender = _Sender(conn, cfg)
        frame = None
        while True:
            got = False
            iv = qv.tryGet()
            if iv is not None:
                frame = iv.getCvFrame()
                got = True
            idp = qd.tryGet()
            if idp is not None:
                depth = idp.getFrame()
                obs = filt.update(detect_hand(depth, fx, near, far))
                hand = None
                if obs is not None:
                    hand = {"present": True, "gesture": "FIST" if obs["gesture"] == "FIST" else "FIVE",
                            "dist_m": float(obs["z"]), "dist_src": "stereo",
                            "x_norm": round(float(obs["center"][0]), 4),
                            "y_norm": round(float(obs["center"][1]), 4),
                            "landmarks": None, "bbox": [round(float(v), 4) for v in obs["bbox"]],
                            "score": 0.0}
                sender.send(hand, frame)
                frame = None
                got = True
            if not got:
                time.sleep(0.004)


def main(conn, cfg: dict) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s camera-proc   %(message)s",
                        datefmt="%H:%M:%S")
    try:
        import depthai as dai
    except Exception as e:
        conn.send({"type": "fatal", "msg": f"DepthAI niedostępne: {e}"})
        return
    reason = ""
    try:
        if cfg.get("force_fallback"):
            reason = "OAK_FORCE_FALLBACK=1"
        elif not _models_present(cfg["ht_dir"]):
            reason = "brak plików modelu dłoni (.blob)"
        else:
            # kolejne próby: z detekcją osób -> bez niej -> (niżej) tryb zapasowy bez sieci NN
            want = bool(cfg.get("persons"))
            have = want and os.path.isfile(cfg.get("person_model", ""))
            attempts = [(True, "")] if have else []
            attempts.append((False, "" if not want else ("brak pliku modelu osób" if not have else "")))
            last = ""
            for use_persons, note in attempts:
                started = CameraStarted()
                if not use_persons and last and want:
                    note = f"wyłączona – VPU nie uruchomił potoku: {last[:80]}"
                try:
                    _run_tracker(dai, conn, cfg, started, use_persons, note)
                    return
                except (KeyboardInterrupt, BrokenPipeError, EOFError, ParentGone):
                    raise
                except BaseException as e:      # HandTracker woła też sys.exit() przy błędach
                    last = f"{type(e).__name__}: {e}"
                    if started.ok or "No available devices" in str(e):
                        # awaria po starcie (np. wypięty kabel) albo brak kamery – restart zrobi rodzic
                        conn.send({"type": "fatal", "msg": last})
                        return
                    log.error("Start kamery%s nieudany: %s", " z detekcją osób" if use_persons else "", last)
                    time.sleep(1.5)             # urządzenie potrzebuje chwili po zamknięciu
            reason = f"model dłoni nie wystartował ({last[:120]})"
        _run_depth_fallback(dai, conn, cfg, reason)
    except (BrokenPipeError, EOFError, ParentGone):
        return
    except BaseException as e:
        try:
            conn.send({"type": "fatal", "msg": f"{type(e).__name__}: {e}"})
        except Exception:
            pass
