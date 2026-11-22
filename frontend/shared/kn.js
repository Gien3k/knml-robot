/* =============================================================================
   Wspólna warstwa ekranu stoiska (i wariantów w archiwum/).
   Dane: WebSocket /ws (stan 20 Hz, skan LiDAR, konfiguracja), obraz: /stream.mjpg,
   "jak widzi robot" (głębia / podczerwień): /vision.mjpg.
   Każdy wariant ma własny układ i kolory, ale te same dane i te same komunikaty.
   Klawisze: M lustro kamery, R kalibracja radaru, F pełny ekran.
   ============================================================================= */

export const $ = (id) => document.getElementById(id);
export const fmt = (v, d = 2) => v.toFixed(d).replace(".", ",");
export const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

export const store = {
  state: null, scan: null, online: false, scale: 1,
  cfg: { max_speed: 0.5, shield_radius_m: 0.3, hfov_deg: 69, hand_near_m: 0.35, hand_far_m: 1.2, mirror: true, cam_offset: [0.1, 0] },
  brakeSeen: null, brakeAt: 0,
  stale: false,           // serwer ma nowszą wersję ekranu -> przeładowanie przy najbliższej okazji
};

const listeners = [];
export function onState(fn) { listeners.push(fn); }

// lustro ustawia serwer, wspólne dla wszystkich otwartych stron (dawne ustawienie z przeglądarki usuwamy)
try { localStorage.removeItem("mirror"); } catch { /* tryb prywatny */ }
/** Prędkość, którą robot właśnie wysyła do kół (po łagodnym rozruchu): -1..1 względem MAX_SPEED.
 *  To samo źródło dla napisu "Jadę 50%" i dla panelu radaru, więc wartości zawsze się zgadzają. */
export function wheelSpeed(s) {
  const c = s && s.drive && s.drive.cmd;
  if (!c) return 0;
  return (c.L + c.R) / 2 / (store.cfg.max_speed || 0.5);
}
export const pct10 = (v) => Math.round(Math.abs(v) * 10) * 10;

export const mirrored = () => (store.state && store.state.mirror != null ? !!store.state.mirror : !!store.cfg.mirror);
/** Numer bieżącego ekranu (serwer pomija wybory zrobione na ekranie, którego już nie ma). */
export const sceneQuery = () => {
  const sc = store.state && store.state.mode && store.state.mode.scene;
  return sc != null ? `?scene=${sc}` : "";
};

// ------------------------------------------------------------------ scena 1920x1080
export function stage(el) {
  const fit = () => {
    const s = Math.min(innerWidth / 1920, innerHeight / 1080);
    store.scale = s;
    el.style.transform = `scale(${s})`;
    el.style.left = `${(innerWidth - 1920 * s) / 2}px`;
    el.style.top = `${(innerHeight - 1080 * s) / 2}px`;
  };
  addEventListener("resize", fit);
  fit();
}

export function fitCanvas(c) {
  const k = Math.max(1, store.scale * (window.devicePixelRatio || 1));
  const W = c.clientWidth, H = c.clientHeight;
  const w = Math.max(1, Math.round(W * k)), h = Math.max(1, Math.round(H * k));
  if (c.width !== w || c.height !== h) { c.width = w; c.height = h; }
  const ctx = c.getContext("2d");
  ctx.setTransform(w / Math.max(1, W), 0, 0, h / Math.max(1, H), 0, 0);
  return { ctx, W, H };
}

export function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

// ------------------------------------------------------------------ połączenie i obraz
let img = null, streamTimer = 0, vimg = null, visionTimer = 0;
function restartStream() {
  if (!img) return;
  clearTimeout(streamTimer);
  img.src = `/stream.mjpg?t=${Date.now()}`;
}
function restartVision() {
  if (!vimg) return;
  clearTimeout(visionTimer);
  vimg.src = `/vision.mjpg?t=${Date.now()}`;
}

