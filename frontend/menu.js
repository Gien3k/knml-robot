/* =============================================================================
   Ekran startowy: odwiedzający wybiera grę DŁONIĄ – bez dotykania ekranu.
   Kursorem jest środek dłoni widzianej przez kamerę (tam, gdzie odwiedzający widzi
   swoją dłoń na obrazie). Najechanie na kartę i przytrzymanie DWELL_MS = wybór.
   Działa też dotyk / kliknięcie karty i klawisze 1 / 2 (obsługa).
   Wybór idzie do serwera (POST /api/mode/drive | game) – to on decyduje, czy koła słuchają dłoni.
   ============================================================================= */
import { AiHand } from "./game.js";
import { $, clamp, fitCanvas, mirrored, roundRect, sceneQuery } from "./shared/kn.js";

const DWELL_MS = 1600;               // tyle trzeba przytrzymać dłoń nad kartą / przyciskiem
const BACK_MS = 2000;                // "‹ MENU" dłuższe: rzadziej przez przypadek
const MAX_DIST_M = 1.8;               // dalej = przechodzień, nie wybiera

const easeInOut = (t) => { t = clamp(t, 0, 1); return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2; };
function slamSmall(el, text) {
  el.textContent = text;
  el.getAnimations().forEach((a) => a.cancel());
  el.animate([{ transform: "scale(1.6) skewX(-6deg)", opacity: 0 }, { transform: "scale(0.95) skewX(-6deg)", opacity: 1, offset: 0.6 },
    { transform: "scale(1) skewX(-6deg)", opacity: 1 }], { duration: 220, easing: "cubic-bezier(.2,.8,.2,1)", fill: "forwards" });
}
function fadeSmall(el) {
  el.getAnimations().forEach((a) => a.cancel());
  el.animate([{ opacity: getComputedStyle(el).opacity }, { opacity: 0 }], { duration: 180, fill: "forwards" });
}

// ------------------------------------------------------------------ podgląd gry: "nagrana" rozgrywka w pętli
const DEMO_ROUNDS = [["paper", "rock", "human"], ["scissors", "scissors", "draw"], ["rock", "paper", "robot"]];
const DEMO_WORDS = ["PAPIER", "KAMIEŃ", "NOŻYCE", "TERAZ!"];
const DEMO_RES = { human: "PUNKT DLA CIEBIE", robot: "PUNKT DLA ROBOTA", draw: "REMIS" };
const DEMO_BEAT = 520;
const DEMO_ROUND_MS = 4000;

class GameDemo {
  constructor() {
    this.human = new AiHand("paper", { tone: "human", mirror: true, beatMs: DEMO_BEAT });
    this.robot = new AiHand("paper", { beatMs: DEMO_BEAT });
    this.t0 = null;
    this.round = -1;
    this.fired = new Set();
    this.score = [0, 0];
  }

  frame(now) {
    if (this.t0 === null) this.t0 = now;
    const el = now - this.t0, r = Math.floor(el / DEMO_ROUND_MS), t = el % DEMO_ROUND_MS;
    const [hm, rm, res] = DEMO_ROUNDS[r % DEMO_ROUNDS.length];
    if (r !== this.round) {
      // nowa runda: obie dłonie zaciskają pięść palec po palcu
      this.round = r;
      this.fired = new Set();
      if (r % DEMO_ROUNDS.length === 0) this.score = [0, 0];
      $("demoScore").textContent = `${this.score[0]} : ${this.score[1]}`;
      fadeSmall($("demoRes"));
      for (const h of [this.human, this.robot]) { h.reaction(null, now); h.set("rock", now, { opening: false }); }
    }
    const cue = (ms, key, fn) => { if (t >= ms && !this.fired.has(key)) { this.fired.add(key); fn(); } };
    for (let b = 0; b < 4; b++) {
      cue(420 + b * DEMO_BEAT, `b${b}`, () => {
        slamSmall($("demoWord"), DEMO_WORDS[b]);
        this.human.pump(now); this.robot.pump(now);
      });
    }
    const tRev = 420 + 3 * DEMO_BEAT + 240;
    cue(tRev, "rev", () => {
      fadeSmall($("demoWord"));
      this.human.throwHand(now); this.robot.throwHand(now);
      this.human.set(hm, now);
      this.robot.set(rm, now, { flash: true });
    });
    cue(tRev + 420, "res", () => {
      const rEl = $("demoRes");
      rEl.dataset.tone = res;
      slamSmall(rEl, DEMO_RES[res]);
      if (res === "human") this.score[0]++;
      if (res === "robot") this.score[1]++;
      $("demoScore").textContent = `${this.score[0]} : ${this.score[1]}`;
      this.robot.reaction(res === "robot" ? "win" : res === "human" ? "lose" : null, now);
      this.human.reaction(res === "human" ? "win" : res === "robot" ? "lose" : null, now);
    });
    this.human.draw($("demoHuman"), now, { cx: 0.47, cy: 0.5, scale: 1.2 });
    this.robot.draw($("demoRobot"), now, { cx: 0.6, cy: 0.5, scale: 1.2 });
  }
}

