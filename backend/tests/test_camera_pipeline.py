"""OAK-D bez urządzenia: prawdziwy potok DepthAI 2.x z dołączonego hand trackera
i pełna ścieżka host -> proces kamery -> słownik dłoni, na atrapie urządzenia.

Sprawdza to, czego nie da się złapać importem: czy dołączony kod hand trackera
buduje potok w tej wersji DepthAI (nazwy API, pliki .blob, skrypt menedżera),
i czy nasz kod poprawnie czyta jego wyniki (punkty, gest, xyz).
"""
import marshal
import os

import numpy as np
import pytest

dai = pytest.importorskip("depthai")

from app.config import settings  # noqa: E402
from app.hw import camera_worker  # noqa: E402

VENDOR = settings.vendor_dir
if not os.path.isdir(VENDOR):
    VENDOR = os.path.join(os.path.dirname(__file__), "..", "..", "vendor")
VENDOR = os.path.abspath(VENDOR)
HT_DIR = os.path.join(VENDOR, "depthai_hand_tracker")
PERSON_BLOB = os.path.join(VENDOR, "models", "mobilenet-ssd_openvino_2021.4_4shave.blob")


def open_hand_rrn():
    """21 punktów (x, y, z) otwartej dłoni w obróconym prostokącie (palce do góry)."""
    pts = [(0.5, 0.9, 0)]
    pts += [(0.35, 0.8, 0), (0.25, 0.7, 0), (0.17, 0.62, 0), (0.10, 0.55, 0)]          # kciuk
    for x in (0.40, 0.50, 0.60, 0.70):                                                  # palce
        pts += [(x, 0.60, 0), (x, 0.45, 0), (x, 0.35, 0), (x, 0.25, 0)]
    return [c for p in pts for c in p]


class FakeMsg:
    def __init__(self, frame=None, data=None):
        self._f, self._d = frame, data

    def getCvFrame(self):
        return self._f

    def getData(self):
        return self._d


class FakeQueue:
    def __init__(self, items):
        self.items = list(items)

    def get(self):
        if not self.items:
            raise RuntimeError("Communication exception - possible device error/misconfiguration")
        return self.items.pop(0)

    def tryGet(self):
        return self.items.pop(0) if self.items else None


class FakeDet:
    """Jak dai.SpatialImgDetection: etykieta VOC, pewność, ramka 0..1, xyz [mm]."""

    def __init__(self, label, conf, box, z_mm):
        self.label, self.confidence = label, conf
        self.xmin, self.ymin, self.xmax, self.ymax = box
        self.spatialCoordinates = type("C", (), {"x": 0.0, "y": 0.0, "z": z_mm})()


class FakeDets:
    def __init__(self, dets):
        self.detections = dets


class FakeDevice:
    """Udaje dai.Device(): przyjmuje prawdziwy potok, zwraca przygotowane wiadomości."""
    started = None
    frames = 3

    def __init__(self, *a, **k):
        self.closed = False
        img_w, img_h, frame_size, pad_h = 960, 540, 960, 210
        res = {"lm_score": [0.93], "handedness": [0.8], "rect_center_x": [0.5], "rect_center_y": [0.5],
               "rect_size": [0.3], "rotation": [0.0], "rrn_lms": [open_hand_rrn()],
               "sqn_lms": [[v for i in range(21) for v in (0.30 + 0.01 * i, (pad_h + 200 + 3 * i) / frame_size)]],
               "world_lms": [0], "xyz": [[-80.0, 30.0, 620.0]], "xyz_zone": [[0, 0, 10, 10]],
               "pd_inf": 1, "nb_lm_inf": 1}
        self.q = {"cam_out": FakeQueue(FakeMsg(frame=np.zeros((img_h, img_w, 3), np.uint8)) for _ in range(self.frames)),
                  "manager_out": FakeQueue(FakeMsg(data=marshal.dumps(res)) for _ in range(self.frames)),
                  # osoba (15) i samochód (7, ma zostać odrzucony), ramka trochę poza obrazem
                  "persons": FakeQueue([FakeDets([FakeDet(15, 0.91, (0.2, -0.05, 0.6, 1.02), 1430.0),
                                                  FakeDet(7, 0.8, (0.7, 0.1, 0.9, 0.3), 3000.0)])])}

    def getConnectedCameras(self):
        return [dai.CameraBoardSocket.CAM_A, dai.CameraBoardSocket.CAM_B, dai.CameraBoardSocket.CAM_C]

    def getUsbSpeed(self):
        return dai.UsbSpeed.SUPER

    def readCalibration(self):
        class C:
            def getLensPosition(self, sock):
                return 130
        return C()

    def startPipeline(self, pipeline):
        FakeDevice.started = pipeline
        pipeline.serializeToJson()              # potok musi dać się zserializować

    def getOutputQueue(self, name, maxSize=1, blocking=False):
        return self.q[name]

    def setIrLaserDotProjectorIntensity(self, v):
        self.ir = v

    def getMxId(self):
        return "FAKE-MXID"

    def close(self):
        self.closed = True


class Conn:
    def __init__(self):
        self.msgs = []

    def send(self, m):
        self.msgs.append(m)


