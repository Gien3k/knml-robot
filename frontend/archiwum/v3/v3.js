import { $, clamp, connect, controls, drawHand, drawRadar, fmt, gestureText, loop, zatrzymania, onState, phase, sinceBrake, stage, store } from "../../shared/kn.js";

const HAND = { hand: "#f2b33d", handShadow: "rgba(8,9,10,0.6)", joint: "#f4f4f2", people: "rgba(244,244,242,0.6)" };
const RADAR = {
  bg: "#16181c", ring: "rgba(244,244,242,0.08)", label: "#80858d", fov: "rgba(108,180,255,0.08)", route: "#6cb4ff",
  stop: "#e5484d", stopFill: "rgba(229,72,77,0.25)", zone: "rgba(63,194,122,0.8)", dot: "#f4f4f2",
  dotBg: "rgba(154,157,159,0.42)", personFill: "rgba(108,180,255,0.22)", robot: "#24282c", robotEdge: "rgba(244,244,242,0.55)",
};
const HUMAN_S = 0.25;      // typowy czas prostej reakcji wzrokowej człowieka
const SCALE_S = 0.4;       // pełna długość paska

stage($("stage"));
const img = $("cam");
connect(img);
controls();

const setText = (id, v) => { const n = $(id); if (n.textContent !== v) n.textContent = v; };
const setTone = (id, t) => { const n = $(id); if (n.dataset.tone !== t) n.dataset.tone = t; };
$("barHuman").style.width = `${(HUMAN_S / SCALE_S) * 100}%`;

function update() {
  const s = store.state;
  const p = phase(s);
  const d = (s && s.drive) || {}, sh = (s && s.shield) || {}, b = (s && s.brake) || {};

  // Ty – kamera i gest
  if (!s) { setText("youState", "Łączę się z robotem"); setTone("youState", "muted"); }
  else if (d.state === "DRIVE") { setText("youState", `Jedziesz ${Math.round((d.throttle || 0) * 100)}%`); setTone("youState", "go"); }
  else if (d.state === "REVERSE") { setText("youState", "Cofasz"); setTone("youState", "route"); }
  else if (gestureText(s)) { setText("youState", p.step === 1 ? p.title : gestureText(s)); setTone("youState", "muted"); }
  else { setText("youState", "Pokaż otwartą dłoń"); setTone("youState", "muted"); }

  // AI – radar
  const hit = p.key === "braked" || p.key === "estop";
  if (!sh.live) { setText("aiState", "Czekam na radar"); setTone("aiState", "muted"); }
  else if (!sh.ready) { setText("aiState", `Poznaję stoisko ${Math.round((sh.learn || 0) * 100)}%`); setTone("aiState", "route"); }
  else if (hit) { setText("aiState", "Hamuję"); setTone("aiState", "stop"); }
  else { setText("aiState", "Droga wolna"); setTone("aiState", "go"); }
  document.querySelector(".side.ai").classList.toggle("alert", hit);

  // wynik
  const last = b.last_s;
  setText("score", last != null ? fmt(last) : "–");
  setText("scoreLabel", last != null ? "Robot zatrzymał się w" : "Robot jeszcze nie hamował");
  document.querySelector(".score").classList.toggle("hit", sinceBrake() < 1500);
  $("barAi").style.width = last != null ? `${clamp(last / SCALE_S, 0.02, 1) * 100}%` : "0%";
  setText("barAiV", last != null ? `${fmt(last)} s` : "–");
  setText("record", b.record_s != null ? `${fmt(b.record_s)} s` : "–");
  setText("count", b.count != null ? String(b.count) : "–");
  setText("countLbl", b.count != null ? zatrzymania(b.count) : "zatrzymań dziś");
  setText("how", p.key === "learn" || p.key === "wait" || p.key === "offline" ? p.line
    : "Otwarta dłoń przy kamerze to gaz. Włóż rękę przy robocie i sprawdź, kto szybszy.");
}
onState(update);
setInterval(update, 200);

loop(() => {
  drawHand($("ov"), img, HAND);
  drawRadar($("radar"), RADAR, { cy: 0.4 });
});
