"""Konfiguracja z zmiennych środowiskowych (plik .env -> docker compose)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _raw(name: str) -> str | None:
    v = os.getenv(name)
    if v is None:
        return None
    # obrona przed komentarzem w tej samej linii ("X=1   # opis")
    v = v.split(" #", 1)[0].strip()
    return v or None


def _str(name: str, default: str) -> str:
    v = _raw(name)
    return default if v is None else v


def _float(name: str, default: float) -> float:
    v = _raw(name)
    try:
        return default if v is None else float(v.replace(",", "."))
    except ValueError:
        return default


def _int(name: str, default: int) -> int:
    v = _raw(name)
    try:
        return default if v is None else int(v)
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    v = _raw(name)
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "on", "tak")


@dataclass(frozen=True)
class Settings:
    # --- ogólne ---
    hw_mode: str = _str("HW_MODE", "auto").lower()           # auto | mock | real
    motion_mode: str = _str("MOTION_MODE", "stand").lower()  # stand (podstawka) | mat (mata + klatka)
    static_dir: str = _str("STATIC_DIR", "/app/static")
    assets_dir: str = _str("ASSETS_DIR", "/app/assets")
    detect_interval_s: float = _float("DETECT_INTERVAL_S", 2.0)

    # --- bezpieczeństwo ---
    safety_distance_m: float = _float("SAFETY_DISTANCE_M", 0.25)
    estop_distance_m: float = _float("ESTOP_DISTANCE_M", 0.12)
    clear_hold_s: float = _float("SAFETY_CLEAR_HOLD_S", 0.8)
    geofence_radius_m: float = _float("GEOFENCE_RADIUS_M", 0.20)

    # --- geometria robota (RaspRover) ---
    robot_half_length_m: float = _float("ROBOT_HALF_LENGTH_M", 0.125)
    robot_half_width_m: float = _float("ROBOT_HALF_WIDTH_M", 0.105)
    track_width_m: float = _float("TRACK_WIDTH_M", 0.17)
    wheel_diameter_m: float = _float("WHEEL_DIAMETER_M", 0.08)

    # --- napęd ---
    max_wheel_speed: float = _float("MAX_WHEEL_SPEED", 0.30)
    mat_max_wheel_speed: float = _float("MAT_MAX_WHEEL_SPEED", 0.10)
    accel_limit: float = _float("ACCEL_LIMIT", 1.2)          # m/s^2 na koło
    steer_gain: float = _float("STEER_GAIN", 1.0)
    cmd_timeout_s: float = _float("CMD_TIMEOUT_S", 0.30)     # watchdog napędu

    # --- LiDAR D500 / STL-19P ---
    lidar_port: str = _str("LIDAR_PORT", "")
    lidar_baud: int = _int("LIDAR_BAUD", 230400)
    lidar_angle_offset_deg: float = _float("LIDAR_ANGLE_OFFSET_DEG", 0.0)
    lidar_clockwise: bool = _bool("LIDAR_CLOCKWISE", True)
    lidar_min_range_m: float = _float("LIDAR_MIN_RANGE_M", 0.06)
    lidar_max_range_m: float = _float("LIDAR_MAX_RANGE_M", 8.0)
    lidar_min_intensity: int = _int("LIDAR_MIN_INTENSITY", 8)

    # --- ESP32 (Waveshare JSON) ---
    rover_port: str = _str("ROVER_PORT", "")
    rover_baud: int = _int("ROVER_BAUD", 115200)
    gimbal_enabled: bool = _bool("GIMBAL_ENABLED", True)
    gimbal_tilt_deg: float = _float("GIMBAL_TILT_DEG", 10.0)

    # --- OAK-D Pro ---
    oak_fps: int = _int("OAK_FPS", 30)
    oak_model_path: str = _str("OAK_MODEL_PATH", "/app/models/mobilenet-ssd.blob")
    oak_ir_dot: float = _float("OAK_IR_DOT", 0.5)
    oak_mirror: bool = _bool("OAK_MIRROR", True)
    oak_hfov_deg: float = _float("OAK_HFOV_DEG", 69.0)
    hand_zmin_m: float = _float("HAND_ZMIN_M", 0.25)
    hand_zmax_m: float = _float("HAND_ZMAX_M", 1.30)

    # --- symulacja ---
    mock_fps: int = _int("MOCK_FPS", 24)
    camera_height_m: float = _float("CAMERA_HEIGHT_M", 0.95)  # wys. kamer nad podłogą (stół + robot)


settings = Settings()