/** cameraImg: <img> obrazu z kamery; visionImg (opcjonalnie): <img> głębi / podczerwieni. */
/** Strona zgłasza serwerowi swoją wersję (GET /api/clients pokazuje, czy któraś karta ma stary kod). */
const PAGE_ID = Math.random().toString(36).slice(2, 10);
function hello() {
  if (!store.pageBuild) return;
  fetch("/api/hello", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: PAGE_ID, build: store.pageBuild }) }).catch(() => {});
}
setInterval(hello, 20000);

export function connect(cameraImg, visionImg) {
  img = cameraImg || null;
  vimg = visionImg || null;
  if (img) img.addEventListener("error", () => { clearTimeout(streamTimer); streamTimer = setTimeout(restartStream, 1000); });
  if (vimg) vimg.addEventListener("error", () => { clearTimeout(visionTimer); visionTimer = setTimeout(restartVision, 1500); });
  let backoff = 400, last = 0, camWas = false;
  const open = () => {
    const ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onopen = () => { store.online = true; backoff = 400; last = performance.now(); restartStream(); restartVision(); };
    ws.onmessage = (ev) => {
      last = performance.now();
      let m;
      try { m = JSON.parse(ev.data); } catch { return; }
      if (m.type === "config") {
        if (store.cfg.build && m.build && m.build !== store.cfg.build) store.stale = true;
        if (!store.pageBuild && m.build) { store.pageBuild = m.build; hello(); }
        store.cfg = { ...store.cfg, ...m };
      }
      else if (m.type === "scan") store.scan = m;
      else if (m.type === "state") {
        store.state = m;
        if (m.mirror != null && document.body.classList.contains("mirror") !== !!m.mirror) document.body.classList.toggle("mirror", !!m.mirror);
        const b = m.brake || {};
        if (store.brakeSeen === null) store.brakeSeen = b.t ?? 0;
        else if (b.t && b.t !== store.brakeSeen) { store.brakeSeen = b.t; store.brakeAt = performance.now(); }
        const camLive = !!(m.cam && m.cam.live);
        if (camLive && !camWas) restartStream();
        camWas = camLive;
        for (const fn of listeners) fn(m);
      }
    };
    ws.onclose = () => {
      store.online = false; store.state = null;
      for (const fn of listeners) fn(null);
      setTimeout(open, backoff);
      backoff = Math.min(3000, backoff * 1.7);
    };
    ws.onerror = () => { try { ws.close(); } catch { /* */ } };
    const guard = setInterval(() => {
      if (ws.readyState !== WebSocket.OPEN) { clearInterval(guard); return; }
      if (performance.now() - last > 3000) { clearInterval(guard); ws.close(); }
    }, 1000);
  };
  open();
}

/** Ile milisekund temu robot zahamował (Infinity, jeśli nie było hamowania od wejścia na stronę). */
export const sinceBrake = () => (store.brakeAt ? performance.now() - store.brakeAt : Infinity);

// ------------------------------------------------------------------ jeden słownik komunikatów
/**
 * Co się teraz dzieje – jednym hasłem i jednym zdaniem, z perspektywy odwiedzającego.
 * step: 1 = pokaż dłoń, 2 = jedziesz, 3 = zatrzymanie (dla wariantu z krokami).
 */
