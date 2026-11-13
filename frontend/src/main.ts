import "@fontsource/barlow/400.css";
import "@fontsource/barlow/500.css";
import "@fontsource/barlow/600.css";
import "@fontsource/barlow-condensed/500.css";
import "@fontsource/barlow-condensed/600.css";
import "./style.css";

import { fmt } from "./canvas";
import { GESTURE_ICON, STATE_UI } from "./icons";
import { connectJson, post } from "./net";
import type { Config, Scan, SensorKey, State } from "./types";
import { oakOverlay } from "./widgets/overlays";
import { Radar } from "./widgets/radar";
import { VideoTile } from "./widgets/video";
import { WheelsViz } from "./widgets/wheels";

const $ = <T extends HTMLElement = HTMLElement>(id: string) => document.getElementById(id) as T;

// ------------------------------------------------------------------ scena 1920x1080 skalowana do monitora
const stage = $("stage");
function layout(): void {
  const s = Math.min(innerWidth / 1920, innerHeight / 1080);
  stage.style.transform = `scale(${s})`;
  stage.style.left = `${(innerWidth - 1920 * s) / 2}px`;
  stage.style.top = `${(innerHeight - 1080 * s) / 2}px`;
}
addEventListener("resize", layout);
layout();

// ------------------------------------------------------------------ konfiguracja
let cfg: Config = {
  hw_mode: "auto", safety_m: 0.25, estop_m: 0.12, geofence_m: 0.2, half_length: 0.125, half_width: 0.105,
  track_width: 0.17, wheel_diameter: 0.08, oak_mirror: true, oak_hfov: 69, max_speed: 0.3, mat_max_speed: 0.1,
};
const readPref = (k: string): boolean | null => {
  try { const v = localStorage.getItem(k); return v === null ? null : v === "1"; } catch { return null; }
};
const savePref = (k: string, v: boolean) => { try { localStorage.setItem(k, v ? "1" : "0"); } catch { /* brak */ } };
const ui = { audience: readPref("audience") ?? true, mirror: readPref("mirror") ?? true, debug: false, help: false };

fetch("/api/config").then((r) => r.json()).then((c: Config) => {
  cfg = c;
  if (readPref("mirror") === null) ui.mirror = c.oak_mirror;
}).catch(() => undefined);

// ------------------------------------------------------------------ widoki
const oak = new VideoTile($("oakCanvas"), "oak", { mirror: () => ui.mirror, overlay: oakOverlay, fit: "cover", label: "Kamera" });
const depth = new VideoTile($("depthCanvas"), "depth", { mirror: () => ui.mirror, fit: "cover", label: "Głębia" });
const radar = new Radar($("radarCanvas"), () => cfg);
const wheels = new WheelsViz($("wheelsCanvas"), () => cfg);
oak.start();
depth.start();

let state: State | null = null;
let online = false;
connectJson("/ws/telemetry", (m: State | Scan) => {
  if (m.type === "scan") { radar.pushScan(m); return; }
  state = m;
  radar.setState(m);
}, (up) => { online = up; });

let last = performance.now();
function frame(t: number): void {
  const dt = Math.min(0.1, (t - last) / 1000);
  last = t;
  oak.draw();
  depth.draw();
  radar.draw(ui.audience);
  wheels.draw(state, ui.audience, dt);
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);

// ------------------------------------------------------------------ odczyty (5 Hz – czytelne z kilku metrów)
const SENSORS: [SensorKey, string][] = [["oak", "OAK-D Pro"], ["lidar", "LiDAR"], ["rover", "Napęd"]];
const MODE_TXT = { real: "na żywo", mock: "symulacja", off: "brak" } as const;
const SIDE_TXT = { front: "z przodu", rear: "z tyłu", left: "z boku", right: "z boku" } as const;

function html(id: string, value: string): void {
  const el = $(id);
  if (el.innerHTML !== value) el.innerHTML = value;
}
function text(id: string, value: string): void {
  const el = $(id);
  if (el.textContent !== value) el.textContent = value;
}

