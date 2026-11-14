import { $, connect, controls, drawChassis, drawHand, drawRadar, fmt, gestureText, loop, zatrzymania, onState, phase, stage, store } from "../../shared/kn.js";

const HAND = { hand: "#6cb4ff", handShadow: "rgba(8,9,10,0.55)", joint: "#ffffff", people: "rgba(255,255,255,0.7)" };
const RADAR = {
  bg: "#f4f5f7", ring: "rgba(17,20,26,0.09)", label: "#8a909c", fov: "rgba(36,87,245,0.07)", route: "#2457f5",
  stop: "#d42a3c", stopFill: "rgba(212,42,60,0.16)", zone: "rgba(18,128,74,0.85)", dot: "#11141a",
  dotBg: "rgba(17,20,26,0.3)", personFill: "rgba(36,87,245,0.16)", robot: "#ffffff", robotEdge: "#11141a",
};
const CHASSIS = { body: "#ffffff", bodyEdge: "rgba(17,20,26,0.35)", route: "#2457f5", go: "#12804a", stop: "#d42a3c",
  wheel: "#dfe2e7", treadIdle: "rgba(17,20,26,0.14)" };
const NAMES = [["oak", "Kamera"], ["lidar", "Radar"], ["esp32", "Napęd"]];

stage($("stage"));
const img = $("cam");
connect(img);
controls();

const setText = (id, v) => { const n = $(id); if (n.textContent !== v) n.textContent = v; };
const setTone = (id, t) => { const n = $(id); if (n.dataset.tone !== t) n.dataset.tone = t; };

function update() {
  const s = store.state;
  const p = phase(s);
  for (const k of [1, 2, 3]) $(`step${k}`).classList.toggle("on", p.step === k);

  // krok 1: dłoń
  const g = gestureText(s);
  if (!s) { setText("st1", "Łączę się z robotem"); setTone("st1", "muted"); }
  else if (p.step === 1) { setText("st1", p.title); setTone("st1", p.tone); }
  else { setText("st1", g || "Dłoń rozpoznana"); setTone("st1", "go"); }

  // krok 2: jazda
  const d = (s && s.drive) || {};
  const thr = Math.round((d.throttle || 0) * 100);
  setText("throttle", d.state === "REVERSE" ? "wstecz" : `${thr}%`);
  if (d.state === "DRIVE") { setText("st2", "Jadę do przodu"); setTone("st2", "go"); }
  else if (d.state === "REVERSE") { setText("st2", "Cofam"); setTone("st2", "route"); }
  else { setText("st2", "Koła stoją"); setTone("st2", "muted"); }

  // krok 3: hamowanie
  const sh = (s && s.shield) || {};
  if (p.key === "braked" || p.key === "estop") { setText("st3", p.title); setTone("st3", "stop"); }
  else if (sh.live && !sh.ready) { setText("st3", p.key === "learn" ? p.line : "Radar się uczy"); setTone("st3", "route"); }
  else if (sh.live) { setText("st3", "Droga wolna"); setTone("st3", "go"); }
  else { setText("st3", "Czekam na radar"); setTone("st3", "muted"); }

  const b = (s && s.brake) || {};
  setText("record", b.record_s != null ? `${fmt(b.record_s)} s` : "–");
  setText("count", b.count != null ? String(b.count) : "–");
  setText("countLbl", b.count != null ? zatrzymania(b.count) : "zatrzymań dziś");
  const dev = s ? NAMES.map(([k, n]) => `<span><i class="${(s.devices[k] || {}).state || ""}"></i>${n}</span>`).join("")
    + (s.sim ? "<span>symulacja</span>" : "") : "";
  if ($("devices").innerHTML !== dev) $("devices").innerHTML = dev;
}
onState(update);
setInterval(update, 200);

loop((dt) => {
  drawHand($("ov"), img, HAND);
  drawChassis($("chassis"), dt, CHASSIS);
  drawRadar($("radar"), RADAR, { cy: 0.4 });
});
