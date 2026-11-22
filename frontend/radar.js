/* =============================================================================
   Panel LiDAR-u w trybie jazdy: duży, czytelny widok z góry.
   Robot w środku pełnego koła (w skali, z kręcącymi się kołami), przód = dół (w stronę odwiedzającego).
   Warstwy: pierścienie odległości z podpisami, pole widzenia kamery, obracająca się wiązka
   skanu, obrys wolnej przestrzeni, punkty (tło / nowe obiekty / wtargnięcie), strefa
   hamowania, najbliższa przeszkoda z odległością, osoby, dłoń z kamery.
   Skala "rybie oko": do 1 m liniowo, dalej ściśnięta (ludzie do ~3,5 m mieszczą się w kole).
   ============================================================================= */
import { $, clamp, fitCanvas, fmt, pct10, roundRect, store, wheelSpeed } from "./shared/kn.js";

const RMAX = 3.5;
const comp = (r) => (r <= 1 ? r : 1 + (r - 1) * 0.5);
const C = {
  paint: "#f3f1ea", concrete: "#9a9d9f", gravel: "#64686b", route: "#6cb4ff", go: "#3fc27a", stop: "#e5484d",
};
const tread = { L: 0, R: 0 };

function pill(ctx, x, y, text, color, bg = "rgba(8,9,10,0.82)", font = "600 15px Barlow, sans-serif") {
  ctx.font = font;
  const w = ctx.measureText(text).width + 16, h = 24;
  ctx.fillStyle = bg;
  roundRect(ctx, x - w / 2, y - h / 2, w, h, h / 2); ctx.fill();
  ctx.fillStyle = color; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  ctx.fillText(text, x, y + 1);
}

