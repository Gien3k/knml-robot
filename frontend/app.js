/* =============================================================================
   Człowiek kontra AI – autonomiczny łazik: logika ekranu stoiska (bez frameworków).
   Dane: WebSocket /ws, obraz: /stream.mjpg, "jak widzi robot": /vision.mjpg (shared/kn.js).
   Gra papier, kamień, nożyce przejmuje ekran (game.js: Arena). Poza grą: jedno hasło,
   wygładzane – krótkie, przypadkowe zmiany stanu nie przełączają napisu.
   Ekran startowy (menu.js): wybór gry dłonią – jazda robotem albo papier, kamień, nożyce.
   Klawisze (obsługa): 1 jazda, 2 gra, 0 menu, G gra, L następny widok, F pełny ekran,
   Shift+M lustro, Shift+R nauka tła radaru.
   ============================================================================= */
import { drawCalibDiagram, initCalib, renderCalib } from "./calib.js";
import { Arena, inviteRecord } from "./game.js";
import { BackButton, FinalChoice, Menu, chooseMode } from "./menu.js";
import { drawProRadar, updateRadarPanel } from "./radar.js";
import { $, connect, controls, drawHand, fmt, loop, onState, phase, sinceBrake, stage, store } from "./shared/kn.js";

const HAND = { hand: "#6cb4ff", handShadow: "rgba(10,11,12,0.6)", joint: "#f3f1ea", people: "rgba(243,241,234,0.75)" };
const HAND_DEPTH = { ...HAND, people: "rgba(243,241,234,0.9)", labels: "#f3f1ea" };
const HAND_GAME = { ...HAND, people: null };
const HAND_MENU = { ...HAND, people: null };            // w menu bez ramek i odległości osób (nie wchodzą na karty)
const DEVICES = [["oak", "OAK-D"], ["lidar", "LiDAR"], ["esp32", "ESP32"]];
const LENS_ORDER = ["color", "depth", "auto"];

const st = $("stage");
stage(st);
const img = $("cam");
connect(img, $("vision"));
controls();
initCalib();
const arena = new Arena(st, img);
window.__arena = arena;                                  // do diagnostyki z konsoli przeglądarki
const menu = new Menu(st, img);
const back = new BackButton(st, img);
const choice = new FinalChoice(st, img);          // po meczu: "Zagraj jeszcze raz" / "Menu"
let backOn = false;

// ------------------------------------------------------------------ teksty scen, gdy nikt nie steruje
const SCENE = {
  depth: { key: "s-depth", tone: "muted", title: "Tak widzi Cię robot",
    line: "Kolor to odległość: im jaśniej, tym bliżej. Robot liczy ją z dwóch kamer, tak jak Ty dwojgiem oczu." },
  ir: { key: "s-ir", tone: "muted", title: "Niewidzialne kropki",
    line: "Projektor rzuca tysiące kropek podczerwieni. Robot je widzi, Ty nie. Dzięki nim mierzy odległość nawet do gładkiej ściany." },
  game: { key: "s-game", tone: "route", title: "Zagraj z robotem",
    line: "Pokaż kciuk w górę i zagraj w papier, kamień, nożyce. Trzy rundy, zobaczymy, kto wygra." },
  start: { key: "s-start", tone: "route", title: "Przytrzymaj kciuk…", line: "Zaraz zaczynamy grę w papier, kamień, nożyce." },
};

function message(s) {
  const g = s && s.game;
  const p = phase(s);
  if (g && !g.active && g.start_progress > 0 && p.tone !== "stop") return SCENE.start;
  if (s && (p.key === "idle" || p.key === "seen") && s.lens) {
    const L = s.lens;
    if (L.scene === "depth" && L.shown === "depth") return SCENE.depth;
    if (L.scene === "game" && g && g.available) return SCENE.game;
  }
  return p;
}

// ------------------------------------------------------------------ hasło bez migania
const HOLD_MS = 600;      // ile najkrócej trzymamy wyświetlone hasło
const CONFIRM_MS = 250;   // ile nowy stan musi trwać, zanim go pokażemy
let shown = null, shownAt = 0, cand = null, candAt = 0;
function stableMessage(p, now) {
  if (!shown || p.key === "s-start" || (p.tone === "stop" && shown.key !== p.key)) {
    shown = p; shownAt = now; cand = null; return shown;
  }
  if (p.key === shown.key) { shown = p; cand = null; return shown; }           // ten sam stan, nowe liczby
  if (!cand || cand.key !== p.key) { cand = p; candAt = now; }
  if (now - candAt >= CONFIRM_MS && now - shownAt >= HOLD_MS) { shown = p; shownAt = now; cand = null; }
  return shown;
}

