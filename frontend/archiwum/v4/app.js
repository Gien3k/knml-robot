/* =============================================================================
   Autonomiczny łazik – logika ekranu stoiska (bez frameworków)
   Stan: WebSocket /ws (20 Hz, automatyczne wznawianie). Obraz: MJPEG /stream.mjpg.
   Scena 1920x1080 skalowana do monitora – tak samo jak na drugim monitorze stoiska.
   ============================================================================= */
"use strict";

const $ = (id) => document.getElementById(id);
const C = { route: "#6cb4ff", go: "#3fc27a", stop: "#e5484d", paint: "#f3f1ea", concrete: "#9a9d9f",
  gravel: "#64686b", line: "#2b2f33", pit: "#0f1112", asphalt: "#16181a" };
const BONES = [[0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [17, 18], [18, 19], [19, 20], [0, 17]];
const TIPS = new Set([4, 8, 12, 16, 20]);

let cfg = { max_speed: 0.5, shield_radius_m: 0.3, hfov_deg: 69, hand_near_m: 0.35, hand_far_m: 1.2,
  mirror: true, cam_offset: [0.1, 0] };
let state = null;
let scan = null;
let online = false;
let lastMsg = 0;

const pref = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* tryb prywatny */ } },
};
let mirrorPref = pref.get("mirror");
const mirrored = () => (mirrorPref === null ? !!cfg.mirror : mirrorPref === "1");

// ------------------------------------------------------------------ scena 1920x1080
const stage = $("stage");
let stageScale = 1;
function layout() {
  stageScale = Math.min(innerWidth / 1920, innerHeight / 1080);
  stage.style.transform = `scale(${stageScale})`;
  stage.style.left = `${(innerWidth - 1920 * stageScale) / 2}px`;
  stage.style.top = `${(innerHeight - 1080 * stageScale) / 2}px`;
}
addEventListener("resize", layout);
layout();

// ------------------------------------------------------------------ pomocnicze
function setText(el, v) { if (el.textContent !== v) el.textContent = v; }
function setTone(el, t) { if (el.dataset.tone !== t) el.dataset.tone = t; }
function setHl(el, t) { if ((el.dataset.hl || "") !== (t || "")) { if (t) el.dataset.hl = t; else delete el.dataset.hl; } }
function show(el, on) { el.classList.toggle("show", !!on); }
const fmt = (v, d = 2) => v.toFixed(d).replace(".", ",");
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
function setPill(el, text, tone) { setText(el.lastElementChild, text); setTone(el, tone); }

function osobyLabel(n) {
  if (n === 1) return "osoba przy robocie";
  const d = n % 10, dd = n % 100;
  return (d >= 2 && d <= 4 && !(dd >= 12 && dd <= 14)) ? "osoby przy robocie" : "osób przy robocie";
}

// płótno: rysujemy w pikselach SCENY (1920x1080), ostro na każdym monitorze
function fitCanvas(c) {
  const k = Math.max(1, stageScale * (window.devicePixelRatio || 1));
  const w = Math.max(1, Math.round(c.clientWidth * k));
  const h = Math.max(1, Math.round(c.clientHeight * k));
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  const ctx = c.getContext("2d");
  ctx.setTransform(w / c.clientWidth, 0, 0, h / c.clientHeight, 0, 0);
  return { ctx, W: c.clientWidth, H: c.clientHeight };
}

function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

// ------------------------------------------------------------------ połączenie
let backoff = 400;
function connect() {
  const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
  ws.onopen = () => { online = true; backoff = 400; lastMsg = performance.now(); restartStream(); };
  ws.onmessage = (ev) => {
    lastMsg = performance.now();
    let m;
    try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === "config") { cfg = { ...cfg, ...m }; applyMirror(); }
    else if (m.type === "scan") scan = m;
    else if (m.type === "state") { state = m; updateDom(); }
  };
  ws.onclose = () => {
    online = false; state = null; updateDom();
    setTimeout(connect, backoff);
    backoff = Math.min(3000, backoff * 1.7);
  };
  ws.onerror = () => { try { ws.close(); } catch { /* */ } };
  const guard = setInterval(() => {
    if (ws.readyState !== WebSocket.OPEN) { clearInterval(guard); return; }
    if (performance.now() - lastMsg > 3000) { clearInterval(guard); ws.close(); }
  }, 1000);
}

