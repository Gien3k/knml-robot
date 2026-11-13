const svg = (body: string, vb = "0 0 24 24") =>
  `<svg viewBox="${vb}" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;

export const ICON = {
  ok: svg('<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.6 2.6L16 9.5"/>'),
  info: svg('<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>'),
  idle: svg('<circle cx="12" cy="12" r="9"/><path d="M9 12h6"/>'),
  warn: svg('<path d="M12 3.5L2.8 19.5h18.4z"/><path d="M12 10v4M12 17h.01"/>'),
  stop: svg('<path d="M8.2 3h7.6L21 8.2v7.6L15.8 21H8.2L3 15.8V8.2z"/><path d="M8.5 12h7"/>'),
  open: svg('<path d="M7 13V6.5a1.5 1.5 0 013 0V12M10 11.5V4.5a1.5 1.5 0 013 0V11.5M13 11.5V5.5a1.5 1.5 0 013 0V12M16 12V8.5a1.5 1.5 0 013 0v5.5a7 7 0 01-7 7h-.5A6.5 6.5 0 015 15.5L3.6 12.6a1.5 1.5 0 012.6-1.5L7 12.5"/>'),
  fist: svg('<rect x="5" y="7" width="14" height="10" rx="4"/><path d="M9 7v4M12 7v4M15 7v4M7 17v3h9v-3"/>'),
  none: svg('<path d="M7 13V6.5a1.5 1.5 0 013 0V12M10 11.5V4.5a1.5 1.5 0 013 0V11.5M13 11.5V5.5a1.5 1.5 0 013 0V12M16 12V8.5a1.5 1.5 0 013 0v5.5a7 7 0 01-7 7h-.5A6.5 6.5 0 015 15.5" opacity=".35"/><path d="M4 4l16 16"/>'),
};

export type Tone = "idle" | "info" | "good" | "warning" | "serious" | "critical";

export const ADAS_UI: Record<string, { label: string; tone: Tone; icon: keyof typeof ICON }> = {
  IDLE: { label: "GOTOWY", tone: "idle", icon: "idle" },
  DRIVE: { label: "JAZDA · GEST", tone: "good", icon: "ok" },
  STOP_GESTURE: { label: "STOP · PIĘŚĆ", tone: "warning", icon: "warn" },
  BLOCKED: { label: "BLOKADA KIERUNKU", tone: "serious", icon: "warn" },
  BOUNCE: { label: "ODBICIE", tone: "serious", icon: "warn" },
  GEOFENCE: { label: "WIRTUALNA KLATKA", tone: "serious", icon: "warn" },
  ESTOP: { label: "STOP AWARYJNY", tone: "critical", icon: "stop" },
  OPERATOR_STOP: { label: "STOP OPERATORA", tone: "critical", icon: "stop" },
  LOCKED: { label: "NAPĘD ZABLOKOWANY", tone: "idle", icon: "info" },
  MANUAL: { label: "TEST NAPĘDU", tone: "info", icon: "info" },
};