// ------------------------------------------------------------------ podgląd jazdy: dłoń bliżej = koła szybciej
const DRIVE_LOOP_MS = 7000;

function drawMiniRover(canvas, v, phase) {
  const { ctx, W, H } = fitCanvas(canvas);
  ctx.clearRect(0, 0, W, H);
  // środek widocznej (ściętej skosem) części prawej połowy; wyżej, bo pod spodem są strzałki i gaz
  const cx = W * 0.564, cy = H * 0.42;
  const bw = 64, bl = 96, ww = 22, wl = 36, gap = 7;
  const col = v > 0.03 ? "63,194,122" : v < -0.03 ? "108,180,255" : "243,241,234";
  const a = Math.min(1, Math.abs(v));
  // strzałki ruchu (przód robota = dół, w stronę widza)
  if (a > 0.05) {
    const dir = v > 0 ? 1 : -1;
    for (let k = 0; k < 3; k++) {
      const f = (((k / 3 + phase * 0.03 * dir) % 1) + 1) % 1;
      const y = cy + dir * (bl / 2 + 16 + f * 46);
      ctx.strokeStyle = `rgba(${col},${0.6 * a * (1 - f)})`; ctx.lineWidth = 4; ctx.lineCap = "round";
      ctx.beginPath(); ctx.moveTo(cx - 14, y - 7 * dir); ctx.lineTo(cx, y); ctx.lineTo(cx + 14, y - 7 * dir); ctx.stroke();
    }
  }
  roundRect(ctx, cx - bw / 2, cy - bl / 2, bw, bl, 10);
  ctx.fillStyle = "#1d2023"; ctx.fill();
  ctx.strokeStyle = "rgba(243,241,234,0.3)"; ctx.lineWidth = 1.5; ctx.stroke();
  ctx.fillStyle = "#6cb4ff";
  roundRect(ctx, cx - bw * 0.28, cy + bl / 2 - 10, bw * 0.56, 5, 2.5); ctx.fill();          // kamera = przód
  for (const x of [cx - bw / 2 - gap - ww, cx + bw / 2 + gap]) {
    for (const y of [cy - bl / 2 + 2, cy + bl / 2 - wl - 2]) {
      ctx.save();
      roundRect(ctx, x, y, ww, wl, 6);
      ctx.fillStyle = "#141618"; ctx.fill(); ctx.clip();
      const step = 8, off = ((phase % step) + step) % step;
      ctx.strokeStyle = a > 0.03 ? `rgba(${col},${0.35 + 0.65 * a})` : "rgba(243,241,234,0.14)"; ctx.lineWidth = 2.5;
      ctx.beginPath();
      for (let ty = y - step + off; ty < y + wl + step; ty += step) { ctx.moveTo(x + 3, ty); ctx.lineTo(x + ww - 3, ty); }
      ctx.stroke();
      ctx.restore();
    }
  }
}

class DriveDemo {
  constructor() {
    this.hand = new AiHand("paper", { tone: "human", mirror: true });
    this.t0 = null;
    this.v = 0;
    this.tread = 0;
  }