const setText = (id, v) => { const n = $(id); if (n.textContent !== v) n.textContent = v; };

// ------------------------------------------------------------------ legenda głębi (z konfiguracji serwera)
let legendFor = null;
function buildLegend() {
  const d = store.cfg.depth;
  if (!d || legendFor === d) return;
  legendFor = d;
  const span = d.far_m - d.near_m;
  const pos = (m) => Math.pow(Math.max(0, Math.min(1, (m - d.near_m) / span)), 0.8);   // jak w vision.py
  const grad = `linear-gradient(90deg, ${d.palette.map(([t, c]) => `${c} ${(t * 100).toFixed(1)}%`).join(", ")})`;
  $("legendBar").style.background = grad;
  $("menuLegend").style.background = grad;
  $("legendTicks").innerHTML = [0.5, 1, 2, 3].map((m) =>
    `<span style="left:${(pos(m) * 100).toFixed(1)}%">${fmt(m, m % 1 ? 1 : 0)} m</span>`).join("")
    + `<span class="far" style="left:100%">dalej</span>`;
}

// ------------------------------------------------------------------ soczewki
const lensButtons = [...document.querySelectorAll("#lenses button")];
function chooseLens(lens) {
  fetch("/api/lens", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lens }) })
    .catch(() => {});
}
for (const b of lensButtons) b.addEventListener("click", () => chooseLens(b.dataset.lens));

function updateLens(s) {
  const L = s && s.lens;
  const avail = !!(L && L.available && s.cam && s.cam.live);
  $("lenses").hidden = !avail;
  const want = avail ? L.lens : "color";
  // obraz głębi / podczerwieni wchodzi dopiero, gdy przychodzą jego świeże klatki
  const showing = avail && want !== "color" && L.shown === want ? want : "color";
  const box = $("camBox");
  if (box.dataset.shown !== showing) box.dataset.shown = showing;
  for (const b of lensButtons) {
    const on = b.dataset.lens === want;
    b.classList.toggle("on", on);
    b.classList.toggle("auto", on && !!(L && L.auto));
    const bar = b.lastElementChild;
    const tr = on && L ? `scaleX(${Math.min(1, L.progress || 0).toFixed(3)})` : "scaleX(0)";
    if (bar.style.transform !== tr) bar.style.transform = tr;
  }
  buildLegend();
  $("legend").hidden = showing !== "depth";
  return showing;
}

// ------------------------------------------------------------------ stan -> DOM (20 Hz)
let lensShown = "color";
function update() {
  const s = store.state;
  const now = performance.now();
  arena.onState(s, now);

  const p = stableMessage(message(s), now);
  setText("title", p.title);
  setText("line", p.line);
  if (st.dataset.tone !== p.tone) st.dataset.tone = p.tone;

  const g = s && s.game;
  const md = (s && s.mode) || {};
  const menuOn = !!(md.menu && md.mode === "menu" && !(g && g.active));
  st.classList.toggle("menu-on", menuOn);
  backOn = !!(md.menu && (md.mode === "drive" || (g && g.active)));
  $("backBtn").hidden = !backOn;
  updateRadarPanel(s);
  const scene = menuOn && p.key === "s-depth" ? p.key : null;
  $("menuScene").hidden = !scene;
  if (scene) {
    setText("menuSceneText", scene === "s-depth" ? "Tak widzi Cię robot. Im jaśniej, tym bliżej." : "Podczerwień. Robot widzi kropki projektora, Ty nie.");
    $("menuLegend").hidden = scene !== "s-depth";
  }
  st.classList.toggle("inviting", !menuOn && (p.key === "s-game" || p.key === "s-start"));

  const b = (s && s.brake) || {};
  let record = b.record_s != null ? `Najszybsze hamowanie dziś: ${fmt(b.record_s)} s` : "";
  if (p.key === "s-game") record = inviteRecord(g);
  if (md.mode === "drive" && md.back_in != null) record = `Bez dłoni wracam do wyboru gry za ${Math.ceil(md.back_in)} s`;
  setText("record", record);

  $("simBadge").hidden = !(s && s.sim);
  $("camWait").classList.toggle("show", !(s && s.cam && s.cam.live));
  lensShown = updateLens(s);
  // dla obsługi: stopka z prawej, czujniki i temperatury (bez napisu o akumulatorze: koła i tak stają przy BATTERY_CRITICAL_V)
  // (kropka: zielona do 70°C, żółta do 80°C, potem czerwona, bo od 80°C Pi samo zwalnia)
  let temp = "";
  if (s && s.temp_c != null) {
    const lvl = s.temp_c >= 80 ? "hot" : s.temp_c >= 70 ? "warm" : "real";
    temp = `<span class="temp ${lvl}"><i class="${lvl}"></i>RPi ${Math.round(s.temp_c)}°C</span>`;
  }
  // kamera OAK-D: jedna pozycja ze stanem i temperaturą procesora kamery ("OAK-D 40°C");
  // do ok. 85°C spokojnie, wyżej napis żółty, od 95°C czerwony
  const chip = s && s.cam && s.cam.chip_c;
  const device = ([k, n]) => {
    const dot = `<i class="${(s.devices[k] || {}).state || ""}"></i>`;
    if (k !== "oak" || chip == null) return `<span>${dot}${n}</span>`;
    const lvl = chip >= 95 ? "hot" : chip >= 85 ? "warm" : "";
    return `<span class="temp ${lvl}">${dot}${n} ${Math.round(chip)}°C</span>`;
  };
  const diag = s ? DEVICES.map(device).join("") + temp : "";
  if ($("diag").innerHTML !== diag) $("diag").innerHTML = diag;
  renderCalib(s && s.calib);
}
onState(update);
setInterval(update, 200);

