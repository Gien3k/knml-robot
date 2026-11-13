const svg = (body: string) =>
  `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;

/** Ikony gestów (rysowane kreską, kolor z otoczenia). */
export const GESTURE_ICON = {
  open: svg('<path d="M7 13V6.5a1.5 1.5 0 013 0V12M10 11.5V4.5a1.5 1.5 0 013 0V11.5M13 11.5V5.5a1.5 1.5 0 013 0V12M16 12V8.5a1.5 1.5 0 013 0v5.5a7 7 0 01-7 7h-.5A6.5 6.5 0 015 15.5L3.6 12.6a1.5 1.5 0 012.6-1.5L7 12.5"/>'),
  fist: svg('<path d="M6 10.5a2 2 0 014 0M10 10.5V9a2 2 0 014 0v1.5M14 10.5V9.5a2 2 0 014 0V14a6 6 0 01-6 6h-1a5 5 0 01-5-5v-4.5a2 2 0 014 0"/><path d="M9 14.5h4.5"/>'),
  none: svg('<path d="M7 13V6.5a1.5 1.5 0 013 0V12M10 11.5V4.5a1.5 1.5 0 013 0V11.5M13 11.5V5.5a1.5 1.5 0 013 0V12M16 12V8.5a1.5 1.5 0 013 0v5.5a7 7 0 01-7 7h-.5A6.5 6.5 0 015 15.5L3.6 12.6a1.5 1.5 0 012.6-1.5L7 12.5" stroke-dasharray="2 2.6"/>'),
};

export type Tone = "idle" | "go" | "caution" | "stop";

/** Hasło stanu na dole ekranu – mówi robot, prostym językiem. */
export const STATE_UI: Record<string, { title: string; tone: Tone }> = {
  IDLE: { title: "Pokaż otwartą dłoń", tone: "idle" },
  DRIVE: { title: "Jadę naprzód", tone: "go" },
  STOP_GESTURE: { title: "Zatrzymany", tone: "idle" },
  BLOCKED: { title: "Przeszkoda na drodze", tone: "caution" },
  BOUNCE: { title: "Odbijam od przeszkody", tone: "caution" },
  GEOFENCE: { title: "Granica strefy", tone: "caution" },
  ESTOP: { title: "Stop awaryjny", tone: "stop" },
  OPERATOR_STOP: { title: "Wstrzymany przez obsługę", tone: "stop" },
  LOCKED: { title: "Napęd zablokowany", tone: "idle" },
  MANUAL: { title: "Test napędu", tone: "go" },
};