  frame(now, dt) {
    if (this.t0 === null) this.t0 = now;
    const t = (now - this.t0) % DRIVE_LOOP_MS;
    let pose = "paper", scale, target, cap;
    if (t < 2600) {                                  // dłoń zbliża się do kamery: coraz szybciej
      const k = easeInOut(t / 2600);
      scale = 0.85 + 0.4 * k; target = 0.25 + 0.75 * k; cap = "Bliżej = szybciej";
    } else if (t < 3500) {
      scale = 1.25; target = 1; cap = "Bliżej = szybciej";
    } else if (t < 5700) {                           // pięść: jazda do tyłu
      pose = "rock"; scale = 1.15; target = t < 3800 ? 0 : -0.6; cap = "Pięść = do tyłu";
    } else {                                         // dłoń się oddala: stop
      const k = easeInOut((t - 5700) / 1300);
      scale = 1.25 - 0.4 * k; target = 0; cap = "Dalej = wolniej";
    }
    if (pose !== this.hand.pose) this.hand.set(pose, now, { opening: pose !== "rock" });
    this.v += (target - this.v) * (1 - Math.exp(-dt * 5));
    this.tread += this.v * dt * 60;
    this.hand.draw($("driveHand"), now, { cx: 0.47, cy: 0.52, scale });
    drawMiniRover($("driveBot"), this.v, this.tread);
    const g = $("driveGauge");
    const tr = `scaleX(${Math.min(1, Math.abs(this.v)).toFixed(3)})`;
    if (g.style.transform !== tr) g.style.transform = tr;
    const bg = this.v < -0.03 ? "rgb(108, 180, 255)" : "rgb(63, 194, 122)";
    if (g.style.background !== bg) g.style.background = bg;
    const c = $("driveCap");
    if (c.textContent !== cap) c.textContent = cap;
  }
}

export function chooseMode(mode) {
  return fetch(`/api/mode/${mode}${sceneQuery()}`, { method: "POST" }).catch(() => {});
}

/** Środek dłoni w pikselach sceny (kadr z kamery: object-fit cover, lustro jak na ekranie).
 *  shiftPx: przesunięcie obrazu (w grze obraz z kamery jedzie w lewo). */
export function handScreenPoint(s, img, shiftPx = 0) {
  const h = s && s.hand;
  if (!h || !(s.cam && s.cam.live)) return null;
  if (h.dist_m != null && h.dist_m > MAX_DIST_M) return null;
  let x = h.x_norm, y = h.y_norm;
  if (h.landmarks && h.landmarks.length === 21) {
    const idx = [0, 5, 9, 13, 17];
    x = idx.reduce((a, i) => a + h.landmarks[i][0], 0) / 5;
    y = idx.reduce((a, i) => a + h.landmarks[i][1], 0) / 5;
  }
  if (x == null || y == null) return null;
  const box = $("camBox");
  const W = box.offsetWidth, H = box.offsetHeight;
  const ia = img.naturalWidth && img.naturalHeight ? img.naturalWidth / img.naturalHeight : 16 / 9;
  const dw = Math.max(W, H * ia), dh = dw / ia;
  let px = (W - dw) / 2 + x * dw;
  const py = (H - dh) / 2 + y * dh;
  if (mirrored()) px = W - px;
  return [px + shiftPx, py];
}

let cursorDirty = false;                 // na cursorFx coś narysowano w poprzedniej klatce

