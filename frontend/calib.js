/* =============================================================================
   Ukryta kalibracja radaru: 5 szybkich kliknięć w logo Koła -> hasło -> kreator.
   Hasło sprawdza serwer (/api/calibration/start). Kreator prowadzi krok po kroku,
   pokazuje procent postępu i rysunek, gdzie stanąć. W trakcie kalibracji koła stoją.
   ============================================================================= */
import { $, fitCanvas, roundRect } from "./shared/kn.js";

const KEY_ROWS = ["QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"];
const TAPS = 5, TAP_WINDOW_MS = 3000;
let pw = "", authOpen = false, calib = null;

const setText = (id, v) => { const n = $(id); if (n.textContent !== v) n.textContent = v; };
const post = (path, body) => fetch(path, {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
});

function modalState() {
  const on = authOpen || !!(calib && (calib.active || calib.done));
  if (on) document.body.dataset.modal = "1"; else delete document.body.dataset.modal;
}

// ------------------------------------------------------------------ hasło
function renderPw() {
  setText("authField", "•".repeat(pw.length));
  if (pw) setText("authError", "");            // nowe wpisywanie chowa stary komunikat
}
function openAuth() {
  pw = ""; renderPw(); setText("authError", "");
  authOpen = true; $("calibAuth").hidden = false; modalState();
}
function closeAuth() { authOpen = false; pw = ""; $("calibAuth").hidden = true; modalState(); }
async function submit() {
  if (!pw) return;
  try {
    const r = await post("/api/calibration/start", { password: pw });
    if (r.ok) { closeAuth(); return; }
    setText("authError", "Złe hasło. Spróbuj jeszcze raz.");
  } catch {
    setText("authError", "Brak połączenia z robotem.");
  }
  pw = ""; renderPw();
  const box = $("authBox");
  box.classList.remove("shake"); void box.offsetWidth; box.classList.add("shake");
}

function buildKeys() {
  const keys = $("authKeys");
  KEY_ROWS.forEach((row, i) => {
    const r = document.createElement("div");
    r.className = "row";
    for (const ch of row) {
      const b = document.createElement("button");
      b.type = "button"; b.textContent = ch;
      b.addEventListener("click", () => { if (pw.length < 20) { pw += ch; renderPw(); } });
      r.appendChild(b);
    }
    if (i === KEY_ROWS.length - 1) {
      const back = document.createElement("button");
      back.type = "button"; back.className = "wide"; back.textContent = "⌫ usuń";
      back.addEventListener("click", () => { pw = pw.slice(0, -1); renderPw(); });
      r.appendChild(back);
    }
    keys.appendChild(r);
  });
}

export function initCalib() {
  buildKeys();
  let taps = [];
  $("logoKolo").addEventListener("pointerdown", () => {
    const now = performance.now();
    taps = taps.filter((t) => now - t < TAP_WINDOW_MS);
    taps.push(now);
    if (taps.length >= TAPS && !authOpen && !(calib && calib.active)) { taps = []; openAuth(); }
  });
  $("authCancel").addEventListener("click", closeAuth);
  $("authOk").addEventListener("click", submit);
  addEventListener("keydown", (e) => {
    if (!authOpen) return;
    e.stopImmediatePropagation();
    if (e.key === "Enter") submit();
    else if (e.key === "Escape") closeAuth();
    else if (e.key === "Backspace") { pw = pw.slice(0, -1); renderPw(); }
    else if (/^[a-z0-9]$/i.test(e.key) && pw.length < 20) { pw += e.key.toUpperCase(); renderPw(); }
  }, true);
  $("calibCancel").addEventListener("click", () => post("/api/calibration/cancel"));
  $("calibSkip").addEventListener("click", () => post("/api/calibration/skip"));
  $("calibClose").addEventListener("click", () => post("/api/calibration/close"));
}