function update(): void {
  const s = state;
  if (!s || !online) {
    text("stateTitle", online ? "Łączę się z robotem" : "Brak połączenia z robotem");
    text("stateText", "Sprawdź, czy kontener z oprogramowaniem robota jest uruchomiony.");
    $("stateBlock").dataset.tone = "idle";
    return;
  }

  // czujniki
  html("sensors", SENSORS.map(([k, name]) => {
    const m = s.sensors[k].mode;
    return `<div class="sensor" title="${s.sensors[k].message}"><i class="dot ${m}"></i><b>${name}</b><span>${MODE_TXT[m]}</span></div>`;
  }).join(""));
  text("camMode", s.sensors.oak.mode === "mock" ? "symulacja w podczerwieni" : s.sensors.oak.mode === "real" ? "na żywo" : "");
  text("zoneLegend", `strefa ${Math.round(cfg.safety_m * 100)} cm`);

  // stan systemu
  const key = s.estop ? "OPERATOR_STOP" : s.adas?.state ?? "IDLE";
  const st = STATE_UI[key] ?? STATE_UI.IDLE;
  let title = st.title;
  if (key === "DRIVE" && Math.abs(s.adas.intent.w) > 0.05) title = "Jadę i skręcam";
  text("stateTitle", title);
  text("stateText", s.adas?.narration ?? "");
  $("stateBlock").dataset.tone = st.tone;
  stage.dataset.alert = st.tone === "stop" ? "stop" : "";

  // gest
  const hand = s.oak.hand;
  const g = hand?.gesture;
  const gColor = !hand ? "var(--gravel)" : g === "OPEN_PALM" ? "var(--go)" : g === "FIST" ? "var(--stop)" : "var(--caution)";
  html("gIcon", `<span style="color:${gColor};display:grid">${!hand ? GESTURE_ICON.none : g === "FIST" ? GESTURE_ICON.fist : GESTURE_ICON.open}</span>`);
  text("gName", !hand ? "Brak dłoni" : g === "OPEN_PALM" ? "Otwarta dłoń" : g === "FIST" ? "Pięść" : "Nie rozpoznaję");
  const conf = hand ? Math.round(hand.conf * 100) : 0;
  $("gConf").style.width = `${conf}%`;
  text("gConfV", hand ? `${conf}%` : "–");
  // skręt z perspektywy widza: dłoń po prawej stronie obrazu z kamery = lewa strona widza
  const off = hand ? hand.offset : 0;
  $("gSteer").style.left = `${50 - off * 50}%`;
  text("gSteerV", !hand ? "–" : Math.abs(off) < 0.12 ? "prosto" : off > 0 ? "w lewo" : "w prawo");

  // odległość
  const sz = s.lidar.safety;
  const d = sz?.min_dist;
  text("dVal", d === null || d === undefined ? "–" : d < 1 ? `${Math.round(d * 100)}` : fmt(d, 1));
  text("dUnit", d === null || d === undefined ? "" : d < 1 ? "cm" : "m");
  const tone = sz?.state === "ESTOP" ? "stop" : sz?.state === "WARN" ? "caution" : "go";
  $("distBlock").dataset.tone = tone;
  const sides = sz ? [...new Set((Object.keys(sz.blocked) as (keyof typeof SIDE_TXT)[]).filter((k) => sz.blocked[k]).map((k) => SIDE_TXT[k]))] : [];
  const dColor = tone === "stop" ? "var(--stop)" : tone === "caution" ? "var(--caution)" : "var(--go)";
  const dText = !sz ? "czekam na skan LiDAR" : tone === "stop" ? "stop awaryjny" : tone === "caution" ? `blokada ${sides.join(" i ")}` : "droga wolna";
  html("dStatus", `<i class="dot" style="background:${dColor}"></i>${dText}`);

  // napęd
  const wl = s.drive.wheels.fl.v, wr = s.drive.wheels.fr.v;
  text("wL", `${fmt(wl, 2)} m/s`);
  text("wR", `${fmt(wr, 2)} m/s`);
  const mat = s.adas?.mode === "mat";
  const sim = s.sensors.rover.mode !== "real";
  text("driveMode", (mat ? `Ruch po macie w strefie ${Math.round(cfg.geofence_m * 100)} cm.` : "Robot stoi na podstawce, koła kręcą się w powietrzu.")
    + (sim ? " Napęd w symulacji." : ""));

  if (ui.debug) renderDebug(s);
}
setInterval(update, 200);

function renderDebug(s: State): void {
  const sens = SENSORS.map(([k, name]) => {
    const st = s.sensors[k];
    return `<div class="ev"><time>${name}</time><span>${MODE_TXT[st.mode]}${st.detail ? `, ${st.detail}` : ""}. ${st.message}</span></div>`;
  }).join("");
  const ev = [...s.events].reverse().map((e) =>
    `<div class="ev ${e.level}"><time>${new Date(e.t * 1000).toLocaleTimeString("pl-PL")}</time><span>${e.msg}</span></div>`,
  ).join("") || `<div class="ev"><time></time><span>Brak zdarzeń.</span></div>`;
  html("debug", `<h2>Czujniki</h2>${sens}<h2 class="gap">Zdarzenia</h2>${ev}`);
}

// ------------------------------------------------------------------ obsługa stoiska
let toastT = 0;
function toast(msg: string): void {
  const el = $("toast");
  el.textContent = msg;
  el.classList.remove("hidden");
  el.style.opacity = "1";
  clearTimeout(toastT);
  toastT = window.setTimeout(() => { el.style.opacity = "0"; setTimeout(() => el.classList.add("hidden"), 300); }, 1800);
}

addEventListener("keydown", async (e) => {
  if (e.repeat) return;
  const k = e.key.toLowerCase();
  if (e.code === "Space") {
    e.preventDefault();
    const r = await post("/api/estop");
    toast(r.estop ? "Robot wstrzymany" : "Robot wznowił pracę");
  } else if (k === "r") {
    await post("/api/recalibrate");
    toast("Uczę się tła stoiska od nowa");
  } else if (k === "v") {
    ui.audience = !ui.audience;
    savePref("audience", ui.audience);
    toast(ui.audience ? "Radar od strony widza" : "Radar od strony robota");
  } else if (k === "m") {
    ui.mirror = !ui.mirror;
    savePref("mirror", ui.mirror);
    toast(ui.mirror ? "Obraz kamery jak w lustrze" : "Obraz kamery bez lustra");
  } else if (k === "d") {
    ui.debug = !ui.debug; ui.help = false;
    $("debug").classList.toggle("hidden", !ui.debug); $("help").classList.add("hidden");
  } else if (k === "h") {
    ui.help = !ui.help; ui.debug = false;
    $("help").classList.toggle("hidden", !ui.help); $("debug").classList.add("hidden");
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

// logotypy z folderu assets/ podmieniają się bez przeładowania strony
function refreshLogo(id: string, name: string): void {
  const img = new Image();
  img.onload = () => { ($(id) as HTMLImageElement).src = img.src; };
  img.src = `/branding/${name}?v=${Date.now()}`;
}
setInterval(() => { refreshLogo("logoKolo", "kolo"); refreshLogo("logoRzeszow", "rzeszow"); }, 20000);