// ------------------------------------------------------------------ klawiatura obsługi
addEventListener("keydown", (e) => {
  if (document.body.dataset.modal) return;
  const k = e.key.toLowerCase();
  if (k === "1") chooseMode("drive");
  else if (k === "2") chooseMode("game");
  else if (k === "0" || k === "escape") chooseMode("menu");
  else if (k === "g") {
    const g = store.state && store.state.game;
    fetch(`/api/game/${g && g.active ? "stop" : "start"}`, { method: "POST" }).catch(() => {});
  } else if (k === "l") {
    const L = store.state && store.state.lens;
    const cur = L ? (L.auto ? "auto" : L.lens) : "auto";
    chooseLens(LENS_ORDER[(LENS_ORDER.indexOf(cur) + 1) % LENS_ORDER.length]);
  }
});

// ------------------------------------------------------------------ cały dzień na stoisku
// co kilka godzin strona odświeża się sama (czyści pamięć przeglądarki), ale tylko w menu,
// gdy od minuty nikt nie pokazuje dłoni – nigdy w trakcie gry czy jazdy
const RELOAD_AFTER_MS = 4 * 3600 * 1000;
let lastHand = performance.now();
function selfHeal(s, now) {
  if (s && s.hand) lastHand = now;
  // nowa wersja ekranu na serwerze (wgranie / restart): przeładowanie od razu, byle nie w trakcie gry
  if (store.stale && !(s && s.game && s.game.active) && !document.body.dataset.modal) { store.stale = false; location.reload(); return; }
  const calm = s && s.mode && s.mode.mode === "menu" && !(s.game && s.game.active) && now - lastHand > 60000;
  if (now > RELOAD_AFTER_MS && calm && !document.body.dataset.modal) location.reload();
}

// ------------------------------------------------------------------ rysowanie (~30/s)
loop((dt, now) => {
  const s = store.state;
  const g = s && s.game;
  const playing = !!(g && g.active);
  drawCalibDiagram(now);
  const ring = g && !playing ? g.start_progress || 0 : 0;
  const inMenu = st.classList.contains("menu-on");
  drawHand($("ov"), img, playing ? HAND_GAME : inMenu ? { ...HAND_MENU, ring }
    : lensShown === "depth" ? { ...HAND_DEPTH, ring } : { ...HAND, ring });
  arena.frame(dt, now);
  if (st.classList.contains("menu-on")) menu.frame(dt, now, s);
  back.frame(dt, now, s, backOn);
  choice.frame(dt, now, s);
  if (!playing && !st.classList.contains("menu-on")) drawProRadar($("radarPro"), dt, now);
  // czerwona ramka tylko w jeździe (albo po prawdziwym hamowaniu kręcących się kół),
  // a nie za każdym razem, gdy ktoś sięga po coś obok stojącego robota
  const md = (s && s.mode) || {};
  const estop = s && s.drive && s.drive.state === "ESTOP" && (!md.menu || md.mode === "drive");
  st.classList.toggle("braking", !!estop || sinceBrake() < 1500);
  selfHeal(s, now);
}, 30);
