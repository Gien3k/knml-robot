import "@fontsource-variable/inter";
import "@fontsource/jetbrains-mono/400.css";
import "@fontsource/jetbrains-mono/600.css";
import "./style.css";

import { fmt } from "./canvas";
import { ADAS_UI, ICON } from "./icons";
import { connectJson, post } from "./net";
import type { Config, Scan, State } from "./types";
import { TimeChart } from "./widgets/chart";
import { fpvOverlay, oakOverlay } from "./widgets/overlays";
import { Radar } from "./widgets/radar";
import { VideoTile } from "./widgets/video";
import { WheelsViz } from "./widgets/wheels";

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;

// ------------------------------------------------------------------ scena 1920x1080
const stage = $("stage");
function layout(): void {
  const s = Math.min(innerWidth / 1920, innerHeight / 1080);
  stage.style.transform = `scale(${s})`;
  stage.style.left = `${(innerWidth - 1920 * s) / 2}px`;
  stage.style.top = `${(innerHeight - 1080 * s) / 2}px`;
}
addEventListener("resize", layout);
layout();

// ------------------------------------------------------------------ konfiguracja i preferencje
let cfg: Config = {
  hw_mode: "auto", safety_m: 0.25, estop_m: 0.12, geofence_m: 0.2, half_length: 0.125, half_width: 0.105,
  track_width: 0.17, wheel_diameter: 0.08, oak_mirror: true, oak_hfov: 69, max_speed: 0.3, mat_max_speed: 0.1,
};
const pref = (k: string, d: boolean): boolean => {
  try { const v = localStorage.getItem(k); return v === null ? d : v === "1"; } catch { return d; }
};
const setPref = (k: string, v: boolean) => { try { localStorage.setItem(k, v ? "1" : "0"); } catch { /* brak storage */ } };
const ui = { audience: pref("audience", true), mirror: pref("mirror", true), debug: false, help: false };

fetch("/api/config").then((r) => r.json()).then((c: Config) => {
  cfg = c;
  if (localStorage.getItem("mirror") === null) ui.mirror = c.oak_mirror;
  distChart.setThresholds(thresholds());
}).catch(() => undefined);

// ------------------------------------------------------------------ widżety
const oak = new VideoTile($("oakCanvas"), "oak", { mirror: () => ui.mirror, overlay: oakOverlay, fit: "contain", label: "OAK-D Pro" });
const depth = new VideoTile($("depthCanvas"), "depth", { mirror: () => ui.mirror, fit: "cover", label: "Głębia" });
const fpv = new VideoTile($("fpvCanvas"), "fpv", { overlay: fpvOverlay, fit: "cover", label: "FPV" });
const radar = new Radar($("radarCanvas"), () => cfg);
const wheels = new WheelsViz($("wheelsCanvas"), () => cfg);
const thresholds = () => [
  { v: cfg.safety_m, color: "#ec835a", label: `${Math.round(cfg.safety_m * 100)} cm` },
  { v: cfg.estop_m, color: "#d03b3b", label: "STOP" },
];
const distChart = new TimeChart($("distChart"), {
  series: [{ color: "#f0efec" }], min: 0, max: 1.5, window: 10, ticks: [0, 0.5, 1, 1.5], thresholds: thresholds(),
});
const speedChart = new TimeChart($("speedChart"), {
  series: [{ color: "#3987e5", label: "L" }, { color: "#d95926", label: "P" }],
  min: -0.35, max: 0.35, window: 10, zero: true, ticks: [-0.3, 0, 0.3], fmt: (v) => v.toFixed(2).replace(".", ","),
});
oak.start();
depth.start();
fpv.start();

// ------------------------------------------------------------------ telemetria
let state: State | null = null;
let online = false;
connectJson("/ws/telemetry", (m: State | Scan) => {
  if (m.type === "scan") { radar.pushScan(m); return; }
  state = m;
  radar.state = m;
  const now = performance.now() / 1000;
  const md = m.lidar.safety?.min_dist;
  distChart.push(now, [md === null || md === undefined ? null : Math.min(md, 1.5)]);
  speedChart.push(now, [m.drive.wheels.fl.v, m.drive.wheels.fr.v]);
  const vm = Math.max(0.12, m.adas?.vmax ?? cfg.max_speed) * 1.15;
  speedChart.setRange(-vm, vm, [-+(vm * 0.85).toFixed(2), 0, +(vm * 0.85).toFixed(2)]);
}, (up) => { online = up; });