// ------------------------------------------------------------------ obraz MJPEG
const img = $("stream");
let streamTimer = 0;
function restartStream() { clearTimeout(streamTimer); img.src = `/stream.mjpg?t=${Date.now()}`; }
img.addEventListener("error", () => { clearTimeout(streamTimer); streamTimer = setTimeout(restartStream, 1000); });
function applyMirror() { $("videoInner").classList.toggle("mirror", mirrored()); }

// ------------------------------------------------------------------ DOM (20 Hz)
const el = {
  tileCam: $("tileCam"), tileRadar: $("tileRadar"), tileDrive: $("tileDrive"),
  pillCam: $("pillCam"), pillRadar: $("pillRadar"), pillDrive: $("pillDrive"),
  camWaiting: $("camWaiting"), camBadge: $("camBadge"), mapWaiting: $("mapWaiting"), driveWaiting: $("driveWaiting"),
  rLast: $("rLast"), rRecord: $("rRecord"), rPeople: $("rPeople"), rPeopleLbl: $("rPeopleLbl"), roLast: $("roLast"),
  dots: { oak: $("dotOak"), lidar: $("dotLidar"), esp32: $("dotEsp") },
};
let wasCamLive = false;
let brakeSeen = null, brakeFlashT = 0;

function cameraPill(s) {
  const d = s.drive || {}, h = s.hand;
  if (!(s.cam && s.cam.live)) return ["Czekam na kamerę", "muted"];
  if (h) {
    const dist = h.dist_m != null ? ` · ${fmt(h.dist_m)} m` : "";
    if (d.sub === "too_close") return [`Za blisko${dist}`, "muted"];
    if (d.sub === "too_far") return [`Podejdź bliżej${dist}`, "muted"];
    if (h.gesture === "FIVE" || h.gesture === "FOUR") return [`Otwarta dłoń${dist}`, "go"];
    if (h.gesture === "FIST") return [`Pięść${dist}`, "route"];
    return ["Rozłóż wszystkie palce", "muted"];
  }
  if (s.people && s.people.length) return ["Widzę Cię – pokaż dłoń", "route"];
  return ["Nie widzę dłoni", "muted"];
}

function radarPill(s) {
  const sh = s.shield || {};
  if (!sh.live) return ["Czekam na radar", "muted"];
  if (!sh.ready) return [`Poznaję stoisko ${Math.round((sh.learn || 0) * 100)}%`, "route"];
  if (sh.active) return [sh.nearest_m != null && sh.nearest_m < 1 ? `Przeszkoda · ${Math.round(sh.nearest_m * 100)} cm` : "Przeszkoda", "stop"];
  return ["Droga wolna", "go"];
}

function drivePill(s) {
  const d = s.drive || {};
  if (d.state === "ESTOP") return ["Hamuję!", "stop"];
  if (d.lock === "no_camera") return ["Czekam na kamerę", "muted"];
  if (d.lock) return ["Czekam na radar", "muted"];
  if (d.state === "DRIVE") return [`Jadę · ${Math.round((d.throttle || 0) * 100)}%`, "go"];
  if (d.state === "REVERSE") return ["Cofam", "route"];
  if (d.sub === "attract") return ["Macham kołami", "route"];
  return ["Stoję", "muted"];
}

