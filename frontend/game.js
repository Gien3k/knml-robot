/* =============================================================================
   Papier, kamień, nożyce – ARENA. Stan gry liczy serwer (backend/app/logic/rps.py),
   tu jest cała reżyseria ekranu:
     wejście   ekran dzieli się po skosie: z lewej odwiedzający (kamera na żywo),
               z prawej "dłoń robota" – świecący hologram 21 punktów; "VS", trzy ruchy
     odliczanie  PAPIER / KAMIEŃ / NOŻYCE / TERAZ!, dłoń robota naprawdę gra: zaciska pięść
               palec po palcu i pompuje w tym samym rytmie co prawdziwe koła
     odsłona   błysk migawki zamraża klatkę z dłonią odwiedzającego, robot "rzuca" ręką
               i otwiera palce po kolei w swój ruch, wynik rundy; robot tańczy albo opada
     finał     wielki wynik, a potem POWTÓRKA ostatniej rundy w zwolnionym tempie z nagranych
               klatek: widać, w której klatce robot "przyłapał" gest (oś czasu w sekundach)
   Minimalistycznie: bez dźwięku i efektów na tle – ruch ma przede wszystkim dłoń robota.
   Wydajność (Raspberry Pi 5): tylko transformacje/przezroczystość w CSS, bez filtrów rozmycia.
   ============================================================================= */
import { $, clamp, fitCanvas, fmt, mirrored } from "./shared/kn.js";

export const MOVE_PL = { rock: "Kamień", paper: "Papier", scissors: "Nożyce" };
const MOVE_UP = { rock: "KAMIEŃ", paper: "PAPIER", scissors: "NOŻYCE" };
const WHY = { rock: "Kamień tępi nożyce", scissors: "Nożyce tną papier", paper: "Papier owija kamień" };
const WORDS = ["PAPIER", "KAMIEŃ", "NOŻYCE", "TERAZ!"];

// wzorcowe dłonie (widok od strony kamery, wspólna skala), pole 0..1; pięść i kciuk zaprojektowane jak ikony:
// kostki, palce zwinięte do środka dłoni, kciuk w poprzek / w górę
const POSES = {
  rock: [[0.376, 0.96], [0.583, 0.785], [0.695, 0.66], [0.595, 0.585], [0.445, 0.572], [0.546, 0.468], [0.537, 0.386], [0.527, 0.455], [0.521, 0.511], [0.411, 0.434], [0.412, 0.353], [0.413, 0.421], [0.413, 0.477], [0.281, 0.474], [0.292, 0.393], [0.302, 0.461], [0.309, 0.517], [0.166, 0.542], [0.185, 0.461], [0.204, 0.53], [0.217, 0.586]],
  paper: [[0.385, 1.0], [0.505, 0.887], [0.671, 0.801], [0.801, 0.699], [0.89, 0.591], [0.521, 0.546], [0.549, 0.332], [0.566, 0.203], [0.581, 0.09], [0.413, 0.519], [0.413, 0.274], [0.413, 0.121], [0.413, 0.0], [0.309, 0.551], [0.281, 0.332], [0.263, 0.186], [0.249, 0.073], [0.217, 0.606], [0.169, 0.443], [0.139, 0.341], [0.11, 0.244]],
  scissors: [[0.462, 0.998], [0.56, 0.86], [0.64, 0.76], [0.55, 0.7], [0.42, 0.7], [0.598, 0.544], [0.655, 0.336], [0.69, 0.211], [0.72, 0.1], [0.489, 0.517], [0.46, 0.274], [0.442, 0.122], [0.427, 0.002], [0.386, 0.609], [0.389, 0.544], [0.391, 0.599], [0.393, 0.644], [0.294, 0.664], [0.303, 0.599], [0.313, 0.654], [0.319, 0.699]],
  thumb: [[0.376, 0.96], [0.583, 0.76], [0.682, 0.547], [0.726, 0.335], [0.757, 0.135], [0.546, 0.468], [0.537, 0.386], [0.527, 0.455], [0.521, 0.511], [0.411, 0.434], [0.412, 0.353], [0.413, 0.421], [0.413, 0.477], [0.281, 0.474], [0.292, 0.393], [0.302, 0.461], [0.309, 0.517], [0.166, 0.542], [0.185, 0.461], [0.204, 0.53], [0.217, 0.586]],
};
// czubki palców schowane w pięści – rysowane mniejsze (czytelniejsza pięść)
const TUCKED = { rock: new Set([8, 12, 16, 20]), thumb: new Set([8, 12, 16, 20]), scissors: new Set([16, 20]), paper: new Set() };
const BONES = [[0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8], [5, 9], [9, 10], [10, 11], [11, 12],
  [9, 13], [13, 14], [14, 15], [15, 16], [13, 17], [17, 18], [18, 19], [19, 20], [0, 17]];
const TIPS = new Set([4, 8, 12, 16, 20]);
const PALM = [0, 1, 5, 9, 13, 17];
const C = { paint: "#f3f1ea", route: "#6cb4ff", go: "#3fc27a", caution: "#f2b33d", concrete: "#9a9d9f", gravel: "#64686b" };
const BEAT_MS = 800;                 // domyślny rytm; w grze bierzemy beat_s z serwera

