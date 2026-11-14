"""Konfiguracja ze zmiennych środowiskowych (plik .env -> docker compose).

Każda wartość ma bezpieczną wartość domyślną, więc brak pliku .env jest w porządku.
Pełna lista z opisami: README.md i .env.example.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _raw(name: str, *aliases: str) -> str | None:
    for key in (name, *aliases):
        v = os.getenv(key)
        if v is None:
            continue
        # obrona przed komentarzem w tej samej linii ("X=1   # opis")
        v = v.split(" #", 1)[0].strip()
        if v:
            return v
    return None


def _str(name: str, default: str, *aliases: str) -> str:
    v = _raw(name, *aliases)
    return default if v is None else v


def _float(name: str, default: float, *aliases: str) -> float:
    v = _raw(name, *aliases)
    try:
        return default if v is None else float(v.replace(",", "."))
    except ValueError:
        return default


def _bool(name: str, default: bool, *aliases: str) -> bool:
    v = _raw(name, *aliases)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on", "tak")


def _hw_mode() -> str:
    v = _str("HW_MODE", "auto").lower()
    if v == "mock":
        return "sim"
    return v if v in ("auto", "sim", "real") else "auto"


@dataclass(frozen=True)
class Settings:
    # --- ogólne ---
    hw_mode: str = field(default_factory=_hw_mode)                  # auto | sim | real
    static_dir: str = field(default_factory=lambda: _str("STATIC_DIR", "/app/static"))
    assets_dir: str = field(default_factory=lambda: _str("ASSETS_DIR", "/app/assets"))
    data_dir: str = field(default_factory=lambda: _str("DATA_DIR", "/app/data"))       # kalibracja itp.
    calib_password: str = field(default_factory=lambda: _str("CALIB_PASSWORD", "KNML"))
    vendor_dir: str = field(default_factory=lambda: _str("VENDOR_DIR", "/app/vendor"))
    detect_interval_s: float = field(default_factory=lambda: _float("DETECT_INTERVAL_S", 2.0))

    # --- interakcja (dłoń -> koła) ---
    hand_near_m: float = field(default_factory=lambda: _float("HAND_NEAR_M", 0.35))
    hand_far_m: float = field(default_factory=lambda: _float("HAND_FAR_M", 1.20))
    min_throttle: float = field(default_factory=lambda: _float("MIN_THROTTLE", 0.25))
    hand_ema: float = field(default_factory=lambda: _float("HAND_EMA", 0.35))
    enable_reverse: bool = field(default_factory=lambda: _bool("ENABLE_REVERSE", True))
    reverse_scale: float = field(default_factory=lambda: _float("REVERSE_SCALE", 0.6))
    attract: bool = field(default_factory=lambda: _bool("ATTRACT", False))
    attract_interval_s: float = field(default_factory=lambda: _float("ATTRACT_INTERVAL_S", 20.0))
    attract_duration_s: float = field(default_factory=lambda: _float("ATTRACT_DURATION_S", 1.5))
    attract_speed: float = field(default_factory=lambda: _float("ATTRACT_SPEED", 0.3))  # ułamek MAX_SPEED
    perception_timeout_s: float = field(default_factory=lambda: _float("PERCEPTION_TIMEOUT_S", 0.30))

    # --- napęd ---
    max_speed: float = field(default_factory=lambda: _float("MAX_SPEED", 0.5))          # m/s, {"T":1}
    ramp_up_s: float = field(default_factory=lambda: _float("RAMP_UP_S", 0.6))           # 0 -> max
    drive_without_lidar: bool = field(default_factory=lambda: _bool("DRIVE_WITHOUT_LIDAR", False))

    # --- tarcza LiDAR ---
    shield_radius_m: float = field(default_factory=lambda: _float("SHIELD_RADIUS", 0.30, "SHIELD_RADIUS_M"))
    shield_margin_m: float = field(default_factory=lambda: _float("SHIELD_MARGIN_M", 0.10))
    shield_hysteresis_m: float = field(default_factory=lambda: _float("SHIELD_HYSTERESIS_M", 0.10))
    shield_release_s: float = field(default_factory=lambda: _float("SHIELD_RELEASE_S", 0.5))
    shield_learn_s: float = field(default_factory=lambda: _float("SHIELD_LEARN_S", 5.0))
    lidar_stale_s: float = field(default_factory=lambda: _float("LIDAR_STALE_S", 0.5))

    # --- LiDAR D500 (LD19 / STL-19P) ---
    lidar_port: str = field(default_factory=lambda: _str("LIDAR_PORT", ""))
    lidar_angle_offset_deg: float = field(default_factory=lambda: _float("LIDAR_ANGLE_OFFSET_DEG", 0.0))
    lidar_clockwise: bool = field(default_factory=lambda: _bool("LIDAR_CLOCKWISE", True))
    lidar_min_intensity: int = field(default_factory=lambda: int(_float("LIDAR_MIN_INTENSITY", 15)))
    lidar_min_range_m: float = field(default_factory=lambda: _float("LIDAR_MIN_RANGE_M", 0.03))
    lidar_max_range_m: float = field(default_factory=lambda: _float("LIDAR_MAX_RANGE_M", 12.0))

    # --- ESP32 (Waveshare) ---
    esp32_port: str = field(default_factory=lambda: _str("ESP32_PORT", "", "ROVER_PORT"))
    esp32_init: str = field(default_factory=lambda: _str(
        "ESP32_INIT", '{"T":131,"cmd":1};{"T":136,"cmd":1000}'))
    # typ podwozia wysyłany po każdym połączeniu (T:900): 1 = RaspRover, 0 = nie wysyłaj
    esp32_chassis: int = field(default_factory=lambda: int(_float("ESP32_CHASSIS", 1)))
    esp32_module: int = field(default_factory=lambda: int(_float("ESP32_MODULE", 0)))
    # 1 = kamera patrzy w stronę TYŁU podwozia: "przód" to kierunek kamery, więc koła
    # dostają odwrócony kierunek i zamienione strony (L,R) -> (-R,-L)
    drive_flip: bool = field(default_factory=lambda: _bool("DRIVE_FLIP", False))

    # --- autodetekcja portów ---
    serial_extra_ids: str = field(default_factory=lambda: _str("SERIAL_EXTRA_IDS", ""))
    serial_probe_all: bool = field(default_factory=lambda: _bool("SERIAL_PROBE_ALL", False))
    probe_retry_s: float = field(default_factory=lambda: _float("PROBE_RETRY_S", 10.0))

    # --- OAK-D Pro ---
    oak_ir_dot: float = field(default_factory=lambda: _float("OAK_IR_DOT", 0.3))         # 0..1
    oak_mirror: bool = field(default_factory=lambda: _bool("OAK_MIRROR", True))
    oak_hfov_deg: float = field(default_factory=lambda: _float("OAK_HFOV_DEG", 69.0))
    oak_lm_model: str = field(default_factory=lambda: _str("OAK_LM_MODEL", "lite"))
    oak_internal_fps: float = field(default_factory=lambda: _float("OAK_INTERNAL_FPS", 0.0))  # 0 = auto
    oak_stream_fps: float = field(default_factory=lambda: _float("OAK_STREAM_FPS", 15.0))
    oak_no_frame_s: float = field(default_factory=lambda: _float("OAK_NO_FRAME_S", 3.0))
    oak_boot_timeout_s: float = field(default_factory=lambda: _float("OAK_BOOT_TIMEOUT_S", 25.0))
    oak_force_fallback: bool = field(default_factory=lambda: _bool("OAK_FORCE_FALLBACK", False))
    # detekcja osób na VPU obok śledzenia dłoni (wyłącza się sama, jeśli kamera nie da rady)
    oak_person_detect: bool = field(default_factory=lambda: _bool("OAK_PERSON_DETECT", True))
    oak_person_every_n: int = field(default_factory=lambda: max(1, int(_float("OAK_PERSON_EVERY_N", 4))))
    oak_person_conf: float = field(default_factory=lambda: _float("OAK_PERSON_CONF", 0.5))
    person_model: str = field(default_factory=lambda: _str(
        "PERSON_MODEL", "/app/vendor/models/mobilenet-ssd_openvino_2021.4_4shave.blob"))
    # ludzie na mapie LiDAR
    people_max_range_m: float = field(default_factory=lambda: _float("PEOPLE_MAX_RANGE_M", 4.0))
    # pozycja kamery względem środka LiDAR-u [m]: X do przodu, Y w lewo, Z w górę
    cam_offset_x: float = field(default_factory=lambda: _float("CAM_OFFSET_X", 0.10))
    cam_offset_y: float = field(default_factory=lambda: _float("CAM_OFFSET_Y", 0.0))
    cam_offset_z: float = field(default_factory=lambda: _float("CAM_OFFSET_Z", -0.05))

    # --- symulacja ---
    sim_fps: float = field(default_factory=lambda: _float("SIM_FPS", 15.0))
    sim_camera_height_m: float = field(default_factory=lambda: _float("SIM_CAMERA_HEIGHT_M", 0.95))

    # --- obraz w przeglądarce ---
    jpeg_quality: int = field(default_factory=lambda: int(_float("JPEG_QUALITY", 78)))
    stream_width: int = field(default_factory=lambda: int(_float("STREAM_WIDTH", 640)))


settings = Settings()
