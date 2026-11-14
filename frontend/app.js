/* =============================================================================
   Człowiek kontra AI – autonomiczny łazik: logika ekranu stoiska (bez frameworków).
   Dane: WebSocket /ws, obraz: /stream.mjpg (shared/kn.js).
   Hasło stanu jest wygładzane: krótkie, przypadkowe zmiany (np. pojedyncza spóźniona
   klatka czujnika) nie przełączają napisu, więc ekran nie miga. Stop pokazuje się od razu.
   ============================================================================= */
import { drawCalibDiagram, initCalib, renderCalib } from "./calib.js";
import { $, connect, controls, drawChassis, drawHand, drawRadar, fmt, loop, onState, phase, sinceBrake, stage, store } from "./shared/kn.js";

const HAND = { hand: "#6cb4ff", handShadow: "rgba(10,11,12,0.6)", joint: "#f3f1ea", people: "rgba(243,241,234,0.75)" };
const RADAR = {
  bg: "#0f1112", ring: "rgba(243,241,234,0.09)", label: "#9a9d9f", fov: "rgba(108,180,255,0.09)", route: "#6cb4ff",
  stop: "#e5484d", stopFill: "rgba(229,72,77,0.25)", zone: "rgba(63,194,122,0.8)", dot: "#f3f1ea",
  dotBg: "rgba(154,157,159,0.45)", personFill: "rgba(108,180,255,0.25)", robot: "#24282c", robotEdge: "rgba(243,241,234,0.55)",
};
const CHASSIS = { body: "#1d2023", bodyEdge: "rgba(243,241,234,0.3)", route: "#6cb4ff", go: "#3fc27a", stop: "#e5484d",
  wheel: "#141618", treadIdle: "rgba(243,241,234,0.14)" };
const DEVICES = [["oak", "OAK-D"], ["lidar", "LiDAR"], ["esp32", "ESP32"]];

const st = $("stage");
stage(st);
const img = $("cam");
connect(img);
controls();
initCalib();

// ------------------------------------------------------------------ hasło bez migania
const HOLD_MS = 600;      // ile najkrócej trzymamy wyświetlone hasło
const CONFIRM_MS = 250;   // ile nowy stan musi trwać, zanim go pokażemy
let shown = null, shownAt = 0, cand = null, candAt = 0;
function stablePhase(p, now) {
  if (!shown || (p.tone === "stop" && shown.key !== p.key)) { shown = p; shownAt = now; cand = null; return shown; }
  if (p.key === shown.key) { shown = p; cand = null; return shown; }           // ten sam stan, nowe liczby
  if (!cand || cand.key !== p.key) { cand = p; candAt = now; }
  if (now - candAt >= CONFIRM_MS && now - shownAt >= HOLD_MS) { shown = p; shownAt = now; cand = null; }
  return shown;
}

const setText = (id, v) => { const n = $(id); if (n.textContent !== v) n.textContent = v; };

function update() {
  const s = store.state;
  const p = stablePhase(phase(s), performance.now());
  setText("title", p.title);
  setText("line", p.line);
  if (st.dataset.tone !== p.tone) st.dataset.tone = p.tone;
  const b = (s && s.brake) || {};
  setText("record", b.record_s != null ? `Najszybsze hamowanie dziś: ${fmt(b.record_s)} s` : "");
  $("simBadge").hidden = !(s && s.sim);
  $("camWait").classList.toggle("show", !(s && s.cam && s.cam.live));
  const diag = s ? DEVICES.map(([k, n]) => `<span><i class="${(s.devices[k] || {}).state || ""}"></i>${n}</span>`).join("") : "";
  if ($("diag").innerHTML !== diag) $("diag").innerHTML = diag;
  renderCalib(s && s.calib);
}
onState(update);
setInterval(update, 200);

loop((dt, now) => {
  drawCalibDiagram(now);
  drawHand($("ov"), img, HAND);
  drawRadar($("radar"), RADAR, { cy: 0.44 });
  drawChassis($("chassis"), dt, CHASSIS);
  const estop = store.state && store.state.drive && store.state.drive.state === "ESTOP";
  st.classList.toggle("braking", !!estop || sinceBrake() < 1500);
});