// ------------------------------------------------------------------ pętla rysowania
let last = performance.now();
function frame(t: number): void {
  const dt = Math.min(0.1, (t - last) / 1000);
  last = t;
  oak.draw();
  depth.draw();
  fpv.draw();
  radar.draw(ui.audience, dt);
  wheels.draw(state, ui.audience, dt);
  distChart.draw(t / 1000);
  speedChart.draw(t / 1000);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

// ------------------------------------------------------------------ DOM (5 Hz – liczby czytelne z daleka)
const SENSOR_NAMES = { oak: "OAK-D", lidar: "LiDAR", rover: "ESP32", fpv: "FPV" } as const;
const MODE_TXT = { real: "LIVE", mock: "SIM", off: "OFF" } as const;
const ZONES = [["front", "PRZÓD"], ["rear", "TYŁ"], ["left", "LEWO"], ["right", "PRAWO"]] as const;

function setHTML(id: string, html: string): void {
  const el = $(id);
  if (el.innerHTML !== html) el.innerHTML = html;
}

function distTxt(d: number | null | undefined): string {
  if (d === null || d === undefined) return "—";
  return d < 1 ? `${Math.round(d * 100)}<small>cm</small>` : `${fmt(d, 2)}<small>m</small>`;
}

function updateDom(): void {
  const now = new Date();
  $("clock").textContent = now.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const s = state;
  if (!s || !online) {
    $("adasLabel").textContent = online ? "ŁĄCZENIE…" : "BRAK POŁĄCZENIA";
    $("adasBadge").dataset.tone = "idle";
    setHTML("adasIcon", ICON.idle);
    $("narrText").textContent = "Łączenie z backendem robota…";
    return;
  }

  // nagłówek
  setHTML("pills", (Object.keys(SENSOR_NAMES) as (keyof typeof SENSOR_NAMES)[]).map((k) => {
    const st = s.sensors[k];
    return `<div class="pill" title="${st.message}"><span class="dot ${st.mode}"></span><b>${SENSOR_NAMES[k]}</b><span class="mode">${MODE_TXT[st.mode]}</span></div>`;
  }).join(""));
  const mat = s.adas?.mode === "mat";
  setHTML("modeChip", mat ? `MATA · klatka ${Math.round(cfg.geofence_m * 100)} cm` : "PODSTAWKA · koła w powietrzu");
  const p = s.perf ?? {};
  setHTML("perfChip", `CPU ${p.cpu ?? "—"}%${p.temp_c ? ` · ${p.temp_c.toFixed(0)}°C` : ""}`);
  const a = ADAS_UI[s.adas?.state ?? "IDLE"] ?? ADAS_UI.IDLE;
  $("adasBadge").dataset.tone = a.tone;
  $("adasLabel").textContent = a.label;
  setHTML("adasIcon", ICON[a.icon]);

  // OAK / FPV
  const so = s.sensors.oak, sf = s.sensors.fpv;
  $("oakMeta").textContent = `${MODE_TXT[so.mode]} · ${oak.fps.toFixed(0)} fps · NN ${s.oak.nn ? `${s.oak.nn_fps.toFixed(0)} fps` : "brak"} · ${s.oak.latency_ms ?? "—"} ms`;
  $("fpvMeta").textContent = `${MODE_TXT[sf.mode]} · ${fpv.fps.toFixed(0)} fps`;

  // bezpieczeństwo
  const sz = s.lidar.safety;
  const tracks = s.lidar.tracks ?? [];
  $("radarMeta").textContent = `${MODE_TXT[s.sensors.lidar.mode]} · ${(s.lidar.hz ?? 0).toFixed(0)} Hz · ${tracks.length} ${tracks.length === 1 ? "obiekt" : "obiekty"}`;
  setHTML("minDist", distTxt(sz?.min_dist));
  const blockedNames = sz ? ZONES.filter(([k]) => sz.blocked[k]).map(([, n]) => n.toLowerCase()) : [];
  const ss = !sz ? `<span style="color:var(--muted)">${ICON.idle}</span>czekam na skan…`
    : sz.state === "ESTOP" ? `<span style="color:var(--critical)">${ICON.stop}</span>STOP AWARYJNY`
    : sz.state === "WARN" ? `<span style="color:var(--serious)">${ICON.warn}</span>Blokada: ${blockedNames.join(", ")}`
    : `<span style="color:var(--good)">${ICON.ok}</span>Strefa bezpieczeństwa wolna`;
  setHTML("safetyStatus", ss);
  setHTML("zones", ZONES.map(([k, n]) => {
    const b = sz?.blocked[k] ?? false;
    const d = sz?.zones[k];
    return `<div class="zone" data-blocked="${b ? 1 : 0}"><span class="z-name">${n}${b ? "<b>BLOKADA</b>" : ""}</span><span class="z-val mono">${d === null || d === undefined ? "—" : d < 1 ? `${Math.round(d * 100)} cm` : `${fmt(d, 1)} m`}</span></div>`;
  }).join(""));
  const pose = s.lidar.pose;
  setHTML("geoInfo", mat && pose
    ? `<b>Wirtualna klatka:</b> ${fmt(s.adas.geofence.dist, 2)} / ${fmt(cfg.geofence_m, 2)} m od środka · ICP ${Math.round(pose.quality * 100)}%${pose.calibrating ? " · kalibracja…" : ""}`
    : `<b>Podstawka:</b> koła kręcą się w powietrzu. Bezpiecznik reaguje na ruchome obiekty bliżej niż ${Math.round(cfg.safety_m * 100)} cm.`);

  // gest
  const hand = s.oak.hand;
  const gName = !hand ? "Brak dłoni" : hand.gesture === "OPEN_PALM" ? "Otwarta dłoń" : hand.gesture === "FIST" ? "Pięść" : "Niepewny gest";
  const gIcon = !hand ? ICON.none : hand.gesture === "FIST" ? ICON.fist : ICON.open;
  const gColor = !hand ? "var(--muted)" : hand.gesture === "OPEN_PALM" ? "var(--good)" : hand.gesture === "FIST" ? "var(--critical)" : "var(--warning)";
  setHTML("gIcon", `<span style="color:${gColor};display:grid">${gIcon}</span>`);
  $("gName").textContent = gName;
  $("gSub").textContent = !hand ? "Otwarta dłoń = jazda · pięść = stop · ruch w bok = skręt"
    : hand.gesture === "OPEN_PALM" ? "Jazda naprzód – przesuń dłoń w bok, aby skręcić" : hand.gesture === "FIST" ? "Hamowanie – koła stoją" : "Rozłóż palce albo zaciśnij pięść";
  $("gConf").style.width = `${Math.round((hand?.conf ?? 0) * 100)}%`;
  $("gConfV").textContent = hand ? `${Math.round(hand.conf * 100)}%` : "—";
  const off = hand ? (ui.mirror ? -hand.offset : hand.offset) : 0;
  $("gSteer").style.left = `${50 + off * 50}%`;
  $("gSteerV").textContent = hand ? `${off > 0.12 ? "→" : off < -0.12 ? "←" : "↑"}` : "—";
  $("gMetrics").textContent = hand
    ? `z ${fmt(hand.z, 2)} m · pole ${Math.round(hand.area_cm2)} cm² · solidność ${fmt(hand.solidity, 2)} · wcięcia ${hand.gaps}`
    : "Klasyfikator kształtu z mapy głębi (plaster najbliższego obiektu)";
  $("gestMeta").textContent = so.mode === "real" ? "głębia OAK-D" : "głębia SIM";

  // ML / system
  const dets = s.oak.detections ?? [];
  const persons = dets.filter((d) => d.label === "osoba").length;
  const fusedN = Object.keys(s.adas?.fused ?? {}).length;
  const rows: [string, string, boolean?][] = [
    ["OAK-D · obraz", `${oak.fps.toFixed(0)} fps`],
    ["VPU · MobileNet-SSD", s.oak.nn ? `${s.oak.nn_fps.toFixed(0)} fps` : "brak modelu", !s.oak.nn],
    ["Osoby w kadrze (OAK)", `${persons}`],
    ["Obiekty 360° (LiDAR)", `${tracks.length}`],
    ["Fuzja OAK ↔ LiDAR", `${fusedN} / ${persons}`],
    ["LiDAR · skan", `${(s.lidar.hz ?? 0).toFixed(0)} Hz`],
    ["ESP32 · telemetria", `${s.sensors.rover.fps.toFixed(0)} Hz`],
    ["CPU · RAM", `${p.cpu ?? "—"}% · ${p.mem_used_pct ?? "—"}%`],
  ];
  setHTML("mlStats", rows.map(([k, v, dim]) => k === "sep" ? `<div class="sep"></div>` : `<dt>${k}</dt><dd class="${dim ? "dim" : ""}">${v}</dd>`).join(""));
  $("mlMeta").textContent = `${p.cores ?? "?"} rdzenie`;

  // napęd
  const wl = s.drive.wheels.fl.v, wr = s.drive.wheels.fr.v;
  const v = (wl + wr) / 2, om = (wr - wl) / cfg.track_width;
  $("driveMeta").textContent = `${MODE_TXT[s.sensors.rover.mode]} · ${s.drive.source ?? ""}`;
  setHTML("driveStats", `<span>v <b class="mono">${fmt(v, 2)}</b> m/s</span><span>ω <b class="mono">${fmt(om, 2)}</b> rad/s</span>` +
    `<span>bateria <b class="mono">${s.drive.battery_v ? fmt(s.drive.battery_v, 1) : "—"}</b> V</span>` +
    `<span>limit <b class="mono">${fmt(s.adas?.vmax ?? 0, 2)}</b></span>`);

  // narrator
  $("narrText").textContent = s.adas?.narration ?? "";
  $("narrDot").dataset.tone = a.tone === "idle" ? "info" : a.tone;
  const mocks = (Object.keys(s.sensors) as (keyof typeof SENSOR_NAMES)[]).filter((k) => s.sensors[k].mode === "mock");
  $("narrSrc").textContent = mocks.length ? `symulowane: ${mocks.map((k) => SENSOR_NAMES[k]).join(", ")}` : "wszystkie sensory na żywo";
  $("footLeft").textContent = `HW_MODE=${cfg.hw_mode}`;

  if (ui.debug) renderDebug(s);
}
setInterval(updateDom, 200);

function renderDebug(s: State): void {
  const ev = [...s.events].reverse().map((e) => {
    const t = new Date(e.t * 1000).toLocaleTimeString("pl-PL");
    return `<div class="ev ${e.level}"><time>${t}</time><span>${e.msg}</span></div>`;
  }).join("") || `<div class="ev"><time>—</time><span>brak zdarzeń</span></div>`;
  const sens = (Object.keys(SENSOR_NAMES) as (keyof typeof SENSOR_NAMES)[]).map((k) => {
    const st = s.sensors[k];
    return `<div class="ev"><time>${SENSOR_NAMES[k]}</time><span>${MODE_TXT[st.mode]} · ${st.detail || "—"} · ${st.message}</span></div>`;
  }).join("");
  setHTML("debug", `<h3>Czujniki</h3>${sens}<h3 style="margin-top:14px">Zdarzenia</h3>${ev}` +
    (s.oak.sim_phase ? `<h3 style="margin-top:14px">Symulacja</h3><div class="ev"><time>faza</time><span>${s.oak.sim_phase}</span></div>` : ""));
}

// ------------------------------------------------------------------ operator
function toast(msg: string): void {
  const el = $("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  el.style.opacity = "1";
  clearTimeout((toast as any)._t);
  (toast as any)._t = setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.classList.add("hidden"), 300); }, 1800);
}