function updateDom() {
  const s = state;
  if (!s || !online) {
    for (const w of [el.camWaiting, el.mapWaiting, el.driveWaiting]) show(w, true);
    setPill(el.pillCam, "Łączę się z robotem…", "muted");
    setPill(el.pillRadar, "–", "muted");
    setPill(el.pillDrive, "–", "muted");
    for (const t of [el.tileCam, el.tileRadar, el.tileDrive]) setHl(t, "");
    for (const d of Object.values(el.dots)) { d.dataset.state = "sim"; delete d.dataset.starting; }
    wasCamLive = false;
    return;
  }

  // kamera
  const camLive = !!(s.cam && s.cam.live);
  if (camLive && !wasCamLive) restartStream();
  wasCamLive = camLive;
  show(el.camWaiting, !camLive);
  const badge = s.sim ? "SYMULACJA" : (s.cam && s.cam.mode === "depth" ? "TRYB ZAPASOWY" : "");
  el.camBadge.hidden = !badge || !camLive;
  setText(el.camBadge, badge);
  const [ct, ctone] = cameraPill(s);
  setPill(el.pillCam, ct, ctone);
  setHl(el.tileCam, s.hand && camLive ? "route" : "");

  // radar
  const sh = s.shield || {};
  show(el.mapWaiting, !sh.live);
  const [rt, rtone] = radarPill(s);
  setPill(el.pillRadar, rt, rtone);
  setHl(el.tileRadar, sh.live && sh.active ? "stop" : "");

  // napęd i decyzja
  show(el.driveWaiting, !s.wheels);
  const [dt, dtone] = drivePill(s);
  setPill(el.pillDrive, dt, dtone);
  setHl(el.tileDrive, dtone === "stop" ? "stop" : dtone === "go" ? "go" : "");

  // odczyty
  const b = s.brake || {};
  setText(el.rLast, b.last_s != null ? fmt(b.last_s) : "–");
  setText(el.rRecord, b.record_s != null ? fmt(b.record_s) : "–");
  const n = s.people_count;
  setText(el.rPeople, n == null ? "–" : String(n));
  setText(el.rPeopleLbl, n == null ? "osoby przy robocie" : osobyLabel(n));
  if (brakeSeen === null) brakeSeen = b.t;
  if (b.t && b.t !== brakeSeen) { brakeSeen = b.t; brakeFlashT = performance.now(); }
  el.roLast.classList.toggle("flash", performance.now() - brakeFlashT < 2000);

  // kropki diagnostyczne
  for (const [k, node] of Object.entries(el.dots)) {
    const dv = s.devices[k] || { state: "sim" };
    if (node.dataset.state !== dv.state) node.dataset.state = dv.state;
    if (dv.starting) node.dataset.starting = ""; else delete node.dataset.starting;
  }
}