const easeInOut = (t) => { t = clamp(t, 0, 1); return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; };
const easeOutBack = (t) => { const c1 = 1.70158, c3 = c1 + 1, x = clamp(t, 0, 1) - 1; return 1 + c3 * x * x * x + c1 * x * x; };
const rot = ([x, y], [cx, cy], a) => {
  const c = Math.cos(a), s = Math.sin(a);
  return [cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c];
};

function plural(n, one, few, many) {
  if (n === 1) return one;
  const d = n % 10, dd = n % 100;
  return d >= 2 && d <= 4 && !(dd >= 12 && dd <= 14) ? few : many;
}

// ------------------------------------------------------------------ rysowanie dłoni
function skeleton(ctx, p, color, joint = C.paint, scale = 1, tucked = null) {
  ctx.lineJoin = ctx.lineCap = "round";
  ctx.beginPath();
  for (const [a, b] of BONES) { ctx.moveTo(...p[a]); ctx.lineTo(...p[b]); }
  ctx.strokeStyle = "rgba(8,9,10,0.55)"; ctx.lineWidth = 8 * scale; ctx.stroke();
  ctx.strokeStyle = color; ctx.lineWidth = 4 * scale; ctx.stroke();
  p.forEach(([x, y], i) => {
    const tip = TIPS.has(i) && !(tucked && tucked.has(i));
    ctx.beginPath(); ctx.arc(x, y, (tip ? 6.5 : 4) * scale, 0, Math.PI * 2);
    ctx.fillStyle = tip ? color : joint; ctx.fill();
  });
}