export function drawProRadar(canvas, dt, now) {
  const { ctx, W, H } = fitCanvas(canvas);
  ctx.clearRect(0, 0, W, H);
  const s = store.state;
  if (!s) return;
  const sh = s.shield || {};
  const scan = store.scan;
  // pełne koło w środku płótna (nic nie jest ucięte przy krawędzi)
  const cx = W / 2, cy = H / 2;
  const Rpx = Math.min(W, H) / 2 - 16;
  const sc = Rpx / comp(RMAX);
  const P = (X, Y) => { const r = Math.hypot(X, Y); const f = r > 1e-9 ? comp(r) / r : 1; return [cx + Y * f * sc, cy + X * f * sc]; };

  // tło koła
  ctx.save();
  ctx.beginPath(); ctx.arc(cx, cy, Rpx + 10, 0, Math.PI * 2); ctx.clip();
  const bg = ctx.createRadialGradient(cx, cy, 0, cx, cy, Rpx + 10);
  bg.addColorStop(0, "rgba(16,24,34,0.95)"); bg.addColorStop(1, "rgba(8,10,13,0.95)");
  ctx.fillStyle = bg; ctx.fillRect(0, 0, W, H);

  // promienie co 30° i pierścienie odległości
  ctx.strokeStyle = "rgba(243,241,234,0.05)"; ctx.lineWidth = 1;
  for (let k = 0; k < 12; k++) {
    const a = (k * Math.PI) / 6;
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(cx + Math.cos(a) * (Rpx + 10), cy + Math.sin(a) * (Rpx + 10)); ctx.stroke();
  }
  for (const r of [0.5, 1, 2, 3]) {
    ctx.strokeStyle = r === 1 ? "rgba(243,241,234,0.16)" : "rgba(243,241,234,0.08)";
    ctx.lineWidth = r === 1 ? 1.5 : 1;
    ctx.beginPath(); ctx.arc(cx, cy, comp(r) * sc, 0, Math.PI * 2); ctx.stroke();
  }

  // pole widzenia kamery
  const [ox, oy] = store.cfg.cam_offset || [0.1, 0];
  const half = ((store.cfg.hfov_deg || 69) * Math.PI) / 360;
  ctx.beginPath(); ctx.moveTo(...P(ox, oy));
  for (let k = 0; k <= 24; k++) { const a = -half + (2 * half * k) / 24; ctx.lineTo(...P(ox + RMAX * Math.cos(a), oy + RMAX * Math.sin(a))); }
  ctx.closePath();
  ctx.fillStyle = "rgba(108,180,255,0.06)"; ctx.fill();

  // obracająca się wiązka skanu (wizualnie wolniej niż prawdziwe 10 obr./s)
  if (sh.live && ctx.createConicGradient) {
    const ang = (now / 2600) * Math.PI * 2;
    const g = ctx.createConicGradient(ang, cx, cy);
    g.addColorStop(0, "rgba(108,180,255,0.22)"); g.addColorStop(0.09, "rgba(108,180,255,0)"); g.addColorStop(1, "rgba(108,180,255,0)");
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(cx, cy, Rpx + 10, 0, Math.PI * 2); ctx.fill();
  }

  // wolna przestrzeń: obrys z punktów skanu
  if (scan && sh.live) {
    // krótkie luki w skanie (pojedyncze zgubione pomiary) uzupełniamy z sąsiadów, żeby obrys nie miał "kolców"
    const rr = scan.r.map((v) => { const r = v / 1000; return r > 0.05 && r <= RMAX ? r : null; });
    const fill = rr.slice();
    for (let i = 0; i < 360; i++) {
      if (rr[i] !== null) continue;
      let a = 1, b = 1;
      while (a <= 6 && rr[(i - a + 360) % 360] === null) a++;
      while (b <= 6 && rr[(i + b) % 360] === null) b++;
      if (a <= 6 && b <= 6) {
        const ra = rr[(i - a + 360) % 360], rb = rr[(i + b) % 360];
        fill[i] = ra + (rb - ra) * (a / (a + b));
      }
    }
    const pts = [];
    for (let i = 0; i < 360; i += 2) {
      const r = fill[i] ?? RMAX;
      const a = (i * Math.PI) / 180;
      pts.push(P(r * Math.cos(a), r * Math.sin(a)));
    }
    ctx.beginPath();
    pts.forEach((p, k) => (k ? ctx.lineTo(...p) : ctx.moveTo(...p)));
    ctx.closePath();
    ctx.fillStyle = "rgba(108,180,255,0.07)"; ctx.fill();
    ctx.strokeStyle = "rgba(108,180,255,0.32)"; ctx.lineWidth = 1.5; ctx.stroke();
  }

  // strefa hamowania
  const Rz = comp(sh.radius_m || store.cfg.shield_radius_m || 0.3) * sc;
  if (sh.live) {
    if (!sh.ready) {
      ctx.strokeStyle = "rgba(108,180,255,0.25)"; ctx.lineWidth = 3;
      ctx.beginPath(); ctx.arc(cx, cy, Rz, 0, Math.PI * 2); ctx.stroke();
      ctx.strokeStyle = C.route;
      ctx.beginPath(); ctx.arc(cx, cy, Rz, -Math.PI / 2, -Math.PI / 2 + Math.PI * 2 * (sh.learn || 0)); ctx.stroke();
    } else if (sh.active) {
      ctx.fillStyle = "rgba(229,72,77,0.22)";
      ctx.beginPath(); ctx.arc(cx, cy, Rz, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = C.stop; ctx.lineWidth = 3; ctx.stroke();
    } else {
      ctx.strokeStyle = "rgba(63,194,122,0.85)"; ctx.lineWidth = 2; ctx.setLineDash([7, 6]);
      ctx.beginPath(); ctx.arc(cx, cy, Rz, 0, Math.PI * 2); ctx.stroke(); ctx.setLineDash([]);
    }
  }

  // punkty skanu
  const hits = new Set(sh.live && sh.intrusion ? sh.intrusion : []);
  if (scan && sh.live) {
    const fg = new Set(scan.fg || []);
    for (let i = 0; i < 360; i++) {
      const r = scan.r[i] / 1000;
      if (r <= 0.05 || r > RMAX + 0.5) continue;
      const a = (i * Math.PI) / 180;
      const [x, y] = P(r * Math.cos(a), r * Math.sin(a));
      const hit = hits.has(i), isFg = fg.has(i);
      ctx.fillStyle = hit ? C.stop : isFg ? C.paint : "rgba(154,157,159,0.55)";
      ctx.beginPath(); ctx.arc(x, y, hit ? 3.6 : isFg ? 2.8 : 1.8, 0, Math.PI * 2); ctx.fill();
    }
  }

  // osoby (tylko potwierdzone przez kamerę dostają podpis "osoba")
  const labels = [];
  if (scan && sh.live) {
    // w tłumie podpis dostają tylko 3 najbliższe osoby (reszta: same kółka), żeby nie było chaosu
    const persons = (scan.tracks || []).filter((t) => t.kind === "person").sort((a, b) => a.d - b.d);
    let named = 0;
    for (const tr of persons) {
      const col = tr.fused ? "108,180,255" : "154,157,159";
      if (tr.trail && tr.trail.length > 1) {
        ctx.strokeStyle = `rgba(${col},0.3)`; ctx.lineWidth = 2;
        ctx.beginPath();
        tr.trail.forEach(([X, Y], k) => (k ? ctx.lineTo(...P(X, Y)) : ctx.moveTo(...P(X, Y))));
        ctx.lineTo(...P(tr.x, tr.y)); ctx.stroke();
      }
      const [px, py] = P(tr.x, tr.y);
      ctx.fillStyle = `rgba(${col},${tr.fused ? 0.22 : 0.1})`;
      ctx.strokeStyle = tr.fused ? C.route : "rgba(154,157,159,0.8)"; ctx.lineWidth = tr.fused ? 2.5 : 1.5;
      ctx.beginPath(); ctx.arc(px, py, 13, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      if (tr.fused && named < 3) { labels.push([px, py + 26, `osoba · ${fmt(tr.d, 1)} m`, C.route, 2]); named++; }
    }
  }

  // najbliższa nowa przeszkoda: linia od robota + odległość
  if (sh.live && sh.ready && sh.nearest_m != null && sh.nearest_deg != null && sh.nearest_m < RMAX) {
    const a = (sh.nearest_deg * Math.PI) / 180;
    const [nx, ny] = P(sh.nearest_m * Math.cos(a), sh.nearest_m * Math.sin(a));
    ctx.strokeStyle = sh.active ? C.stop : "rgba(243,241,234,0.7)"; ctx.lineWidth = 2; ctx.setLineDash([5, 5]);
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(nx, ny); ctx.stroke(); ctx.setLineDash([]);
    labels.push([(cx + nx) / 2, (cy + ny) / 2, `${fmt(sh.nearest_m)} m`, sh.active ? C.stop : C.paint, 0]);
  }

  // dłoń widziana przez kamerę
  const h = s.cam && s.cam.live ? s.hand : null;
  if (h && h.map) {
    const [hx, hy] = P(h.map[0], h.map[1]);
    ctx.fillStyle = "rgba(108,180,255,0.25)";
    ctx.beginPath(); ctx.arc(hx, hy, 14, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = C.route;
    ctx.beginPath(); ctx.arc(hx, hy, 6, 0, Math.PI * 2); ctx.fill();
    labels.push([hx, hy - 24, "dłoń", C.route, 1]);
  }

  // robot w skali, koła kręcą się z prawdziwą prędkością
  const wh = s.wheels;
  const max = store.cfg.max_speed || 0.5;
  const estop = s.drive && s.drive.state === "ESTOP";
  const sp = { L: wh && !estop ? clamp(wh.L / max, -1, 1) : 0, R: wh && !estop ? clamp(wh.R / max, -1, 1) : 0 };
  const bl = 0.13, bw = 0.1, wl = 0.09, ww = 0.035;
  const [b0x, b0y] = P(-bl, -bw), [b1x, b1y] = P(bl, bw);
  for (const [side, Y] of [["R", -bw - ww * 0.6], ["L", bw + ww * 0.6]]) {
    tread[side] += sp[side] * dt * 120;
    const col = sp[side] > 0.02 ? C.go : sp[side] < -0.02 ? C.route : "rgba(243,241,234,0.25)";
    for (const X of [bl - wl / 2, -bl + wl / 2]) {
      const [x0, y0] = P(X - wl / 2, Y - ww / 2), [x1, y1] = P(X + wl / 2, Y + ww / 2);
      const x = Math.min(x0, x1), y = Math.min(y0, y1), w = Math.abs(x1 - x0), hh = Math.abs(y1 - y0);
      ctx.save();
      roundRect(ctx, x, y, w, hh, 3); ctx.fillStyle = "#141618"; ctx.fill(); ctx.clip();
      ctx.strokeStyle = col; ctx.lineWidth = 2;
      const step = 5, off = ((tread[side] % step) + step) % step;
      ctx.beginPath();
      for (let ty = y - step + off; ty < y + hh + step; ty += step) { ctx.moveTo(x, ty); ctx.lineTo(x + w, ty); }
      ctx.stroke();
      ctx.restore();
    }
  }
  roundRect(ctx, Math.min(b0x, b1x), Math.min(b0y, b1y), Math.abs(b1x - b0x), Math.abs(b1y - b0y), 5);
  ctx.fillStyle = "#24282c"; ctx.fill();
  ctx.strokeStyle = estop ? C.stop : "rgba(243,241,234,0.6)"; ctx.lineWidth = 1.5; ctx.stroke();
  ctx.strokeStyle = C.route; ctx.lineWidth = 3;
  ctx.beginPath(); ctx.moveTo(...P(bl, -bw * 0.65)); ctx.lineTo(...P(bl, bw * 0.65)); ctx.stroke();
  ctx.restore();

  // podpisy bez nakładania: najpierw stałe (pierścienie, PRZÓD), potem najbliższa przeszkoda, dłoń
  // i osoby od najbliższej. Podpis, który nie mieści się obok swojego obiektu, po prostu się nie pojawia.
  const placed = [];
  const put = (x, y, t, col, bg, font = "600 15px Barlow, sans-serif") => {
    ctx.font = font;
    const w = ctx.measureText(t).width + 16;
    x = clamp(x, w / 2 + 2, W - w / 2 - 2);
    for (const dy of [0, 26, -26, 52]) {
      const yy = clamp(y + dy, 14, H - 14);
      const b = [x - w / 2 - 4, yy - 15, x + w / 2 + 4, yy + 15];
      if (placed.some((q) => b[0] < q[2] && b[2] > q[0] && b[1] < q[3] && b[3] > q[1])) continue;
      placed.push(b);
      pill(ctx, x, yy, t, col, bg, font);
      return;
    }
  };
  // podziałka: na jednej osi w prawo, tuż za każdym pierścieniem, z kreską; bez "pigułek",
  // ciemna obwódka liter zamiast tła (czytelne także na punktach ściany)
  ctx.font = "600 14px Barlow, sans-serif"; ctx.textAlign = "left"; ctx.textBaseline = "middle"; ctx.lineJoin = "round";
  for (const r of [0.5, 1, 2, 3]) {
    const x = cx + comp(r) * sc, t = r < 1 ? "0,5" : `${r} m`;
    ctx.fillStyle = "rgba(243,241,234,0.45)";
    ctx.fillRect(x - 1, cy - 5, 2, 10);
    ctx.strokeStyle = "rgba(10,13,17,0.92)"; ctx.lineWidth = 4;
    ctx.strokeText(t, x + 5, cy - 11);
    ctx.fillStyle = r === 1 ? C.paint : C.concrete;
    ctx.fillText(t, x + 5, cy - 11);
    const w = ctx.measureText(t).width;
    placed.push([x - 2, cy - 21, x + w + 9, cy + 6]);           // podpisy obiektów omijają podziałkę
  }
  put(cx, Math.min(H - 14, cy + Rpx + 2), "PRZÓD", C.route, "rgba(8,9,10,0.85)", "600 13px 'Barlow Condensed', Barlow, sans-serif");
  labels.sort((a, b) => a[4] - b[4]);
  for (const [x, y, t, col] of labels) put(x, y, t, col);
}

/** Odczyty pod radarem. */
export function updateRadarPanel(s) {
  const sh = (s && s.shield) || {};
  const set = (id, v) => { const n = $(id); if (n && n.textContent !== v) n.textContent = v; };
  set("rdNear", sh.live && sh.nearest_m != null ? `${fmt(sh.nearest_m)} m` : "brak");
  set("rdPeople", s && s.people_count != null ? String(s.people_count) : "0");
  const v = wheelSpeed(s);                                   // to samo co w napisie "Jadę …%"
  set("rdSpeed", Math.abs(v) < 0.03 ? "stoi" : `${Math.max(10, pct10(v))}% ${v > 0 ? "przód" : "tył"}`);
}