// ------------------------------------------------------------------ szkielet dłoni i osoby na obrazie
function drawOverlay() {
  const { ctx, W, H } = fitCanvas($("overlay"));
  ctx.clearRect(0, 0, W, H);
  if (!(state && state.cam && state.cam.live)) return;
  // obraz ma object-fit: cover – to samo przeliczenie co przeglądarka
  const ia = img.naturalWidth && img.naturalHeight ? img.naturalWidth / img.naturalHeight : 16 / 9;
  const dw = Math.max(W, H * ia), dh = dw / ia;
  const ox = (W - dw) / 2, oy = (H - dh) / 2;
  const map = (x, y) => [ox + x * dw, oy + y * dh];

  // osoby: narożniki ramki + krótki podpis (podpis odwrócony z powrotem, bo obraz jest lustrzany)
  const taken = [];
  for (const p of state.people || []) {
    const [ax0, ay0] = map(p.bbox[0], p.bbox[1]), [ax1, ay1] = map(p.bbox[2], p.bbox[3]);
    const x0 = clamp(ax0, 2, W - 2), x1 = clamp(ax1, 2, W - 2), y0 = clamp(ay0, 2, H - 2), y1 = clamp(ay1, 2, H - 2);
    if (x1 - x0 < 8 || y1 - y0 < 8) continue;
    const L = Math.min(22, (x1 - x0) * 0.25, (y1 - y0) * 0.25);
    ctx.strokeStyle = "rgba(243,241,234,0.85)";
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.beginPath();
    for (const [cx, cy, dx, dy] of [[x0, y0, 1, 1], [x1, y0, -1, 1], [x0, y1, 1, -1], [x1, y1, -1, -1]]) {
      ctx.moveTo(cx + dx * L, cy); ctx.lineTo(cx, cy); ctx.lineTo(cx, cy + dy * L);
    }
    ctx.stroke();
    const text = `osoba${p.dist_m != null ? ` · ${fmt(p.dist_m, 1)} m` : ""}`;
    ctx.save();
    ctx.font = "600 13px Barlow, sans-serif";
    const tw = ctx.measureText(text).width + 14, th = 22;
    let ty = y0 > th + 6 ? y0 - th - 4 : y0 + 6;
    const anchor = mirrored() ? clamp(x1, tw, W) : clamp(x0, 0, W - tw);
    const bx = mirrored() ? anchor - tw : anchor;
    for (let k = 0; k < 6 && taken.some((r) => bx < r[0] + r[2] && bx + tw > r[0] && ty < r[1] + r[3] && ty + th > r[1]); k++) {
      ty = Math.min(H - th, ty + th + 4);
    }
    taken.push([bx, ty, tw, th]);
    ctx.translate(anchor, ty);
    if (mirrored()) ctx.scale(-1, 1);
    ctx.fillStyle = "rgba(15,17,18,0.8)";
    roundRect(ctx, 0, 0, tw, th, th / 2); ctx.fill();
    ctx.fillStyle = C.paint;
    ctx.textBaseline = "middle";
    ctx.fillText(text, 7, th / 2 + 0.5);
    ctx.restore();
  }

  const h = state.hand;
  if (!h) return;
  if (h.landmarks && h.landmarks.length === 21) {
    const p = h.landmarks.map(([x, y]) => map(x, y));
    ctx.lineJoin = ctx.lineCap = "round";
    ctx.fillStyle = "rgba(108,180,255,0.16)";
    ctx.beginPath();
    [0, 1, 5, 9, 13, 17].forEach((i, k) => (k ? ctx.lineTo(...p[i]) : ctx.moveTo(...p[i])));
    ctx.closePath(); ctx.fill();
    ctx.strokeStyle = "rgba(15,17,18,0.55)"; ctx.lineWidth = 6;
    ctx.beginPath();
    for (const [a, b] of BONES) { ctx.moveTo(...p[a]); ctx.lineTo(...p[b]); }
    ctx.stroke();
    ctx.strokeStyle = C.route; ctx.lineWidth = 3; ctx.stroke();
    p.forEach(([x, y], i) => {
      ctx.beginPath(); ctx.arc(x, y, TIPS.has(i) ? 5 : 3.5, 0, Math.PI * 2);
      ctx.fillStyle = TIPS.has(i) ? C.route : C.paint; ctx.fill();
      if (TIPS.has(i)) { ctx.lineWidth = 1.5; ctx.strokeStyle = C.paint; ctx.stroke(); }
    });
  } else if (h.bbox) {
    const [a0, a1] = [map(h.bbox[0], h.bbox[1]), map(h.bbox[2], h.bbox[3])];
    const L = Math.min(a1[0] - a0[0], a1[1] - a0[1]) * 0.28;
    ctx.strokeStyle = C.route; ctx.lineWidth = 3;
    ctx.beginPath();
    for (const [cx, cy, dx, dy] of [[a0[0], a0[1], 1, 1], [a1[0], a0[1], -1, 1], [a0[0], a1[1], 1, -1], [a1[0], a1[1], -1, -1]]) {
      ctx.moveTo(cx + dx * L, cy); ctx.lineTo(cx, cy); ctx.lineTo(cx, cy + dy * L);
    }
    ctx.stroke();
  }
}

