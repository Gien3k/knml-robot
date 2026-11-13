export type Mode = "real" | "mock" | "off";
export type SensorKey = "oak" | "lidar" | "rover";

export interface SensorStatus { mode: Mode; detail: string; message: string; fps: number; since: number }

export interface Detection { label: string; conf: number; bbox: [number, number, number, number]; xyz: [number, number, number] }

export interface Hand {
  gesture: "OPEN_PALM" | "FIST" | "UNKNOWN"; raw: string; conf: number; offset: number;
  bbox: [number, number, number, number]; center: [number, number]; z: number;
  area_cm2: number; solidity: number; gaps: number;
}

export interface Track { id: number; x: number; y: number; vx: number; vy: number; w: number; age: number; d: number }

type Zones<T> = Record<"front" | "rear" | "left" | "right", T>;

export interface Safety {
  state: "OK" | "WARN" | "ESTOP"; min_dist: number | null; min_angle: number | null;
  zones: Zones<number | null>; blocked: Zones<boolean>;
  safety_m: number; estop_m: number; intrusions: number;
}

export interface Pose { x: number; y: number; th: number; quality: number; mode: string; calibrating: boolean }

export interface Wheel { v: number; rpm: number }

export interface Adas {
  state: string; reason: string; mode: "stand" | "mat";
  intent: { v: number; w: number }; cmd: { L: number; R: number }; vmax: number;
  geofence: { active: boolean; radius: number; dist: number; breach: boolean };
  target: number | null; fused: Record<string, number>; gesture: Hand | null; narration: string;
}

export interface State {
  type: "state"; t: number;
  sensors: Record<SensorKey, SensorStatus>;
  oak: { detections: Detection[] | null; hand: Hand | null; latency_ms: number | null; nn: boolean; nn_fps: number; sim_phase?: string };
  lidar: { tracks: Track[] | null; safety: Safety | null; pose: Pose | null; hz: number };
  drive: { wheels: Record<"fl" | "rl" | "fr" | "rr", Wheel>; source: string; battery_v: number | null;
           gimbal: { pan: number; tilt: number } | null; imu: Record<string, number> | null };
  adas: Adas; estop: boolean;
  events: { t: number; level: string; msg: string }[];
}

export interface Scan { type: "scan"; t: number; r: number[]; fg: number[] }

export interface Config {
  hw_mode: string; safety_m: number; estop_m: number; geofence_m: number; half_length: number;
  half_width: number; track_width: number; wheel_diameter: number; oak_mirror: boolean; oak_hfov: number;
  max_speed: number; mat_max_speed: number;
}

export interface FrameHeader { ts: number; w: number; h: number; dets?: Detection[]; hand?: Hand | null; mode?: string }