// ------------------------------------------------------------------ kreator
const fmtDeg = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(Math.round(v))}°`;

export function renderCalib(c) {
  calib = c || null;
  const show = !!(calib && (calib.active || calib.done));
  $("calib").hidden = !show;
  modalState();
  if (!show) return;
  const box = document.querySelector(".calib-box");
  box.classList.toggle("done", !!calib.done);
  $("calibCancel").hidden = !!calib.done;
  $("calibClose").hidden = !calib.done;
  $("calibSkip").hidden = !calib.can_skip;
  const pct = Math.max(0, Math.min(100, calib.progress || 0));
  $("calibBar").style.width = `${pct}%`;
  setText("calibPct", `${pct}%`);

  if (calib.done) {
    const r = calib.result || {};
    setText("calibStep", "Gotowe");
    setText("calibTitle", "Kalibracja zakończona");
    setText("calibHint", calib.saved ? "Ustawienie jest zapisane i zostanie po restarcie robota." : "Uwaga: nie udało się zapisać ustawienia na dysku.");
    const items = [
      [`Kąt radaru: ${fmtDeg(r.offset_deg || 0)}`, true],
      [r.flipped ? "Kierunek obrotu radaru: poprawiony" : "Kierunek obrotu radaru: prawidłowy", true],
      [r.brake_ok ? (r.brake_front ? "Hamowanie: działa" : "Hamowanie: działa (dłoń wykryta z boku)") : "Hamowanie: nie sprawdzone", !!r.brake_ok],
    ];
    if (r.residual_deg != null) items.push([`Dokładność dopasowania: ±${Math.round(r.residual_deg)}°`, r.residual_deg <= 8]);
    const html = items.map(([t, ok]) => `<li class="${ok ? "ok" : ""}">${t}</li>`).join("");
    if ($("calibChecks").innerHTML !== html) $("calibChecks").innerHTML = html;
    setText("calibMsg", "");
    setText("calibCount", "");
    return;
  }
  setText("calibStep", `Krok ${calib.index} z ${calib.total}`);
  setText("calibTitle", calib.title || "");
  setText("calibHint", calib.hint || "");
  const html = (calib.checks || []).map((ch) =>
    `<li class="${ch.ok === true ? "ok" : ch.ok === null ? "wait" : ""}">${ch.label}</li>`).join("");
  if ($("calibChecks").innerHTML !== html) $("calibChecks").innerHTML = html;
  setText("calibMsg", calib.message || "");
  setText("calibCount", calib.countdown != null && calib.countdown > 0 ? String(calib.countdown) : "");
}

// rysunek "gdzie stanąć" – widok z góry, przód robota na dole (od strony widza)
export function drawCalibDiagram(t) {
  if (!calib || !(calib.active || calib.done) || $("calib").hidden) return;
  const { ctx, W, H } = fitCanvas($("calibDiagram"));
  ctx.clearRect(0, 0, W, H);
  const step = calib.done ? "done" : calib.step;
  const cx = W / 2, cy = H * 0.3, m = 110;             // m = piksele na metr
  // pole widzenia kamery
  ctx.fillStyle = "rgba(108,180,255,0.10)";
  ctx.beginPath(); ctx.moveTo(cx, cy + 22);
  ctx.lineTo(cx - Math.tan(0.6) * 260, cy + 22 + 260); ctx.lineTo(cx + Math.tan(0.6) * 260, cy + 22 + 260);
  ctx.closePath(); ctx.fill();
  // strefa 2 m (kroki "odsuń się")
  if (step === "clear" || step === "relearn") {
    ctx.strokeStyle = "rgba(242,179,61,0.8)"; ctx.setLineDash([8, 8]); ctx.lineWidth = 2.5;
    ctx.beginPath(); ctx.arc(cx, cy, 1.6 * m, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = "#9a9d9f"; ctx.font = "500 18px Barlow, sans-serif"; ctx.textAlign = "center";
    ctx.fillText("2 m", cx + 1.6 * m * 0.72, cy - 1.6 * m * 0.72 - 6);
  }
  // strefa hamowania
  if (step === "brake") {
    ctx.fillStyle = "rgba(229,72,77,0.18)"; ctx.strokeStyle = "#e5484d"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(cx, cy, 0.42 * m, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
  }
  // robot
  ctx.fillStyle = "#2b2f33"; ctx.strokeStyle = "#f3f1ea"; ctx.lineWidth = 2;
  roundRect(ctx, cx - 26, cy - 32, 52, 64, 8); ctx.fill(); ctx.stroke();
  ctx.fillStyle = "#6cb4ff"; roundRect(ctx, cx - 16, cy + 24, 32, 6, 3); ctx.fill();   // kamera z przodu
  // osoba / dłoń
  const person = (x, y, col) => {
    ctx.fillStyle = col;
    ctx.beginPath(); ctx.arc(x, y, 15, 0, Math.PI * 2); ctx.fill();
    roundRect(ctx, x - 28, y + 18, 56, 22, 11); ctx.fill();
  };
  if (step === "front") person(cx, cy + 1.0 * m + 40, "#6cb4ff");
  if (step === "side") person(cx + Math.sin(t / 700) * 0.55 * m, cy + 1.0 * m + 40, "#6cb4ff");
  if (step === "clear" || step === "relearn") {
    const a = (t / 2200) % (Math.PI * 2);
    person(cx + Math.cos(a) * 1.85 * m, cy + Math.sin(a) * 1.85 * m * 0.9 + 20, "#9a9d9f");
  }
  if (step === "brake") {
    const y = cy + 0.25 * m + 30 + Math.sin(t / 300) * 4;
    ctx.fillStyle = "#f3f1ea";
    ctx.beginPath(); ctx.arc(cx, y, 11, 0, Math.PI * 2); ctx.fill();
  }
  if (step === "done") {
    ctx.strokeStyle = "#3fc27a"; ctx.lineWidth = 7; ctx.lineCap = "round"; ctx.lineJoin = "round";
    ctx.beginPath(); ctx.moveTo(cx - 40, cy + 170); ctx.lineTo(cx - 10, cy + 200); ctx.lineTo(cx + 45, cy + 140); ctx.stroke();
  }
}