def test_vendored_models_present():
    assert camera_worker._models_present(HT_DIR)


CFG = {"ht_dir": HT_DIR, "lm_model": "lite", "ir_dot": 0.3, "stream_fps": 1000, "stream_width": 640,
       "jpeg_quality": 70, "internal_fps": 0, "hfov": 69.0,
       "persons": True, "person_model": PERSON_BLOB, "person_every_n": 4, "person_conf": 0.5}


def node_names(p):
    return [n.getName() for n in p.getAllNodes()]


def has_person_nn(p):
    return any(isinstance(n, dai.node.MobileNetSpatialDetectionNetwork) for n in p.getAllNodes())


def test_hand_tracker_pipeline_and_host_path(monkeypatch):
    monkeypatch.setattr(dai, "Device", FakeDevice)
    conn = Conn()
    started = camera_worker.CameraStarted()
    with pytest.raises(RuntimeError, match="Communication exception"):
        camera_worker._run_tracker(dai, conn, CFG, started, persons=False)   # koniec = atrapa "odłącza" kamerę
    assert started.ok
    assert FakeDevice.started is not None
    assert not has_person_nn(FakeDevice.started)
    ready = conn.msgs[0]
    assert ready["type"] == "ready" and ready["mode"] == "nn" and "xyz=tak" in ready["detail"]
    assert ready["persons"] is False
    data = [m for m in conn.msgs if m["type"] == "data"]
    assert len(data) == FakeDevice.frames
    hand = data[0]["hand"]
    assert hand["gesture"] == "FIVE"
    assert hand["dist_m"] == pytest.approx(0.62)
    assert len(hand["landmarks"]) == 21
    assert all(0.0 <= x <= 1.0 and 0.0 <= y <= 1.0 for x, y in hand["landmarks"])
    assert data[0]["jpeg"][:2] == b"\xff\xd8" and data[0]["w"] == 640 and data[0]["h"] == 360


def test_person_detection_added_to_hand_tracker_pipeline(monkeypatch):
    """Detektor osób doklejony do potoku hand trackera: węzły, głębia 3D, odczyt wyników."""
    assert os.path.isfile(PERSON_BLOB)
    monkeypatch.setattr(dai, "Device", FakeDevice)
    conn = Conn()
    with pytest.raises(RuntimeError, match="Communication exception"):
        camera_worker._run_tracker(dai, conn, CFG, camera_worker.CameraStarted(), persons=True)
    assert has_person_nn(FakeDevice.started)
    assert node_names(FakeDevice.started).count("Script") == 2   # menedżer hand trackera + ogranicznik klatek
    assert conn.msgs[0]["persons"] is True
    data = [m for m in conn.msgs if m["type"] == "data"]
    people = data[0]["people"]
    assert people == [{"bbox": [0.2, 0.0, 0.6, 1.0], "conf": 0.91, "dist_m": 1.43}]   # bez samochodu, przycięte
    assert data[1]["people"] is None                          # brak nowego wyniku = brak aktualizacji
    assert data[0]["hand"]["gesture"] == "FIVE"               # dłoń nadal działa obok osób


def test_main_retries_without_person_detection(monkeypatch):
    """Gdy VPU nie uruchomi potoku z detektorem osób – kamera startuje bez niego, a nie w trybie zapasowym."""

    class NoRoomDevice(FakeDevice):
        def startPipeline(self, pipeline):
            if has_person_nn(pipeline):
                raise RuntimeError("Not enough resources (SHAVE) for the pipeline")
            super().startPipeline(pipeline)

    monkeypatch.setattr(dai, "Device", NoRoomDevice)
    monkeypatch.setattr(camera_worker.time, "sleep", lambda s: None)
    conn = Conn()
    camera_worker.main(conn, dict(CFG))
    ready = [m for m in conn.msgs if m["type"] == "ready"]
    assert len(ready) == 1
    assert ready[0]["mode"] == "nn" and ready[0]["persons"] is False
    assert "wyłączona" in ready[0]["detail"]
    assert conn.msgs[-1]["type"] == "fatal"                  # po "odłączeniu" atrapy proces kończy się
    assert len([m for m in conn.msgs if m["type"] == "data"]) == FakeDevice.frames


def test_manager_script_compiles(monkeypatch):
    """Kod skryptu, który trafia na VPU, musi być poprawnym Pythonem po podstawieniu szablonu."""
    import sys
    sys.path.insert(0, HT_DIR)
    from HandTrackerEdge import HandTracker
    monkeypatch.setattr(dai, "Device", FakeDevice)
    ht = HandTracker(input_src="rgb", lm_model="lite", xyz=True, use_gesture=True, solo=True,
                     resolution="full", internal_frame_height=540)
    compile(ht.build_manager_script(), "manager_script", "exec")
    assert (ht.img_w, ht.img_h) == (960, 540)


def test_fallback_pipeline_builds():
    p = camera_worker.build_fallback_pipeline(dai)
    p.serializeToJson()
    names = {n.getName() for n in p.getAllNodes()}
    assert {"ColorCamera", "StereoDepth", "MonoCamera", "XLinkOut"} <= names