addEventListener("keydown", async (e) => {
  if (e.repeat) return;
  const k = e.key.toLowerCase();
  if (e.code === "Space") {
    e.preventDefault();
    const r = await post("/api/estop");
    toast(r.estop ? "STOP operatora – koła zatrzymane" : "Wznowiono pracę");
  } else if (k === "r") {
    await post("/api/recalibrate");
    toast("Kalibracja tła LiDAR i wirtualnej klatki…");
  } else if (k === "v") {
    ui.audience = !ui.audience; setPref("audience", ui.audience);
    toast(ui.audience ? "Radar: widok od strony widza" : "Radar: widok od strony robota");
  } else if (k === "m") {
    ui.mirror = !ui.mirror; setPref("mirror", ui.mirror);
    toast(ui.mirror ? "Obraz OAK: lustro" : "Obraz OAK: bez lustra");
  } else if (k === "d") {
    ui.debug = !ui.debug; $("debug").classList.toggle("hidden", !ui.debug); $("help").classList.add("hidden"); ui.help = false;
  } else if (k === "h") {
    ui.help = !ui.help; $("help").classList.toggle("hidden", !ui.help); $("debug").classList.add("hidden"); ui.debug = false;
  } else if (k === "f") {
    if (document.fullscreenElement) await document.exitFullscreen(); else await document.documentElement.requestFullscreen();
  }
});

let cursorT = 0;
addEventListener("mousemove", () => {
  document.body.classList.add("show-cursor");
  clearTimeout(cursorT);
  cursorT = window.setTimeout(() => document.body.classList.remove("show-cursor"), 2500);
});

// logotypy: podmiana pliku w assets/ pojawia się bez przeładowania strony
function refreshLogo(id: string, name: string): void {
  const img = new Image();
  img.onload = () => { ($(id) as HTMLImageElement).src = img.src; };
  img.src = `/branding/${name}?v=${Date.now()}`;
}
setInterval(() => { refreshLogo("logoKolo", "kolo"); refreshLogo("logoRzeszow", "rzeszow"); }, 20000);