// ------------------------------------------------------------------ radar z lotu ptaka
// Układ robota: X do przodu (do odwiedzających), Y w lewo. Przód robota NA DOLE ekranu,
// więc scena wygląda "od strony widza", zgodnie z lustrzanym obrazem kamery.
function drawMap(t) {
  const { ctx, W, H } = fitCanvas($("map"));
  ctx.clearRect(0, 0, W, H);
  if (!state) return;
  const sh = state.shield || {};
  // skala "rybie oko": do 1 m liniowo, dalej ściśnięta – ludzie do ~3 m też się mieszczą
  const comp = (r) => (r <= 1 ? r : 1 + (r - 1) * 0.45);
  const cx = W / 2, cy = H * 0.3;
  const sc = Math.min((H - cy - 10) / comp(3.0), (W / 2 - 10) / comp(2.6), (cy - 4) / 0.55);
  const P = (X, Y) => { const r = Math.hypot(X, Y); const f = r > 1e-9 ? comp(r) / r : 1; return [cx + Y * f * sc, cy + X * f * sc]; };

  // pierścienie odległości (jeden podpis wystarczy)
  ctx.lineWidth = 1;
  for (const r of [0.5, 1, 2, 3]) {
    ctx.strokeStyle = "rgba(243,241,234,0.06)";
    ctx.beginPath(); ctx.arc(cx, cy, comp(r) * sc, 0, Math.PI * 2); ctx.stroke();
  }
  ctx.fillStyle = C.gravel; ctx.font = "500 13px Barlow, sans-serif"; ctx.textBaseline = "top"; ctx.textAlign = "left";
  ctx.fillText("1 m", cx + comp(1) * sc * Math.cos(0.35) + 4, cy + comp(1) * sc * Math.sin(0.35));

  // pole widzenia kamery i strefa sterowania dłonią
  const [ox, oy] = cfg.cam_offset || [0.1, 0];
  const half = ((cfg.hfov_deg || 69) * Math.PI) / 360;
  const band = (r0, r1) => {
    ctx.beginPath();
    for (let k = 0; k <= 24; k++) { const a = -half + (2 * half * k) / 24; ctx.lineTo(...P(ox + r1 * Math.cos(a), oy + r1 * Math.sin(a))); }
    for (let k = 24; k >= 0; k--) { const a = -half + (2 * half * k) / 24; ctx.lineTo(...P(ox + r0 * Math.cos(a), oy + r0 * Math.sin(a))); }
    ctx.closePath();
  };
  band(cfg.hand_near_m || 0.35, cfg.hand_far_m || 1.2);
  ctx.fillStyle = "rgba(108,180,255,0.07)"; ctx.fill();

  // tarcza: strefa hamowania
  const R = (sh.radius_m || cfg.shield_radius_m || 0.3) * sc;
  if (sh.live) {
    if (!sh.ready) {
      ctx.strokeStyle = "rgba(108,180,255,0.3)"; ctx.lineWidth = 2; ctx.setLineDash([5, 5]);
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
      ctx.strokeStyle = C.route; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.arc(cx, cy, R, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * (sh.learn || 0)); ctx.stroke();
    } else if (sh.active) {
      ctx.fillStyle = "rgba(229,72,77,0.16)";
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = C.stop; ctx.lineWidth = 2.5; ctx.stroke();
    } else {
      ctx.strokeStyle = "rgba(63,194,122,0.7)"; ctx.lineWidth = 2; ctx.setLineDash([6, 5]);
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
    }
  }
  const hits = sh.live && sh.intrusion ? sh.intrusion : [];
  if (hits.length) {
    const set = new Set(hits);
    ctx.fillStyle = "rgba(229,72,77,0.35)";
    for (const s0 of hits.filter((i) => !set.has((i + 359) % 360))) {
      let e = s0;
      while (set.has((e + 1) % 360) && (e + 1) % 360 !== s0) e = (e + 1) % 360;
      const a0 = ((s0 - 4) * Math.PI) / 180, span = ((((e - s0 + 360) % 360) + 8) * Math.PI) / 180;
      ctx.beginPath(); ctx.moveTo(cx, cy);
      for (let k = 0; k <= 16; k++) { const a = a0 + (span * k) / 16; ctx.lineTo(...P(Math.cos(a) * R / sc * 1.15, Math.sin(a) * R / sc * 1.15)); }
      ctx.closePath(); ctx.fill();
    }
  }

  // dłoń z kamery: poświata pod punktami radaru
  const h = state.cam && state.cam.live ? state.hand : null;
  const hp = h && h.map ? P(h.map[0], h.map[1]) : null;
  if (hp) {
    const pulse = 0.5 + 0.5 * Math.sin(t / 220);
    ctx.fillStyle = `rgba(108,180,255,${0.12 + 0.1 * pulse})`;
    ctx.beginPath(); ctx.arc(hp[0], hp[1], 16 + 6 * pulse, 0, Math.PI * 2); ctx.fill();
  }

  // punkty radaru
  if (scan && sh.live) {
    const fg = new Set(scan.fg || []), hit = new Set(hits);
    for (let i = 0; i < 360; i++) {
      const r = scan.r[i] / 1000;
      if (r <= 0 || r > 4.5) continue;
      const a = (i * Math.PI) / 180;
      const [x, y] = P(r * Math.cos(a), r * Math.sin(a));
      if (x < -4 || y < -4 || x > W + 4 || y > H + 4) continue;
      const isHit = hit.has(i), isFg = fg.has(i);
      ctx.fillStyle = isHit ? C.stop : isFg ? C.paint : "rgba(154,157,159,0.45)";
      ctx.beginPath(); ctx.arc(x, y, isHit ? 3.4 : isFg ? 2.6 : 1.8, 0, Math.PI * 2); ctx.fill();
    }
  }

  // ludzie: tylko potwierdzeni przez kamerę dostają podpis
  const placed = [];
  const place = (x, y, w, hgt, right) => {
    for (const dy of [0, 16, -16, 32, -32]) {
      const bx = right ? x - w : x, by = y + dy - hgt / 2;
      if (!placed.some((p) => bx < p[0] + p[2] && bx + w > p[0] && by < p[1] + p[3] && by + hgt > p[1])) { placed.push([bx, by, w, hgt]); return y + dy; }
    }
    return null;
  };
  ctx.font = "600 14px Barlow, sans-serif"; ctx.textBaseline = "middle";
  if (hp) place(hp[0] + 14, hp[1], ctx.measureText("dłoń").width, 16, false);
  if (scan && sh.live) {
    for (const tr of scan.tracks || []) {
      if (tr.kind !== "person") continue;
      const col = tr.fused ? "108,180,255" : "154,157,159";
      if (tr.trail && tr.trail.length > 1) {
        ctx.strokeStyle = `rgba(${col},0.3)`; ctx.lineWidth = 2;
        ctx.beginPath();
        tr.trail.forEach(([X, Y], k) => (k ? ctx.lineTo(...P(X, Y)) : ctx.moveTo(...P(X, Y))));
        ctx.lineTo(...P(tr.x, tr.y)); ctx.stroke();
      }
      const [px, py] = P(tr.x, tr.y);
      const rad = clamp(tr.w * 0.5 * sc * (tr.d > 1 ? 0.45 : 1), 7, 15);
      ctx.fillStyle = `rgba(${col},${tr.fused ? 0.25 : 0.12})`;
      ctx.strokeStyle = tr.fused ? C.route : "rgba(154,157,159,0.8)";
      ctx.lineWidth = tr.fused ? 2.5 : 1.5;
      ctx.beginPath(); ctx.arc(px, py, rad, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      if (tr.fused) {
        const right = px > W * 0.72;
        const text = `osoba · ${fmt(tr.d, 1)} m`;
        const lx = px + (right ? -1 : 1) * (rad + 6);
        const ly = place(lx, py, ctx.measureText(text).width, 16, right);
        if (ly !== null) { ctx.fillStyle = C.route; ctx.textAlign = right ? "right" : "left"; ctx.fillText(text, lx, ly); }
      }
    }
  }

  // robot
  const bl = 0.125, bw = 0.10;
  const [rx0, ry0] = P(-bl, -bw), [rx1, ry1] = P(bl, bw);
  ctx.fillStyle = "#24282c"; ctx.strokeStyle = "rgba(243,241,234,0.4)"; ctx.lineWidth = 1.5;
  roundRect(ctx, rx0, ry0, rx1 - rx0, ry1 - ry0, 4); ctx.fill(); ctx.stroke();
  ctx.strokeStyle = C.route; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(...P(bl, -bw * 0.7)); ctx.lineTo(...P(bl, bw * 0.7)); ctx.stroke();

  // dłoń z kamery: rdzeń nad punktami + podpis
  if (hp) {
    ctx.fillStyle = C.route;
    ctx.beginPath(); ctx.arc(hp[0], hp[1], 6, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = C.paint; ctx.lineWidth = 2; ctx.stroke();
    ctx.fillStyle = C.paint; ctx.textAlign = "left"; ctx.font = "600 14px Barlow, sans-serif";
    ctx.fillText("dłoń", hp[0] + 14, hp[1]);
  }
}

// ------------------------------------------------------------------ podwozie z kołami
const treadPhase = { L: 0, R: 0 };
function drawChassis(dt) {
  const { ctx, W, H } = fitCanvas($("chassis"));
  ctx.clearRect(0, 0, W, H);
  const wh = state && state.wheels;
  const max = cfg.max_speed || 0.5;
  const sp = { L: wh ? clamp(wh.L / max, -1, 1) : 0, R: wh ? clamp(wh.R / max, -1, 1) : 0 };
  const d = (state && state.drive) || {};
  const estop = d.state === "ESTOP";

  const bodyW = Math.min(W * 0.3, H * 0.52), bodyL = bodyW * 1.3;
  const cx = W / 2, cy = H / 2;
  const ww = bodyW * 0.3, wl = bodyL * 0.36, gap = bodyW * 0.07;

  // nadwozie (przód = dół ekranu, od strony widza)
  ctx.fillStyle = "#1d2023";
  ctx.strokeStyle = estop ? C.stop : "rgba(243,241,234,0.22)";
  ctx.lineWidth = estop ? 2.5 : 1.5;
  roundRect(ctx, cx - bodyW / 2, cy - bodyL / 2, bodyW, bodyL, 12); ctx.fill(); ctx.stroke();
  ctx.strokeStyle = "rgba(243,241,234,0.16)"; ctx.lineWidth = 1.5;
  ctx.beginPath(); ctx.arc(cx, cy - bodyL * 0.08, bodyW * 0.2, 0, Math.PI * 2); ctx.stroke();
  ctx.fillStyle = C.route;
  roundRect(ctx, cx - bodyW * 0.28, cy + bodyL / 2 - 12, bodyW * 0.56, 7, 3); ctx.fill();

  // koła: lewa strona ekranu = prawe koła robota (R), prawa = lewe (L)
  ctx.font = "600 22px 'Barlow Condensed', sans-serif";
  for (const [side, x] of [["R", cx - bodyW / 2 - gap - ww], ["L", cx + bodyW / 2 + gap]]) {
    const v = estop ? 0 : sp[side];
    treadPhase[side] += v * dt * 160;
    const tone = v > 0.02 ? C.go : v < -0.02 ? C.route : null;
    for (const y of [cy - bodyL / 2 + bodyL * 0.04, cy + bodyL / 2 - bodyL * 0.04 - wl]) {
      ctx.save();
      roundRect(ctx, x, y, ww, wl, 8);
      ctx.fillStyle = "#24282c"; ctx.fill(); ctx.clip();
      const step = 11, off = ((treadPhase[side] % step) + step) % step;
      ctx.strokeStyle = tone || "rgba(243,241,234,0.12)";
      ctx.globalAlpha = tone ? 0.35 + 0.65 * Math.abs(v) : 1;
      ctx.lineWidth = 3;
      ctx.beginPath();
      for (let ty = y - step + off; ty < y + wl + step; ty += step) { ctx.moveTo(x + 4, ty); ctx.lineTo(x + ww - 4, ty); }
      ctx.stroke();
      ctx.restore();
      ctx.strokeStyle = estop ? C.stop : (tone || "rgba(243,241,234,0.16)"); ctx.lineWidth = 1.5;
      roundRect(ctx, x, y, ww, wl, 8); ctx.stroke();
    }
    // prędkość strony w procentach, obok kół
    const pct = Math.round(v * 100);
    ctx.fillStyle = tone || C.gravel;
    ctx.textAlign = side === "R" ? "right" : "left"; ctx.textBaseline = "middle";
    ctx.fillText(`${pct > 0 ? "+" : pct < 0 ? "−" : ""}${Math.abs(pct)}%`, side === "R" ? x - 14 : x + ww + 14, cy);
  }
}

// ------------------------------------------------------------------ pętla rysowania (~30 kl./s)
let lastT = performance.now(), frameN = 0;
function frame(t) {
  const dt = Math.min(0.1, (t - lastT) / 1000);
  lastT = t;
  if ((frameN++ & 1) === 0) {
    drawOverlay();
    drawMap(t);
    drawChassis(dt * 2);
  }
  requestAnimationFrame(frame);
}

// ------------------------------------------------------------------ obsługa (opcjonalna klawiatura)
addEventListener("keydown", (e) => {
  const k = e.key.toLowerCase();
  if (k === "r") fetch("/api/recalibrate", { method: "POST" }).catch(() => {});
  else if (k === "m") { mirrorPref = mirrored() ? "0" : "1"; pref.set("mirror", mirrorPref); applyMirror(); }
  else if (k === "f") {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else document.documentElement.requestFullscreen().catch(() => {});
  }
});

// logotypy z folderu assets/ podmieniają się bez przeładowania strony
function refreshLogo(id, name) {
  const probe = new Image();
  probe.onload = () => { $(id).src = probe.src; };
  probe.src = `/branding/${name}?v=${Date.now()}`;
}
setInterval(() => { refreshLogo("logoKolo", "kolo"); refreshLogo("logoRzeszow", "rzeszow"); }, 30000);

applyMirror();
updateDom();
connect();
requestAnimationFrame(frame);