export function phase(s) {
  if (!store.online || !s) return { key: "offline", step: 1, tone: "muted", title: "Łączę się z robotem", line: "To potrwa chwilę." };
  const d = s.drive || {}, sh = s.shield || {}, h = s.hand, b = s.brake || {};
  if (sinceBrake() < 3200 && b.last_s != null) {
    return { key: "braked", step: 3, tone: "stop", title: `Stop w ${fmt(b.last_s)} s`, line: "Radar zauważył rękę i zatrzymał koła szybciej, niż zdążysz mrugnąć." };
  }
  if (d.state === "ESTOP") return { key: "estop", step: 3, tone: "stop", title: "Hamuję", line: "Coś jest tuż przy robocie. Radar ma pierwszeństwo przed kamerą." };
  if (sh.live && !sh.ready) return { key: "learn", step: 1, tone: "route", title: "Poznaję stoisko", line: `Radar uczy się otoczenia: ${Math.round((sh.learn || 0) * 100)}%.` };
  if (d.lock === "battery") return { key: "battery", step: 1, tone: "muted", title: "Robot odpoczywa", line: "Akumulatory są prawie puste. Obsługa podłączy ładowarkę." };
  if (d.lock === "calibration") return { key: "calib", step: 1, tone: "route", title: "Kalibracja radaru", line: "Obsługa stoiska ustawia robota. Koła są zablokowane." };
  if (d.lock === "no_camera") return { key: "wait", step: 1, tone: "muted", title: "Czekam na kamerę", line: "Za chwilę będzie można sterować dłonią." };
  if (d.lock) return { key: "wait", step: 1, tone: "muted", title: "Czekam na radar", line: "Bez radaru robot nie rusza. To zabezpieczenie." };
  if (d.state === "DRIVE") return { key: "drive", step: 2, tone: "go", title: `Jadę ${Math.max(10, pct10(wheelSpeed(s)))}%`, line: "Przybliż dłoń do kamery, żeby przyspieszyć. Pięść to jazda do tyłu." };
  if (d.state === "REVERSE") return { key: "reverse", step: 2, tone: "route", title: "Cofam", line: "Otwórz dłoń, żeby jechać do przodu." };
  if (h && s.cam && s.cam.live) {
    if (d.sub === "too_close") return { key: "hand", step: 1, tone: "muted", title: "Trochę dalej", line: "Dłoń jest za blisko kamery. Odsuń ją na pół metra." };
    if (d.sub === "too_far") return { key: "hand", step: 1, tone: "muted", title: "Podejdź bliżej", line: "Wyciągnij dłoń w stronę kamery." };
    return { key: "hand", step: 1, tone: "muted", title: "Rozłóż palce", line: "Otwarta dłoń to gaz, pięść to jazda do tyłu." };
  }
  if (s.people && s.people.length) return { key: "seen", step: 1, tone: "route", title: "Widzę Cię", line: "Pokaż otwartą dłoń do kamery, żeby ruszyć robotem." };
  return { key: "idle", step: 1, tone: "muted", title: "Pokaż otwartą dłoń", line: "Robot ruszy. Włóż rękę przy robocie, a radar go zatrzyma." };
}

/** Odmiana: 1 zatrzymanie, 2–4 zatrzymania, 5+ zatrzymań (12–14 też "zatrzymań"). */
export function zatrzymania(n) {
  if (n === 1) return "zatrzymanie dziś";
  const d = n % 10, dd = n % 100;
  return d >= 2 && d <= 4 && !(dd >= 12 && dd <= 14) ? "zatrzymania dziś" : "zatrzymań dziś";
}

export function gestureText(s) {
  const h = s && s.hand;
  if (!h || !(s.cam && s.cam.live)) return "";
  if (h.gesture === "FIVE" || h.gesture === "FOUR") return "Otwarta dłoń";
  if (h.gesture === "FIST") return "Pięść";
  return "Dłoń";
}