/** Piktogram ruchu dopasowany do płótna (wejście areny). */
export function drawPictogram(canvas, pose, color = C.paint) {
  const { ctx, W, H } = fitCanvas(canvas);
  ctx.clearRect(0, 0, W, H);
  const pts = POSES[pose];
  const xs = pts.map((v) => v[0]), ys = pts.map((v) => v[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = 12, k = Math.min((W - 2 * pad) / (x1 - x0), (H - 2 * pad) / (y1 - y0));
  const mir = mirrored();
  const p = pts.map(([x, y]) => [W / 2 + (mir ? (x0 + x1) / 2 - x : x - (x0 + x1) / 2) * k, H / 2 + (y - (y0 + y1) / 2) * k]);
  skeleton(ctx, p, color, C.paint, Math.max(0.7, W / 220), TUCKED[pose]);
}

/** Kadr z kamery na płótnie o rozmiarze obrazu (object-fit: cover), zwraca mapowanie punktów 0..1. */
function coverDraw(ctx, src, W, H) {
  const ia = 16 / 9;
  const dw = Math.max(W, H * ia), dh = dw / ia, ox = (W - dw) / 2, oy = (H - dh) / 2;
  if (src) ctx.drawImage(src, ox, oy, dw, dh);
  return (x, y) => [ox + x * dw, oy + y * dh];
}

// ------------------------------------------------------------------ dłoń robota, która naprawdę gra
/**
 * Hologram 21 punktów z "fizyką" ręki:
 *  - zmiana ułożenia palec po palcu (otwieranie od wskazującego, zamykanie od małego),
 *    z lekkim przestrzeleniem – jak prawdziwe palce, a nie przenikanie kształtów,
 *  - pompowanie w rytm odliczania: ręka idzie w górę, nadgarstek odgina się do tyłu
 *    (skrót perspektywy), na uderzeniu dłoń "spada" i lekko się spłaszcza,
 *  - "rzut" przy odsłonięciu, taniec nadgarstkiem po wygranej, opadnięcie po przegranej,
 *  - w spoczynku oddycha, a palce lekko się poruszają.
 */
// kolory dłoni: robot = niebieski hologram, człowiek = biała (podgląd gry na ekranie startowym)
const TONES = {
  robot: { rgb: "108,180,255", core: "#cfe6ff", tip: "#ffffff", joint: "#d8ecff", scan: true },
  human: { rgb: "243,241,234", core: "#f3f1ea", tip: "#ffffff", joint: "#f3f1ea", scan: false },
};

export class AiHand {
  /** o.tone: robot | human; o.mirror: lustro (dłoń gracza jak w lustrze); o.beatMs: rytm pompowania */
  constructor(pose = "paper", o = {}) {
    this.tone = TONES[o.tone] || TONES.robot;
    this.mirror = !!o.mirror;
    this.beatMs = o.beatMs || BEAT_MS;
    this.pose = pose;
    this.anim = null;
    this.flashAt = -1e9;
    this.pumpAt = -1e9;
    this.pumpN = 0;
    this.throwAt = -1e9;
    this.react = null;
  }

  set(pose, now, o = {}) {
    if (!POSES[pose] || (pose === this.pose && !o.force)) { if (o.flash) this.flashAt = now; return; }
    const from = this.shape(now);
    const opening = o.opening ?? pose !== "rock";
    // opóźnienia palców [kciuk, wskazujący, środkowy, serdeczny, mały] [ms]
    const delays = opening ? [80, 0, 50, 100, 150] : [140, 100, 70, 35, 0];
    const k = o.fast ? 0.45 : 1;
    this.anim = { from, to: POSES[pose], t0: now, delays: delays.map((d) => d * k),
      dur: (opening ? 240 : 280) * k, back: opening };
    this.pose = pose;
    if (o.flash) this.flashAt = now;
  }

  shape(now) {
    const a = this.anim;
    if (!a) return POSES[this.pose];
    let done = true;
    const out = a.from.map((f, i) => {
      const d = i === 0 ? 0 : a.delays[Math.floor((i - 1) / 4)];
      const p = (now - a.t0 - d) / a.dur;
      if (p < 1) done = false;
      const e = a.back ? easeOutBack(p) : easeInOut(p);
      return [f[0] + (a.to[i][0] - f[0]) * e, f[1] + (a.to[i][1] - f[1]) * e];
    });
    if (done) this.anim = null;
    return out;
  }

  pump(now) { this.pumpAt = now; this.pumpN++; }
  throwHand(now) { this.throwAt = now; }
  reaction(kind, now) { this.react = kind ? { kind, t0: now } : null; }

  draw(canvas, now, o = {}) {
    const { ctx, W, H } = fitCanvas(canvas);
    ctx.clearRect(0, 0, W, H);
    const size = Math.min(W * 0.6, H * 0.6) * (o.scale || 1);
    const cx = W * (o.cx ?? 0.57), cy = H * (o.cy ?? 0.45);
    const s = size / 520;
    let pts = this.shape(now).map(([x, y]) => [cx + ((this.mirror ? 1 - x : x) - 0.5) * size, cy + (y - 0.5) * size]);
    const T = this.tone;

    // --- ruch całej ręki: uniesienie, odgięcie nadgarstka (skrót), przechylenie, skala
    let lift = 0, flex = 1, roll = 0, pop = 1;
    const beat = (now - this.pumpAt) / this.beatMs;
    if (beat >= 0 && beat < 1) {
      // jak człowiek: spokojnie w górę, szybko i mocno w dół – uderzenie dokładnie na następne słowo
      const h = Math.sin(Math.PI * Math.pow(beat, 1.6));
      lift += 130 * s * h;
      flex -= 0.24 * h;
      roll += (this.pumpN % 2 ? 1 : -1) * 0.05 * h;
      if (beat < 0.1) pop += 0.05 * (1 - beat / 0.1);           // uderzenie
    }
    const th = (now - this.throwAt) / 1000;
    if (th >= 0 && th < 0.4) {                                 // rzut przy odsłonięciu
      const h = Math.sin(Math.PI * th / 0.4);
      lift -= 26 * s * h; pop += 0.07 * h;
    }
    if (this.react) {
      const t = (now - this.react.t0) / 1000;
      if (this.react.kind === "win" && t < 1.3) roll += 0.16 * Math.sin(t * 19) * (1 - t / 1.3);
      if (this.react.kind === "lose") {
        const e = easeInOut(t / 0.7);
        lift -= 34 * s * e; flex -= 0.14 * e; roll -= 0.1 * e;
      }
    }
    if (o.idle) {
      lift += 7 * s * Math.sin(now / 760);
      roll += 0.02 * Math.sin(now / 1100 + 1);
      // palce lekko "żyją"
      if (!this.anim) {
        pts = pts.map((p, i) => {
          if (i === 0 || (i - 1) % 4 === 0) return p;
          const g = Math.floor((i - 1) / 4), base = pts[g * 4 + 1];
          return rot(p, base, 0.035 * Math.sin(now / 650 + g * 0.9));
        });
      }
    }
    const wrist = [pts[0][0], pts[0][1] - lift];
    pts = pts.map(([x, y], i) => {
      let q = [x, y - lift];
      if (i) q = [wrist[0] + (q[0] - wrist[0]) * pop, wrist[1] + (q[1] - wrist[1]) * flex * pop];
      return rot(q, wrist, roll);
    });
    const P = pts;
    const fl = clamp(1 - (now - this.flashAt) / 650, 0, 1);       // poświata przy odsłonięciu

    // delikatna linia skanowania hologramu
    if (T.scan) {
      const sy = ((now / 2800) % 1.3) * H;
      ctx.fillStyle = "rgba(108,180,255,0.16)"; ctx.fillRect(0, sy, W, 1.5);
    }

    // przedramię: od nadgarstka w dół, wygaszane (podąża za ręką)
    const ag = ctx.createLinearGradient(0, wrist[1], 0, H);
    ag.addColorStop(0, `rgba(${T.rgb},${T.scan ? 0.32 : 0.2})`); ag.addColorStop(1, `rgba(${T.rgb},0)`);
    ctx.strokeStyle = ag; ctx.lineCap = "round"; ctx.lineWidth = 64 * s;
    ctx.beginPath(); ctx.moveTo(wrist[0], wrist[1]); ctx.lineTo(wrist[0] - 24 * s + roll * 300 * s, H + 60); ctx.stroke();

    // śródręcze
    ctx.beginPath();
    PALM.forEach((i, n) => (n ? ctx.lineTo(...P[i]) : ctx.moveTo(...P[i])));
    ctx.closePath();
    ctx.fillStyle = `rgba(${T.rgb},${(T.scan ? 0.10 : 0.07) + 0.22 * fl})`; ctx.fill();

    // kości: trzy przejścia = poświata bez kosztownego shadowBlur
    ctx.lineJoin = ctx.lineCap = "round";
    ctx.beginPath();
    for (const [a, b] of BONES) { ctx.moveTo(...P[a]); ctx.lineTo(...P[b]); }
    ctx.strokeStyle = `rgba(${T.rgb},${0.08 + 0.14 * fl})`; ctx.lineWidth = (30 + 26 * fl) * s; ctx.stroke();
    ctx.strokeStyle = `rgba(${T.rgb},${(T.scan ? 0.28 : 0.2) + 0.3 * fl})`; ctx.lineWidth = 13 * s; ctx.stroke();
    ctx.strokeStyle = fl > 0.05 ? "#ffffff" : T.core; ctx.lineWidth = 4.5 * s; ctx.stroke();

    // stawy
    const tucked = TUCKED[this.pose];
    P.forEach(([x, y], i) => {
      const tip = TIPS.has(i) && !tucked.has(i);
      const r = (tip ? 11 : 7) * s;
      ctx.fillStyle = `rgba(${T.rgb},${T.scan ? 0.22 : 0.14})`;
      ctx.beginPath(); ctx.arc(x, y, r * 2.2, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = tip ? T.tip : T.joint;
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
    });
  }
}

// ------------------------------------------------------------------ nagrywanie ostatniej rundy
class Recorder {
  constructor() { this.frames = []; this.pool = []; this.on = false; }
  reset() { for (const f of this.frames) this.pool.push(f.c); this.frames = []; this.on = false; }
  capture(img, lm, now) {
    if (!img || !img.naturalWidth) return;
    const last = this.frames[this.frames.length - 1];
    if (last && now - last.t < 30) return;                      // ~30 kl./s wystarczy
    const c = this.pool.pop() || Object.assign(document.createElement("canvas"), { width: 480, height: 270 });
    c.getContext("2d").drawImage(img, 0, 0, 480, 270);
    this.frames.push({ t: now, c, lm: lm ? lm.map((p) => [p[0], p[1]]) : null });
    while (this.frames.length && now - this.frames[0].t > 2200) this.pool.push(this.frames.shift().c);
  }
  /** Odcinek wokół "Teraz!" do powtórki (klatki przestają być nadpisywane). */
  cut(tGo) {
    this.on = false;
    const keep = this.frames.filter((f) => f.t >= tGo - 400 && f.t <= tGo + 1100);
    for (const f of this.frames) if (!keep.includes(f)) this.pool.push(f.c);
    this.frames = [];
    return keep.length >= 3 ? keep : null;
  }
}

/** Powtórka: wyścig od chwili, gdy gest stał się widoczny. Pasek robota rośnie do jego decyzji (~0,1 s),
 *  pasek człowieka do typowego czasu reakcji (ok. 0,25 s). tl.t: czas od pokazania gestu (zwolniony). */
const HUMAN_S = 0.25;
function drawTimeline(canvas, tl) {
  const { ctx, W, H } = fitCanvas(canvas);
  ctx.clearRect(0, 0, W, H);
  if (!tl) return;
  const T = Math.max(0.32, tl.robot + 0.06, HUMAN_S + 0.07);
  const pw = 1040, px0 = (W - pw) / 2, ph = 176, py0 = H - ph - 64;
  ctx.fillStyle = "rgba(8,9,10,0.8)";
  ctx.beginPath(); ctx.roundRect(px0, py0, pw, ph, 24); ctx.fill();
  ctx.font = "500 18px Barlow, sans-serif"; ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
  ctx.fillStyle = C.concrete;
  ctx.fillText("Od pokazania gestu", px0 + 32, py0 + 36);
  const bx0 = px0 + 220, bx1 = px0 + pw - 170, X = (t) => bx0 + clamp(t / T, 0, 1) * (bx1 - bx0);
  const rows = [["Robot", tl.robot, C.route, `${fmt(tl.robot)} s`], ["Człowiek", HUMAN_S, C.caution, `ok. ${fmt(HUMAN_S)} s`]];
  rows.forEach(([name, val, col, txt], i) => {
    const y = py0 + 76 + i * 56;
    ctx.font = "600 26px 'Barlow Condensed', Barlow, sans-serif"; ctx.fillStyle = col; ctx.textAlign = "left";
    ctx.fillText(name, px0 + 32, y + 9);
    ctx.fillStyle = "rgba(243,241,234,0.08)";
    ctx.beginPath(); ctx.roundRect(bx0, y - 12, bx1 - bx0, 24, 12); ctx.fill();
    const t = clamp(tl.t, 0, val);
    if (t > 0) {
      ctx.fillStyle = col;
      ctx.beginPath(); ctx.roundRect(bx0, y - 12, Math.max(24, X(t) - bx0), 24, 12); ctx.fill();
    }
    if (tl.t >= val) {                                       // dobiegł: czas przy końcu paska
      ctx.font = "600 26px Barlow, sans-serif"; ctx.fillStyle = col; ctx.textAlign = "left";
      ctx.fillText(txt, X(val) + 16, y + 9);
    }
  });
  if (tl.t >= 0) {                                           // "teraz" powtórki: pionowa kreska przez oba paski
    const x = X(tl.t);
    ctx.fillStyle = "rgba(255,255,255,0.85)";
    ctx.fillRect(x - 1.5, py0 + 52, 3, 108);
  }
}

// ------------------------------------------------------------------ reżyser areny
// napisy sceny i fazy, w których wolno je pokazać. Wszystko spoza listy znika od razu, więc żaden
// napis nie zostaje z poprzedniej fazy (np. po zgubionej wiadomości, przeładowaniu czy przerwie w rysowaniu)
const SCENE_EL = ["arenaWord", "arenaHint", "arenaVs", "arenaMoves", "resTitle", "moveHuman", "moveRobot", "finalBox", "replayBadge", "replayCap"];
const ALLOWED = {
  intro: ["arenaVs", "arenaMoves"],
  countdown: ["arenaWord", "arenaHint"],
  read: ["arenaWord", "arenaHint", "moveHuman"],
  reveal: ["arenaWord", "resTitle", "moveHuman", "moveRobot"],
  miss: ["resTitle", "arenaMoves"],
  abort: ["resTitle"],
  final: ["finalBox", "replayBadge", "replayCap"],
};

export class Arena {
  constructor(stage, img) {
    this.st = stage;
    this.img = img;
    this.ai = new AiHand("paper");
    this.inviteHand = new AiHand("thumb");
    this.rec = new Recorder();
    this.prev = { active: false };
    this.later = [];
    this.clip = null;
    this.replay = null;
    this.tGo = 0;
    this.tBeat2 = 0;
    this.revealAt = -1e9;
    this.frozen = false;
    this.timelineOn = false;
    this.el = Object.fromEntries(["arenaWord", "arenaHint", "arenaVs", "arenaMoves", "arenaRound", "scHuman", "scRobot",
      "resTitle", "moveHuman", "moveRobot", "finalBox", "finalTitle", "finalH", "finalR",
      "replayBadge", "replayCap", "arenaFlash", "freeze", "camBox", "aiHand", "arenaFx", "inviteHand"].map((id) => [id, $(id)]));
  }

  // ---------------------------------------------------------------- pomocnicze
  schedule(ms, fn) { this.later.push({ at: performance.now() + ms, fn }); }
  clearLater() { this.later = []; }
  show(el, on) { el.classList.toggle("on", !!on); if (on) el.hidden = false; }
  text(el, v) { if (el.textContent !== v) el.textContent = v; }
  slam(el, big = 1.6, dur = 260) {
    el.getAnimations().forEach((a) => a.cancel());
    el.animate([{ transform: `scale(${big}) skewX(-6deg)`, opacity: 0 },
      { transform: "scale(0.96) skewX(-6deg)", opacity: 1, offset: 0.62 },
      { transform: "scale(1) skewX(-6deg)", opacity: 1 }], { duration: dur, easing: "cubic-bezier(.2,.8,.2,1)", fill: "forwards" });
  }
  hideNow(el) {
    el.getAnimations().forEach((a) => a.cancel());
    this.show(el, false);
  }
  fadeOut(el, dur = 220) {
    el.getAnimations().forEach((a) => a.cancel());
    el.animate([{ opacity: getComputedStyle(el).opacity }, { opacity: 0 }], { duration: dur, fill: "forwards" });
  }
  flash(alpha = 0.5, dur = 280) {
    this.el.arenaFlash.animate([{ opacity: alpha }, { opacity: 0 }], { duration: dur, easing: "ease-out" });
  }
  pop(el) {
    // powiększenie "w dół" – cyfra nie zahacza o napis TY / ROBOT nad nią
    el.animate([{ transform: "scale(1.3)", transformOrigin: "50% 0%" }, { transform: "scale(1)", transformOrigin: "50% 0%" }], { duration: 420, easing: "cubic-bezier(.2,.8,.2,1)" });
  }

  // ---------------------------------------------------------------- zamrożona klatka / powtórka
  /** Obraz, który widać teraz na ekranie: kolor albo "jak widzi robot" (głębia / podczerwień). */
  shown() {
    const v = this.el.camBox.dataset.shown;
    const vis = $("vision");
    return v && v !== "color" && vis && vis.naturalWidth ? { img: vis, ir: v === "ir" } : { img: this.img, ir: false };
  }

  freezeNow(s) {
    const { ctx, W, H } = fitCanvas(this.el.freeze);
    ctx.clearRect(0, 0, W, H);
    const src = this.shown();
    const map = coverDraw(ctx, src.img.naturalWidth ? src.img : null, W, H);
    const lm = s && s.hand && s.hand.landmarks;
    // podczerwień to inna kamera (inny kadr): bez szkieletu, żeby nie był przesunięty
    if (lm && lm.length === 21 && !src.ir) skeleton(ctx, lm.map(([x, y]) => map(x, y)), C.route, C.paint, 1.2);
    this.frozen = true;
    this.el.camBox.classList.add("frozen");
  }
  unfreeze() {
    this.frozen = false;
    this.el.camBox.classList.remove("frozen");
  }

  // ---------------------------------------------------------------- stan z serwera (20/s)
  onState(s, now) {
    const g = s && s.game;
    const active = !!(g && g.active);
    const P = this.prev;
    if (active && !P.active) this.enter(now);
    if (!active && P.active) this.leave();
    if (active) {
      const key = `${g.phase}|${g.beat}|${g.round}|${(g.history || []).length}|${g.warmup ? 1 : 0}`;
      // błąd w jednym przejściu nie może zatrzymać reszty sceny (i tak sprząta tidy)
      if (key !== P.key) { try { this.transition(g, s, now, P); } catch (e) { this.oops(e); } }
      try { this.tidy(g, now); } catch (e) { this.oops(e); }
      // punkt na tablicy dopiero razem z napisem wyniku (ok. 0,45 s po odsłonie)
      let sc = g.score || { human: 0, robot: 0 };
      if (g.phase === "reveal" && now - this.revealAt < 450 && P.score) sc = P.score;
      if (P.score && sc.human > P.score.human) this.pop(this.el.scHuman);
      if (P.score && sc.robot > P.score.robot) this.pop(this.el.scRobot);
      this.text(this.el.scHuman, String(sc.human));
      this.text(this.el.scRobot, String(sc.robot));
      this.text(this.el.arenaRound, g.phase === "final" ? "KONIEC" : g.warmup ? "ROZGRZEWKA"
        : g.wins ? (g.decider ? "RUNDA DECYDUJĄCA" : `RUNDA ${g.round}`) : `RUNDA ${g.round} / ${g.rounds}`);
      if (g.phase === "read") this.text(this.el.moveHuman, g.live ? MOVE_UP[g.live] : "…");
      this.prev = { active, key, phase: g.phase, score: { ...sc } };
    } else {
      this.prev = { active: false };
    }
    this.g = g;
    this.s = s;
  }

  /** Błędy areny: do konsoli i do window.__arenaErrors (podgląd błędów na stoisku). */
  oops(e) {
    console.error("arena", e);
    const log = (window.__arenaErrors = window.__arenaErrors || []);
    if (log.length < 50) log.push(`${Math.round(performance.now())} ${String(e && e.stack || e)}`);
  }

  /** Trzy gesty do wyboru: rysowane w chwili pokazania (schowane płótno nie ma rozmiaru). */
  showMoves() {
    this.show(this.el.arenaMoves, true);
    for (const pose of ["paper", "rock", "scissors"]) drawPictogram($(`picto-${pose}`), pose);
  }

  /** Czysta scena: nic z poprzedniej gry ani fazy nie zostaje na ekranie. */
  resetScene() {
    this.clearLater();
    for (const id of SCENE_EL) { this.hideNow(this.el[id]); this.el[id].hidden = true; }
    this.st.classList.remove("replaying", "final", "choice");
    this.unfreeze();
    this.replay = null;
    this.clip = null;
  }

  /** Pilnuje, żeby w danej fazie było widać tylko to, co do niej należy (wywoływane 20 razy na sekundę). */
  tidy(g, now) {
    const ok = new Set(ALLOWED[g.phase] || []);
    if (!g.warmup) ok.delete("arenaHint");                                                   // tylko w rozgrzewce
    if (g.phase === "reveal" && now - this.revealAt > 400) ok.delete("arenaWord");          // słowo już wygaszone
    if (g.phase === "final") {
      if (this.replay && now - this.replay.t0 > 400) ok.delete("finalBox");                   // powtórka zastąpiła wynik
      if (!this.replay) { ok.delete("replayBadge"); ok.delete("replayCap"); }
    }
    for (const id of SCENE_EL) {
      const el = this.el[id];
      const allowed = ok.has(id);
      // display: none – niezależnie od animacji i klas: tego, co nie należy do fazy, nie da się zobaczyć
      if (el.hidden === allowed) el.hidden = !allowed;
      if (!allowed && el.classList.contains("on")) el.classList.remove("on");
      if (!allowed) el.getAnimations().forEach((a) => a.cancel());
    }
    if (g.phase !== "final") {
      this.st.classList.remove("final", "choice");
      if (this.replay) { this.replay = null; this.st.classList.remove("replaying"); }
    }
    if (this.frozen && g.phase !== "reveal" && g.phase !== "final") this.unfreeze();
  }

  enter(now) {
    this.st.classList.add("arena-on");
    this.resetScene();
    this.rec.reset();
    this.ai.reaction(null, now);
    this.ai.set("paper", now);
  }

  leave() {
    this.st.classList.remove("arena-on");
    this.resetScene();
    this.rec.reset();
  }

  hideReveal() {
    for (const id of ["resTitle", "moveHuman", "moveRobot"]) {
      this.el[id].getAnimations().forEach((a) => a.cancel());
      this.show(this.el[id], false);
    }
  }

  transition(g, s, now, P) {
    const E = this.el;
    switch (g.phase) {
      case "intro": {
        // nowy mecz zawsze od czystej sceny (także gdy poprzedni wynik był jeszcze na ekranie)
        this.resetScene();
        this.rec.reset();
        this.show(E.arenaVs, true);
        this.slam(E.arenaVs, 2.2, 420);
        // "VS" i trzy ruchy znikają, zanim padnie pierwsze słowo odliczania (nic się nie nakłada)
        this.schedule(Math.max(600, (g.duration || 3.4) * 1000 - 420), () => { this.fadeOut(E.arenaVs, 260); this.fadeOut(E.arenaMoves, 260); });
        this.schedule(900, () => {
          this.showMoves();
          E.arenaMoves.animate([{ opacity: 0, transform: "translateY(30px)" }, { opacity: 1, transform: "none" }], { duration: 420, easing: "cubic-bezier(.2,.8,.2,1)", fill: "forwards" });
        });
        break;
      }
      case "countdown": {
        if (P.phase !== "countdown") {
          // nowa runda: sprzątamy po poprzedniej, robot zaciska pięść palec po palcu
          this.clearLater();
          this.hideReveal();
          this.unfreeze();
          this.hideNow(E.arenaVs); this.hideNow(E.arenaMoves);
          this.ai.reaction(null, now);
          this.ai.set("rock", now, { opening: false });
          if (g.decider) { this.rec.reset(); this.rec.on = true; }     // runda decydująca: nagrywamy (powtórka)
        }
        if (g.beat === 2) this.tBeat2 = now;
        this.ai.beatMs = (g.beat_s || 0.8) * 1000;                // pompowanie w rytmie serwera (i kół)
        if (g.warmup) this.show(E.arenaHint, true);                // rozgrzewka: "Pokaż gest dopiero na TERAZ!"
        this.text(E.arenaWord, WORDS[g.beat ?? 0] || "");
        this.show(E.arenaWord, true);
        this.slam(E.arenaWord, 1.6, 230);
        this.ai.pump(now);
        break;
      }
      case "read": {
        this.tGo = now;
        this.text(E.arenaWord, WORDS[3]);
        this.show(E.arenaWord, true);
        this.slam(E.arenaWord, 1.9, 240);
        this.ai.pump(now);                                   // ostatnie uderzenie, pięść czeka na rzut
        this.text(E.moveHuman, "…");
        this.show(E.moveHuman, true);
        break;
      }
      case "reveal": this.reveal(g, s, now, P); break;
      case "miss": {
        this.clearLater();
        this.hideNow(E.arenaWord);
        this.ai.set("paper", now);
        this.text(E.resTitle, "NIE WIDZĘ GESTU");
        E.resTitle.dataset.tone = "muted";
        this.show(E.resTitle, true);
        this.slam(E.resTitle, 1.4, 260);
        this.showMoves();                                    // przypomnienie: trzy gesty do wyboru
        break;
      }
      case "final": this.final(g, now); break;
      case "abort": {
        this.clearLater();                                   // bez spóźnionego napisu wyniku rundy
        this.hideReveal();
        this.hideNow(E.arenaWord);
        this.ai.set("paper", now);
        this.text(E.resTitle, "KONIEC GRY");
        E.resTitle.dataset.tone = "muted";
        this.show(E.resTitle, true);
        this.slam(E.resTitle, 1.4, 260);
        break;
      }
      default: break;
    }
  }

  reveal(g, s, now, P = {}) {
    const E = this.el;
    this.revealAt = now;
    // "Teraz!" mogło trwać krócej niż jedna wiadomość z serwera (runda robota): moment startu z rytmu odliczania
    if (P.phase !== "read") this.tGo = this.tBeat2 ? Math.min(now, this.tBeat2 + (g.beat_s || 0.8) * 1000) : now;
    this.fadeOut(E.arenaWord, 160);
    this.flash(0.45, 300);
    if (this.rec.on) this.clip = this.rec.cut(this.tGo);
    this.freezeNow(s);
    // robot "rzuca" ręką i otwiera palce po kolei w swój ruch
    this.ai.throwHand(now);
    this.ai.set(g.robot, now, { flash: true });

    this.text(E.moveHuman, MOVE_UP[g.human] || "BRAK GESTU");          // walkower: gracz nic nie pokazał
    this.text(E.moveRobot, MOVE_UP[g.robot] || "");
    for (const [el, from, delay] of [[E.moveHuman, -50, 0], [E.moveRobot, 50, 120]]) {
      this.show(el, true);
      el.animate([{ opacity: 0, transform: `translateX(${from}px)` }, { opacity: 1, transform: "none" }], { duration: 300, delay, easing: "cubic-bezier(.2,.8,.2,1)", fill: "both" });
    }

    const res = g.result;
    const warm = !!g.warmup;
    this.schedule(450, () => {
      const t = performance.now();
      // rozgrzewka: gest rozpoznany, punkt się nie liczy
      E.resTitle.dataset.tone = warm ? "human" : res;
      this.text(E.resTitle, warm ? "DOBRZE!" : res === "human" ? "PUNKT DLA CIEBIE" : res === "robot" ? "PUNKT DLA ROBOTA" : "REMIS");
      this.show(E.resTitle, true);
      this.slam(E.resTitle, 1.7, 300);
      this.ai.reaction(warm ? null : res === "robot" ? "win" : res === "human" ? "lose" : null, t);
    });
  }

  final(g, now) {
    const E = this.el;
    this.clearLater();
    this.hideReveal();
    this.hideNow(E.arenaWord);
    this.st.classList.add("final");
    const w = g.winner;
    const sc = g.score || { human: 0, robot: 0 };
    E.finalBox.dataset.tone = w;
    this.text(E.finalTitle, w === "human" ? "WYGRYWASZ" : w === "robot" ? "ROBOT WYGRYWA" : "REMIS");
    this.text($("againLabel"), w === "human" ? "Zagraj jeszcze raz" : "Spróbuj ponownie");
    this.text(E.finalH, String(sc.human));
    this.text(E.finalR, String(sc.robot));
    this.show(E.finalBox, true);
    E.finalBox.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 300, fill: "forwards" });
    this.slam(E.finalTitle, 1.6, 360);
    // robot wygrał: kciuk w górę i taniec; przegrał: otwarta, opadająca dłoń
    this.ai.set(w === "robot" ? "thumb" : "paper", now, { flash: w === "robot" });
    this.ai.reaction(w === "robot" ? "win" : w === "human" ? "lose" : null, now);

    // powtórka "nieuczciwej" rundy, potem wybór: jeszcze raz / menu
    const c = g.cheat;
    if (c && c.rec_s != null) {
      this.schedule(3600, () => {
        E.finalBox.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 300, fill: "forwards" });
        this.schedule(300, () => this.show(E.finalBox, false));
        this.startReplay(c, performance.now());
      });
    } else {
      this.schedule(2600, () => this.showChoice());
    }
  }

  /** Wynik meczu wraca z przyciskami "Zagraj jeszcze raz" / "Menu" (wybór: menu.js, FinalChoice). */
  showChoice() {
    const E = this.el;
    if (!this.g || this.g.phase !== "final" || this.st.classList.contains("choice")) return;
    this.unfreeze();                                          // kamera na żywo: gracz widzi swoją dłoń
    this.st.classList.add("choice");
    E.finalBox.getAnimations().forEach((a) => a.cancel());
    this.show(E.finalBox, true);                             // także zdejmuje display: none z czasu powtórki
    E.finalBox.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 360, fill: "forwards" });
  }

  startReplay(c, now) {
    const E = this.el;
    const since = c.since_s ?? 0;
    this.replay = { t0: now, vis: since, rec: since + c.rec_s, human: c.human, robot: c.robot, caught: false, loop: -1 };
    this.st.classList.add("replaying");
    this.ai.reaction(null, now);
    this.show(E.replayBadge, true);
    this.show(E.replayCap, true);
    E.replayBadge.animate([{ opacity: 0, transform: "translateY(-20px)" }, { opacity: 1, transform: "none" }], { duration: 360, delay: 160, fill: "both" });
    this.text(E.replayCap, "Przyznaję się: podejrzałem Twój ruch");
    E.replayCap.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 500, delay: 300, fill: "forwards" });
  }

  // ---------------------------------------------------------------- klatka animacji (~30/s)
  frame(dt, now) {
    const due = this.later.filter((x) => x.at <= now);
    if (due.length) { this.later = this.later.filter((x) => x.at > now); due.forEach((x) => x.fn()); }
    const g = this.g, s = this.s;
    const on = !!(g && g.active);

    // zaproszenie (scena "game", nikt nie gra): hologram z kciukiem w górę
    if (this.st.classList.contains("inviting")) {
      this.inviteHand.draw(this.el.inviteHand, now, { cx: 0.5, cy: 0.5, scale: 1.3, idle: true });
    }
    if (!on) {
      if (this.timelineOn) { drawTimeline(this.el.arenaFx, null); this.timelineOn = false; }
      return;
    }

    // nagrywanie ostatniej rundy (od "Kamień" do chwili odsłony)
    if (this.rec.on && (g.phase === "read" || (g.phase === "countdown" && g.beat >= 1))) {
      const src = this.shown();
      this.rec.capture(src.img, src.ir ? null : s && s.hand && s.hand.landmarks, now);
    }

    let timeline = null;
    const r = this.replay;
    if (r) {
      // powtórka zwolniona 4x, dwa razy: od chwili tuż przed pokazaniem gestu do chwili, gdy zareagowałby
      // człowiek (ok. 0,25 s po zobaczeniu) – oś bez długiego, pustego końca
      const SLOW = 4, hold = 1.4;
      const from = r.vis - 0.12, span = Math.max(r.rec, r.vis + HUMAN_S) + 0.12 - from;
      const total = span * SLOW + hold;
      const el = (now - r.t0) / 1000;
      const loop = Math.floor(el / total);
      const vt = from + Math.min((el % total) / SLOW, span);
      if (loop !== r.loop) { r.loop = loop; r.caught = false; this.ai.set("rock", now, { opening: false, fast: true }); }
      if (loop < 2) {
        this.drawReplayFrame(vt);
        if (vt >= r.rec && !r.caught) {
          r.caught = true;                                   // tu robot "przyłapał" gest
          this.ai.throwHand(now);
          this.ai.set(r.robot, now, { fast: true, flash: true });
        }
        timeline = { t: vt - r.vis, robot: r.rec - r.vis };
      } else {
        this.unfreeze();
        this.hideReveal();
        this.hideNow(this.el.replayBadge);
        this.hideNow(this.el.replayCap);
        this.st.classList.remove("replaying");
        this.replay = null;
        this.showChoice();
      }
    }

    const idle = g.phase === "intro" || g.phase === "miss" || g.phase === "abort" || (g.phase === "final" && !r);
    this.ai.draw(this.el.aiHand, now, r ? { scale: 0.84, cy: 0.47 } : { scale: 0.9, cy: 0.4, idle });
    if (timeline || this.timelineOn) { drawTimeline(this.el.arenaFx, timeline); this.timelineOn = !!timeline; }
  }

  drawReplayFrame(vt) {
    const clip = this.clip;
    const { ctx, W, H } = fitCanvas(this.el.freeze);
    if (!clip) { this.unfreeze(); return; }                      // bez nagrania: tylko oś czasu
    const t = this.tGo + vt * 1000;
    let f = clip[0];
    for (const c of clip) { if (c.t <= t) f = c; else break; }
    ctx.clearRect(0, 0, W, H);
    const map = coverDraw(ctx, f.c, W, H);
    ctx.fillStyle = "rgba(8,9,10,0.18)"; ctx.fillRect(0, 0, W, H);      // lekko przyciemnione = powtórka
    if (f.lm) skeleton(ctx, f.lm.map(([x, y]) => map(x, y)), C.route, C.paint, 1.2);
    if (!this.frozen) { this.frozen = true; this.el.camBox.classList.add("frozen"); }
  }
}

// ------------------------------------------------------------------ teksty poza areną
export function inviteRecord(g) {
  const st = (g && g.stats) || {};
  return st.games ? `Dziś robot wygrał ${st.robot} z ${st.games} ${plural(st.games, "gry", "gier", "gier")}` : "";
}