function drawCursor(ctx, cx, cy, p) {
  cursorDirty = true;
  ctx.lineCap = "round";
  ctx.strokeStyle = "rgba(243,241,234,0.28)"; ctx.lineWidth = 6;
  ctx.beginPath(); ctx.arc(cx, cy, 44, 0, Math.PI * 2); ctx.stroke();
  if (p > 0) {
    ctx.strokeStyle = "#6cb4ff"; ctx.lineWidth = 9;
    ctx.beginPath(); ctx.arc(cx, cy, 44, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * p); ctx.stroke();
  }
  ctx.fillStyle = "rgba(108,180,255,0.25)";
  ctx.beginPath(); ctx.arc(cx, cy, 22, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "#f3f1ea";
  ctx.beginPath(); ctx.arc(cx, cy, 9, 0, Math.PI * 2); ctx.fill();
}

/** Wybór dłonią – wspólny dla kart, przycisków po meczu i "‹ MENU":
 *  - kursor wygładzony (drżenie dłoni nie skacze po krawędzi karty),
 *  - cel "przyklejony": raz wybrana karta zostaje celem, dopóki kursor wyraźnie z niej nie zjedzie,
 *  - krótka przerwa w śledzeniu dłoni (do 0,5 s) wstrzymuje postęp zamiast go kasować,
 *  - po wyborze 1,5 s przerwy (bez przypadkowego drugiego wyboru). */
class Dwell {
  constructor(ms) {
    this.ms = ms;
    this.cursor = null;
    this.target = null;
    this.progress = 0;
    this.seen = -1e9;
    this.pause = 0;
  }

  update(dt, now, pt, pick) {
    if (pt) {
      this.seen = now;
      const k = this.cursor ? 1 - Math.exp(-dt * 10) : 1;
      this.cursor = this.cursor ? [this.cursor[0] + (pt[0] - this.cursor[0]) * k, this.cursor[1] + (pt[1] - this.cursor[1]) * k] : [pt[0], pt[1]];
    } else if (now - this.seen < 500) {
      return null;                                   // dłoń mignęła: kursor i postęp czekają bez zmian
    } else {
      this.cursor = null;
    }
    const t = this.cursor ? pick(this.cursor[0], this.cursor[1], this.target) : null;
    if (now < this.pause) { this.progress = 0; this.target = t; return null; }
    if (t && t === this.target) {
      this.progress += dt * 1000;
    } else {
      this.progress = Math.max(0, this.progress - dt * 2000);
      if (this.progress === 0) this.target = t;
    }
    if (this.target && this.progress >= this.ms) {
      const done = this.target;
      this.progress = 0;
      this.pause = now + 1500;
      return done;
    }
    return null;
  }

  get p() { return clamp(this.progress / this.ms, 0, 1); }

  reset() { this.progress = 0; this.target = null; this.cursor = null; }
}

/** Położenie elementu w pikselach sceny 1920x946 (strona może być przeskalowana). */
function stageBox(stage, el) {
  const sr = stage.getBoundingClientRect(), k = sr.width / 1920 || 1, r = el.getBoundingClientRect();
  return { l: (r.left - sr.left) / k, r: (r.right - sr.left) / k, t: (r.top - sr.top) / k, b: (r.bottom - sr.top) / k };
}

/** Przycisk "Menu" w jeździe i w grze: dotyk / klawisz 0 albo dłoń przytrzymana nad przyciskiem.
 *  W trakcie rundy gry dłoń pokazuje gesty, więc wtedy działa tylko dotyk / klawisz. */
export class BackButton {
  constructor(stage, img) {
    this.st = stage;
    this.img = img;
    this.btn = $("backBtn");
    this.dwell = new Dwell(BACK_MS);
    this.btn.addEventListener("click", () => chooseMode("menu"));
  }

  frame(dt, now, s, visible) {
    const { ctx, W, H } = fitCanvas($("cursorFx"));
    if (cursorDirty) { ctx.clearRect(0, 0, W, H); cursorDirty = false; }
    const bar = this.btn.querySelector("i");
    const g = s && s.game;
    const midRound = !!(g && g.active && ["countdown", "read", "reveal", "miss"].includes(g.phase));
    if (!visible || midRound) {
      this.dwell.reset();
      if (bar.style.transform !== "scaleX(0)") bar.style.transform = "scaleX(0)";
      delete this.btn.dataset.hover;
      return;
    }
    const shifted = this.st.classList.contains("arena-on") && !this.st.classList.contains("choice");
    const pt = handScreenPoint(s, this.img, shifted ? -400 : 0);
    // strefa: lewy górny róg (przycisk + zapas); gdy kursor już tam jest, zapas większy (nie "mruga")
    const b = stageBox(this.st, this.btn);
    const done = this.dwell.update(dt, now, pt, (x, y, cur) => {
      const m = cur ? 150 : 90;
      return x < b.r + m && y < b.b + m ? "menu" : null;
    });
    if (done) chooseMode("menu");
    const inZone = this.dwell.target === "menu" && !!this.dwell.cursor;
    if (inZone) this.btn.dataset.hover = ""; else delete this.btn.dataset.hover;
    const tr = `scaleX(${inZone ? this.dwell.p.toFixed(3) : 0})`;
    if (bar.style.transform !== tr) bar.style.transform = tr;
    if (inZone) drawCursor(ctx, this.dwell.cursor[0], this.dwell.cursor[1], this.dwell.p);
  }
}

/** Po meczu: "Zagraj jeszcze raz" / "Menu". Dłoń nad przyciskiem przytrzymana DWELL_MS, dotyk albo klik.
 *  Liczy się tylko położenie w poziomie (jak przy kartach); dłoń na wprost trafia w przerwę między nimi. */
export class FinalChoice {
  constructor(stage, img) {
    this.st = stage;
    this.img = img;
    this.btns = [...document.querySelectorAll(".final-btn")];
    this.dwell = new Dwell(DWELL_MS);
    this.doneAt = 0;
    for (const b of this.btns) b.addEventListener("click", () => this.choose(b.dataset.act));
  }

  on() { return this.st.classList.contains("choice"); }

  choose(act) {
    const now = performance.now();
    if (!this.on() || now - this.doneAt < 1500) return;
    this.doneAt = now;
    this.dwell.reset();
    const b = this.btns.find((x) => x.dataset.act === act);
    if (b) { b.classList.add("chosen"); setTimeout(() => b.classList.remove("chosen"), 1000); }
    if (act === "again") fetch(`/api/game/start${sceneQuery()}`, { method: "POST" }).catch(() => {});
    else chooseMode("menu");
  }

  /** Przycisk pod kursorem; obecny cel trzyma się z większym zapasem (histereza). */
  btnAt(px, cur) {
    let hit = null;
    for (const b of this.btns) {
      const r = stageBox(this.st, b), m = b.dataset.act === cur ? 90 : 20;
      if (px >= r.l - m && px <= r.r + m) {
        if (b.dataset.act === cur) return cur;
        hit = hit || b.dataset.act;
      }
    }
    return hit;
  }

  /** Rysuje kursor na cursorFx (czyści go BackButton.frame, wywoływany wcześniej). */
  frame(dt, now, s) {
    const on = this.on();
    let done = null;
    if (on) done = this.dwell.update(dt, now, handScreenPoint(s, this.img), (x, y, cur) => this.btnAt(x, cur));
    else this.dwell.reset();
    if (done) this.choose(done);
    const cur = this.dwell.cursor, p = this.dwell.p;
    for (const b of this.btns) {
      const hov = on && !!cur && b.dataset.act === this.dwell.target;
      if (hov) b.dataset.hover = ""; else delete b.dataset.hover;
      const tr = `scaleX(${hov ? p.toFixed(3) : 0})`;
      const bar = b.querySelector("i");
      if (bar.style.transform !== tr) bar.style.transform = tr;
    }
    if (!on || !cur) return;
    const { ctx } = fitCanvas($("cursorFx"));
    drawCursor(ctx, cur[0], cur[1], p);
  }
}

export class Menu {
  constructor(stage, img) {
    this.st = stage;
    this.img = img;
    this.cards = [...document.querySelectorAll(".menu-card")];
    this.dwell = new Dwell(DWELL_MS);
    this.chosenAt = 0;
    this.gameDemo = new GameDemo();
    this.driveDemo = new DriveDemo();
    for (const c of this.cards) c.addEventListener("click", () => this.choose(c.dataset.mode));
  }

  choose(mode) {
    const now = performance.now();
    if (now - this.chosenAt < 1500) return;
    this.chosenAt = now;
    const card = this.cards.find((c) => c.dataset.mode === mode);
    if (card) {
      card.classList.add("chosen");
      setTimeout(() => card.classList.remove("chosen"), 1200);
    }
    this.dwell.reset();
    chooseMode(mode);
  }

  /** Karta pod kursorem; obecny cel trzyma się z większym zapasem (histereza), wejście na nową kartę – mały zapas. */
  cardAt(px, cur) {
    let hit = null;
    for (const c of this.cards) {
      const r = stageBox(this.st, c), m = c.dataset.mode === cur ? 110 : 20;
      if (px >= r.l - m && px <= r.r + m) {
        if (c.dataset.mode === cur) return cur;
        hit = hit || c.dataset.mode;
      }
    }
    return hit;
  }

  frame(dt, now, s) {
    // podglądy w kartach: "nagrana" jazda i rozgrywka
    this.driveDemo.frame(now, dt);
    this.gameDemo.frame(now);

    // kursor = dłoń
    const done = this.dwell.update(dt, now, handScreenPoint(s, this.img), (x, y, cur) => this.cardAt(x, cur));
    if (done) this.choose(done);
    const cur = this.dwell.cursor, hover = this.dwell.target, p = this.dwell.p;

    for (const c of this.cards) {
      const on = c.dataset.mode === hover && !!cur;
      if (on) c.dataset.hover = ""; else delete c.dataset.hover;
      const bar = c.querySelector(".menu-bar");
      const tr = `scaleX(${on ? p.toFixed(3) : 0})`;
      if (bar.style.transform !== tr) bar.style.transform = tr;
    }
    const hint = !(s && s.cam && s.cam.live) ? "Dotknij karty, żeby wybrać"
      : !cur ? "Pokaż dłoń do kamery i najedź nią na grę"
        : hover ? "Przytrzymaj dłoń…" : "Przesuń dłoń nad wybraną grę";
    const hn = $("menuHint");
    if (hn.textContent !== hint) hn.textContent = hint;

    // płynny kursor (płótno czyszczone tylko, gdy coś na nim było)
    const { ctx, W, H } = fitCanvas($("menuFx"));
    if (this.fxDirty) { ctx.clearRect(0, 0, W, H); this.fxDirty = false; }
    if (!cur) return;
    this.fxDirty = true;
    drawCursor(ctx, cur[0], cur[1], p);
  }
}