// ------------------------------------------------------------------ szkielet dłoni na obrazie
const BONES = [[0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [17, 18], [18, 19], [19, 20], [0, 17]];
const TIPS = new Set([4, 8, 12, 16, 20]);

/** Rysuje na płótnie leżącym dokładnie na <img> z object-fit: cover (lustro robi CSS rodzica). */
export function drawHand(canvas, image, t) {
  const { ctx, W, H } = fitCanvas(canvas);
  const s = store.state;
  // pełnoekranowe płótno czyścimy tylko, gdy coś na nim było (Raspberry: mniej pracy, mniej ciepła)
  const busy = !!(s && s.cam && s.cam.live && (s.hand || (t.people && s.people && s.people.length)));
  if (canvas._drawn || busy) ctx.clearRect(0, 0, W, H);
  canvas._drawn = busy;
  if (!busy) return;
  const ia = image.naturalWidth && image.naturalHeight ? image.naturalWidth / image.naturalHeight : 16 / 9;
  const dw = Math.max(W, H * ia), dh = dw / ia, ox = (W - dw) / 2, oy = (H - dh) / 2;
  const map = (x, y) => [ox + x * dw, oy + y * dh];

  if (t.people) {
    ctx.strokeStyle = t.people;
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    const near = (s.people || []).slice().sort((a, b) => (a.dist_m ?? 99) - (b.dist_m ?? 99)).slice(0, 5);
    for (const p of near) {
      const [x0, y0] = map(p.bbox[0], p.bbox[1]), [x1, y1] = map(p.bbox[2], p.bbox[3]);
      if (x1 - x0 < 10 || y1 - y0 < 10) continue;
      const L = Math.min(24, (x1 - x0) * 0.22, (y1 - y0) * 0.22);
      ctx.beginPath();
      for (const [cx, cy, dx, dy] of [[x0, y0, 1, 1], [x1, y0, -1, 1], [x0, y1, 1, -1], [x1, y1, -1, -1]]) {
        ctx.moveTo(cx + dx * L, cy); ctx.lineTo(cx, cy); ctx.lineTo(cx, cy + dy * L);
      }
      ctx.stroke();
      // odległość nad osobą (widok głębi); tekst odwrócony z powrotem, bo cały obraz jest lustrem
      if (t.labels && p.dist_m != null) {
        const text = `${fmt(p.dist_m, 1)} m`;
        ctx.save();
        ctx.font = "600 20px Barlow, sans-serif";
        const tw = ctx.measureText(text).width + 22, th = 32;
        const cx = clamp((x0 + x1) / 2, tw / 2 + 4, W - tw / 2 - 4);
        const ty = Math.max(t.labelTop ?? 96, y0 - th - 8);      // nie wchodzi na nagłówek
        ctx.translate(cx, ty);
        if (mirrored()) ctx.scale(-1, 1);
        ctx.fillStyle = "rgba(15,17,18,0.78)";
        roundRect(ctx, -tw / 2, 0, tw, th, th / 2); ctx.fill();
        ctx.fillStyle = t.labels; ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.fillText(text, 0, th / 2 + 1);
        ctx.restore();
      }
    }
  }
  const h = s.hand;
  if (!h) return;
  if (h.landmarks && h.landmarks.length === 21) {
    const p = h.landmarks.map(([x, y]) => map(x, y));
    ctx.lineJoin = ctx.lineCap = "round";
    ctx.beginPath();
    for (const [a, b] of BONES) { ctx.moveTo(...p[a]); ctx.lineTo(...p[b]); }
    ctx.strokeStyle = t.handShadow; ctx.lineWidth = 7; ctx.stroke();
    ctx.strokeStyle = t.hand; ctx.lineWidth = 3.5; ctx.stroke();
    p.forEach(([x, y], i) => {
      ctx.beginPath(); ctx.arc(x, y, TIPS.has(i) ? 6 : 3.5, 0, Math.PI * 2);
      ctx.fillStyle = TIPS.has(i) ? t.hand : t.joint; ctx.fill();
    });
  } else if (h.bbox) {
    const [a0, a1] = [map(h.bbox[0], h.bbox[1]), map(h.bbox[2], h.bbox[3])];
    roundRect(ctx, a0[0] - 6, a0[1] - 6, a1[0] - a0[0] + 12, a1[1] - a0[1] + 12, 12);
    ctx.strokeStyle = t.hand; ctx.lineWidth = 3.5; ctx.stroke();
  }
  // "przytrzymaj": pierścień wokół dłoni, gdy kciuk w górę uruchamia grę (t.ring 0..1)
  if (t.ring > 0 && h.landmarks && h.landmarks.length === 21) {
    const p = h.landmarks.map(([x, y]) => map(x, y));
    const c = [0, 5, 9, 13, 17].reduce((a, i) => [a[0] + p[i][0] / 5, a[1] + p[i][1] / 5], [0, 0]);
    const r = Math.max(60, 1.9 * Math.hypot(p[9][0] - p[0][0], p[9][1] - p[0][1]));
    ctx.lineCap = "round";
    ctx.strokeStyle = "rgba(243,241,234,0.22)"; ctx.lineWidth = 10;
    ctx.beginPath(); ctx.arc(c[0], c[1], r, 0, Math.PI * 2); ctx.stroke();
    ctx.strokeStyle = t.hand; ctx.lineWidth = 10;
    ctx.beginPath(); ctx.arc(c[0], c[1], r, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * Math.min(1, t.ring)); ctx.stroke();
  }
}

// ------------------------------------------------------------------ radar z lotu ptaka
/**
 * Układ robota: X do przodu (do odwiedzających), Y w lewo; przód robota na dole ekranu.
 * Skala "rybie oko": do 1 m liniowo, dalej ściśnięta, żeby zmieścić ludzi do ~3 m.
 * t: kolory wariantu; o: { cy: środek w pionie 0..1, rings: bool, ringLabel: bool }
 */
export function drawRadar(canvas, t, o = {}) {
  const { ctx, W, H } = fitCanvas(canvas);
  ctx.clearRect(0, 0, W, H);
  const s = store.state;
  if (!s) return;
  const sh = s.shield || {};
  const comp = (r) => (r <= 1 ? r : 1 + (r - 1) * 0.45);
  const cx = W / 2, cy = H * (o.cy ?? 0.42);
  const sc = Math.min((H - cy - 8) / comp(3.0), (W / 2 - 8) / comp(3.0), (cy - 8) / comp(1.4));
  const P = (X, Y) => { const r = Math.hypot(X, Y); const f = r > 1e-9 ? comp(r) / r : 1; return [cx + Y * f * sc, cy + X * f * sc]; };

  if (o.rings !== false) {
    ctx.lineWidth = 1;
    ctx.strokeStyle = t.ring;
    for (const r of [1, 2, 3]) { ctx.beginPath(); ctx.arc(cx, cy, comp(r) * sc, 0, Math.PI * 2); ctx.stroke(); }
    if (o.ringLabel) {
      ctx.fillStyle = t.label; ctx.font = "500 14px Barlow, sans-serif"; ctx.textBaseline = "bottom"; ctx.textAlign = "left";
      ctx.fillText("1 m", cx + 6, cy - comp(1) * sc - 4);
      ctx.fillText("2 m", cx + 6, cy - comp(2) * sc - 4);
    }
  }

  // pole widzenia kamery
  const [ox, oy] = store.cfg.cam_offset || [0.1, 0];
  const half = ((store.cfg.hfov_deg || 69) * Math.PI) / 360;
  ctx.beginPath();
  ctx.moveTo(...P(ox, oy));
  for (let k = 0; k <= 24; k++) { const a = -half + (2 * half * k) / 24; ctx.lineTo(...P(ox + 3 * Math.cos(a), oy + 3 * Math.sin(a))); }
  ctx.closePath();
  ctx.fillStyle = t.fov; ctx.fill();

  // strefa hamowania
  const R = (sh.radius_m || store.cfg.shield_radius_m || 0.3) * sc;
  if (sh.live) {
    if (!sh.ready) {
      ctx.strokeStyle = t.ring; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.stroke();
      ctx.strokeStyle = t.route;
      ctx.beginPath(); ctx.arc(cx, cy, R, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * (sh.learn || 0)); ctx.stroke();
    } else if (sh.active) {
      ctx.fillStyle = t.stopFill;
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = t.stop; ctx.lineWidth = 3; ctx.stroke();
    } else {
      ctx.strokeStyle = t.zone; ctx.lineWidth = 2; ctx.setLineDash([6, 6]);
      ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
    }
  }

  // punkty radaru
  const sc_ = store.scan;
  if (sc_ && sh.live) {
    const fg = new Set(sc_.fg || []), hit = new Set(sh.intrusion || []);
    for (let i = 0; i < 360; i++) {
      const r = sc_.r[i] / 1000;
      if (r <= 0 || r > 4) continue;
      const a = (i * Math.PI) / 180;
      const [x, y] = P(r * Math.cos(a), r * Math.sin(a));
      if (x < 0 || y < 0 || x > W || y > H) continue;
      const isHit = hit.has(i), isFg = fg.has(i);
      ctx.fillStyle = isHit ? t.stop : isFg ? t.dot : t.dotBg;
      ctx.beginPath(); ctx.arc(x, y, isHit ? 3.6 : isFg ? 2.8 : 1.7, 0, Math.PI * 2); ctx.fill();
    }
    // ludzie (tylko potwierdzeni)
    for (const tr of sc_.tracks || []) {
      if (tr.kind !== "person") continue;
      const [px, py] = P(tr.x, tr.y);
      const rad = clamp(tr.w * 0.5 * sc * (tr.d > 1 ? 0.45 : 1), 8, 16);
      ctx.beginPath(); ctx.arc(px, py, rad, 0, Math.PI * 2);
      ctx.fillStyle = tr.fused ? t.personFill : "transparent"; ctx.fill();
      ctx.strokeStyle = tr.fused ? t.route : t.dotBg; ctx.lineWidth = tr.fused ? 2.5 : 1.5; ctx.stroke();
    }
  }

  // dłoń widziana przez kamerę
  const h = s.cam && s.cam.live ? s.hand : null;
  if (h && h.map) {
    const [hx, hy] = P(h.map[0], h.map[1]);
    ctx.fillStyle = t.route;
    ctx.beginPath(); ctx.arc(hx, hy, 7, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = t.bg; ctx.lineWidth = 2.5; ctx.stroke();
  }

  // robot
  const bl = 0.125, bw = 0.10;
  const [rx0, ry0] = P(-bl, -bw), [rx1, ry1] = P(bl, bw);
  roundRect(ctx, rx0, ry0, rx1 - rx0, ry1 - ry0, 4);
  ctx.fillStyle = t.robot; ctx.fill();
  ctx.strokeStyle = t.robotEdge; ctx.lineWidth = 1.5; ctx.stroke();
  ctx.strokeStyle = t.route; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(...P(bl, -bw * 0.7)); ctx.lineTo(...P(bl, bw * 0.7)); ctx.stroke();
}

// ------------------------------------------------------------------ podwozie z kołami
const tread = { L: 0, R: 0 };
/** Widok z góry, przód na dole (od strony widza): lewa strona ekranu = prawe koła robota. */
export function drawChassis(canvas, dt, t) {
  const { ctx, W, H } = fitCanvas(canvas);
  ctx.clearRect(0, 0, W, H);
  const s = store.state;
  const wh = s && s.wheels;
  const max = store.cfg.max_speed || 0.5;
  const estop = s && s.drive && s.drive.state === "ESTOP";
  const sp = { L: wh && !estop ? clamp(wh.L / max, -1, 1) : 0, R: wh && !estop ? clamp(wh.R / max, -1, 1) : 0 };
  const bodyW = Math.min(W * 0.34, H * 0.42), bodyL = bodyW * 1.3;
  const cx = W / 2, cy = H / 2;
  const ww = bodyW * 0.3, wl = bodyL * 0.38, gap = bodyW * 0.08;
  roundRect(ctx, cx - bodyW / 2, cy - bodyL / 2, bodyW, bodyL, 12);
  ctx.fillStyle = t.body; ctx.fill();
  ctx.strokeStyle = estop ? t.stop : t.bodyEdge; ctx.lineWidth = estop ? 3 : 1.5; ctx.stroke();
  ctx.fillStyle = t.route;
  roundRect(ctx, cx - bodyW * 0.26, cy + bodyL / 2 - 13, bodyW * 0.52, 6, 3); ctx.fill();
  for (const [side, x] of [["R", cx - bodyW / 2 - gap - ww], ["L", cx + bodyW / 2 + gap]]) {
    const v = sp[side];
    tread[side] += v * dt * 170;
    const tone = v > 0.02 ? t.go : v < -0.02 ? t.route : null;
    for (const y of [cy - bodyL / 2 + 2, cy + bodyL / 2 - wl - 2]) {
      ctx.save();
      roundRect(ctx, x, y, ww, wl, 7);
      ctx.fillStyle = t.wheel; ctx.fill(); ctx.clip();
      const step = 10, off = ((tread[side] % step) + step) % step;
      ctx.strokeStyle = tone || t.treadIdle;
      ctx.lineWidth = 3;
      ctx.beginPath();
      for (let ty = y - step + off; ty < y + wl + step; ty += step) { ctx.moveTo(x + 4, ty); ctx.lineTo(x + ww - 4, ty); }
      ctx.stroke();
      ctx.restore();
    }
  }
  return sp;
}

// ------------------------------------------------------------------ obsługa i logotypy
/** Obsługa z klawiatury (opcjonalna): M lustro kamery, R kalibracja radaru, F pełny ekran. */
export function controls() {
  addEventListener("keydown", (e) => {
    if (document.body.dataset.modal) return;      // okno kalibracji: bez skrótów
    const k = e.key.toLowerCase();
    // R (nauka tła radaru) i M (lustro) tylko z Shiftem: przypadkowe naciśnięcie nie psuje stoiska
    if (k === "r" && e.shiftKey) fetch("/api/recalibrate", { method: "POST" }).catch(() => {});
    else if (k === "m" && e.shiftKey) fetch("/api/mirror", { method: "POST" }).catch(() => {});      // dla wszystkich ekranów
    else if (k === "f") {
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      else document.documentElement.requestFullscreen().catch(() => {});
    }
  });
  const tick = () => document.body.classList.toggle("mirror", mirrored());
  tick();
  setInterval(tick, 1000);
  // kursor myszy: pojawia się przy ruchu / kliknięciu, chowa po 10 s bezruchu
  // (przy otwartym oknie kalibracji zostaje widoczny)
  let idleT = 0;
  const wake = () => {
    document.body.classList.remove("cursor-idle");
    clearTimeout(idleT);
    idleT = setTimeout(function hide() {
      if (document.body.dataset.modal) { idleT = setTimeout(hide, 2000); return; }
      document.body.classList.add("cursor-idle");
    }, 10000);
  };
  for (const ev of ["mousemove", "pointerdown", "wheel", "keydown"]) addEventListener(ev, wake, { passive: true });
  document.body.classList.add("cursor-idle");
  // logotypy z folderu assets/ podmieniają się bez przeładowania
  setInterval(() => {
    for (const [id, name] of [["logoKolo", "kolo"], ["logoRzeszow", "rzeszow"]]) {
      const node = $(id);
      if (!node) continue;
      const probe = new Image();
      probe.onload = () => { node.src = probe.src; };
      probe.src = `/branding/${name}?v=${Date.now()}`;
    }
  }, 30000);
}

/** Kiosk na samym Raspberry: 20 klatek/s zamiast 30, bo rysowanie mocno grzeje procesor. Rozpoznanie po adresie
 *  (kiosk otwiera serwer robota przez localhost:8000); po napisie przeglądarki się nie da, bo Chromium na Raspberry
 *  przedstawia się jako "Linux x86_64". */
export const ON_PI = ["localhost", "127.0.0.1"].includes(location.hostname) && location.port === "8000";

/** Pętla rysowania z limitem klatek (30/s wystarcza do płynnego radaru i kół, a odciąża Raspberry). */
export function loop(fn, maxFps = ON_PI ? 20 : 30) {
  let last = performance.now(), lastDraw = 0;
  const minGap = 1000 / maxFps - 2;
  const frame = (now) => {
    requestAnimationFrame(frame);
    if (now - lastDraw < minGap) return;
    lastDraw = now;
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    fn(dt, now);
  };
  requestAnimationFrame(frame);
}
