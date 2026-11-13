import { fit, fmt, roundRect } from "../canvas";
import type { Config, Scan, State } from "../types";

const DEG = Math.PI / 180;

/**
 * Radar 360° w układzie robota (X – przód, Y – lewo).
 * Widok "od strony widza": przód robota skierowany w DÓŁ ekranu (do odwiedzających),
 * więc krok widza w prawo przesuwa go w prawo na radarze – tak samo jak w lustrzanym wideo.
 */
export class Radar {
  private scans: { x: Float32Array; y: Float32Array; fg: Uint8Array; n: number }[] = [];
  state: State | null = null;
  private sweep = 0;

  constructor(private canvas: HTMLCanvasElement, private cfg: () => Config) {}

  pushScan(s: Scan): void {
    const n = s.r.length;
    const x = new Float32Array(n), y = new Float32Array(n), fg = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
      const r = s.r[i] / 1000;
      x[i] = r * Math.cos(i * DEG);
      y[i] = r * Math.sin(i * DEG);
    }
    for (const i of s.fg) fg[i] = 1;
    this.scans.push({ x, y, fg, n });
    if (this.scans.length > 4) this.scans.shift();
  }

  draw(audience: boolean, dt: number): void {
    const { ctx, w, h } = fit(this.canvas);
    ctx.clearRect(0, 0, w, h);
    const cfg = this.cfg();
    const st = this.state;
    const range = 4.0;
    const cx = w / 2, cy = h / 2 - 8;
    const k = (Math.min(w, h) / 2 - 30) / range;
    const T = (X: number, Y: number): [number, number] =>
      audience ? [cx + Y * k, cy + X * k] : [cx - Y * k, cy - X * k];
    const ang0 = audience ? Math.PI / 2 : -Math.PI / 2; // kąt ekranu kierunku "przód"
    const screenAngle = (a: number) => ang0 - a; // a: CCW w układzie robota -> kąt canvasa

    // --- siatka ---
    ctx.lineWidth = 1;
    for (const r of [0.5, 1, 2, 3, 4]) {
      ctx.strokeStyle = r === 4 ? "#383835" : "#2c2c2a";
      ctx.beginPath(); ctx.arc(cx, cy, r * k, 0, Math.PI * 2); ctx.stroke();
    }
    ctx.strokeStyle = "#242422";
    for (let a = 0; a < 360; a += 30) {
      const [x, y] = T(range * Math.cos(a * DEG), range * Math.sin(a * DEG));
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(x, y); ctx.stroke();
    }
    ctx.fillStyle = "#898781";
    ctx.font = "500 11px JetBrains Mono, monospace";
    for (const r of [1, 2, 3, 4]) {
      const [x, y] = T(r * Math.cos(-45 * DEG), r * Math.sin(-45 * DEG));
      ctx.fillText(`${r} m`, x + 4, y - 4);
    }

    // --- omiatanie (efekt radaru) ---
    this.sweep = (this.sweep + dt * 1.6) % (Math.PI * 2);
    const g = ctx.createConicGradient(this.sweep, cx, cy);
    g.addColorStop(0, "rgba(57,135,229,0.10)");
    g.addColorStop(0.08, "rgba(57,135,229,0.0)");
    g.addColorStop(1, "rgba(57,135,229,0.0)");
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(cx, cy, range * k, 0, Math.PI * 2); ctx.fill();

    // --- pole widzenia OAK-D i kierunek kamery FPV ---
    const hf = (cfg.oak_hfov / 2) * DEG;
    ctx.fillStyle = "rgba(57,135,229,0.07)";
    ctx.strokeStyle = "rgba(57,135,229,0.35)";
    ctx.beginPath();
    const [ox, oy] = T(cfg.half_length, 0);
    ctx.moveTo(ox, oy);
    ctx.arc(ox, oy, range * k, screenAngle(hf), screenAngle(-hf), false);
    ctx.closePath(); ctx.fill(); ctx.stroke();
    const pan = (st?.drive.gimbal?.pan ?? 0) * DEG;
    const [gx, gy] = T(1.2 * Math.cos(pan), 1.2 * Math.sin(pan));
    ctx.strokeStyle = "rgba(240,239,236,0.35)";
    ctx.setLineDash([3, 4]);
    ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(gx, gy); ctx.stroke();
    ctx.setLineDash([]);

    // --- punkty skanu (poświata starszych obrotów) ---
    this.scans.forEach((s, si) => {
      const age = this.scans.length - 1 - si;
      const a = age === 0 ? 1 : 0.45 / age;
      for (let i = 0; i < s.n; i++) {
        if ((s.x[i] === 0 && s.y[i] === 0) || s.x[i] * s.x[i] + s.y[i] * s.y[i] > range * range) continue;
        const [px, py] = T(s.x[i], s.y[i]);
        if (s.fg[i]) {
          ctx.fillStyle = `rgba(240,239,236,${a})`;
          ctx.fillRect(px - 1.75, py - 1.75, 3.5, 3.5);
        } else {
          ctx.fillStyle = `rgba(137,135,129,${a * 0.8})`;
          ctx.fillRect(px - 1, py - 1, 2, 2);
        }
      }
    });

    // --- strefy bezpieczeństwa wokół obrysu ---
    const hl = cfg.half_length, hw = cfg.half_width;
    const sz = st?.lidar.safety;
    const sm = sz?.safety_m ?? cfg.safety_m, em = sz?.estop_m ?? cfg.estop_m;
    const zoneRect = (d: number) => {
      const [x1, y1] = T(hl + d, hw + d), [x2, y2] = T(-hl - d, -hw - d);
      roundRect(ctx, Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1), d * k);
    };
    const band = (X1: number, X2: number, Y1: number, Y2: number) => {
      const a = T(X1, Y1), b = T(X2, Y2);
      ctx.fillRect(Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1]));
    };
    if (sz?.blocked) {
      ctx.fillStyle = "rgba(236,131,90,0.28)";
      if (sz.blocked.front) band(hl, hl + sm, -hw - sm, hw + sm);
      if (sz.blocked.rear) band(-hl, -hl - sm, -hw - sm, hw + sm);
      if (sz.blocked.left) band(-hl, hl, hw, hw + sm);
      if (sz.blocked.right) band(-hl, hl, -hw, -hw - sm);
    }
    const estop = sz?.state === "ESTOP";
    zoneRect(sm);
    ctx.setLineDash([5, 4]);
    ctx.strokeStyle = sz?.state === "WARN" || estop ? "#ec835a" : "rgba(195,194,183,0.55)";
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.setLineDash([]);
    zoneRect(em);
    if (estop) {
      ctx.fillStyle = "rgba(208,59,59,0.35)";
      ctx.fill();
    }
    ctx.strokeStyle = estop ? "#d03b3b" : "rgba(208,59,59,0.55)";
    ctx.lineWidth = estop ? 2.5 : 1;
    ctx.stroke();

    // --- wirtualna klatka (tryb mat) ---
    const ad = st?.adas;
    const pose = st?.lidar.pose;
    if (ad?.geofence?.active && pose) {
      const c = Math.cos(-pose.th), s = Math.sin(-pose.th);
      const sx = c * -pose.x - s * -pose.y, sy = s * -pose.x + c * -pose.y;
      const [px, py] = T(sx, sy);
      ctx.strokeStyle = ad.geofence.breach ? "#ec835a" : "rgba(57,135,229,0.8)";
      ctx.setLineDash([2, 4]);
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(px, py, ad.geofence.radius * k, 0, Math.PI * 2); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "rgba(57,135,229,0.9)";
      ctx.beginPath(); ctx.arc(px, py, 3, 0, Math.PI * 2); ctx.fill();
    }

    // --- osoby (tory LiDAR, fuzja z OAK) ---
    const fused = ad?.fused ?? {};
    ctx.font = "600 12px Inter Variable, system-ui";
    const labels: number[][] = [];
    for (const t of st?.lidar.tracks ?? []) {
      const [px, py] = T(t.x, t.y);
      const r = Math.max(0.14, t.w / 2) * k;
      const isF = String(t.id) in fused;
      if (Math.hypot(t.vx, t.vy) > 0.15) {
        const [vx, vy] = T(t.x + t.vx * 0.8, t.y + t.vy * 0.8);
        ctx.strokeStyle = "rgba(240,239,236,0.5)";
        ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(vx, vy); ctx.stroke();
      }
      ctx.beginPath(); ctx.arc(px, py, r, 0, Math.PI * 2);
      ctx.fillStyle = isF ? "rgba(57,135,229,0.30)" : "rgba(26,26,25,0.6)";
      ctx.fill();
      ctx.strokeStyle = isF ? "#3987e5" : "#c3c2b7";
      ctx.lineWidth = 2;
      ctx.stroke();
      if (ad?.target === t.id) {
        ctx.strokeStyle = "rgba(240,239,236,0.8)";
        ctx.lineWidth = 1;
        ctx.beginPath(); ctx.arc(px, py, r + 5, 0, Math.PI * 2); ctx.stroke();
      }
      const txt = `#${t.id} · ${fmt(t.d, 1)} m`;
      const tw = ctx.measureText(txt).width;
      let ly = py + 4;
      for (let n = 0; n < 8 && labels.some(([x, y, w2, h2]) => px + r + 5 < x + w2 && px + r + 5 + tw > x && ly - 12 < y + h2 && ly > y); n++) ly += 14;
      labels.push([px + r + 5, ly - 12, tw, 14]);
      ctx.fillStyle = "#f0efec";
      ctx.fillText(txt, px + r + 5, ly);
    }

    // --- dłoń (OAK, 3D) ---
    const hand = st?.oak.hand;
    if (hand) {
      const z = hand.z, xc = hand.offset * z * Math.tan(hf);
      const [px, py] = T(z + 0.1, -xc);
      ctx.fillStyle = hand.gesture === "OPEN_PALM" ? "#0ca30c" : hand.gesture === "FIST" ? "#d03b3b" : "#fab219";
      ctx.beginPath();
      ctx.moveTo(px, py - 8); ctx.lineTo(px + 8, py); ctx.lineTo(px, py + 8); ctx.lineTo(px - 8, py);
      ctx.closePath(); ctx.fill();
      ctx.strokeStyle = "#0d0d0d"; ctx.lineWidth = 2; ctx.stroke();
    }

    // --- robot ---
    const [ax, ay] = T(hl, hw), [bx, by] = T(-hl, -hw);
    roundRect(ctx, Math.min(ax, bx), Math.min(ay, by), Math.abs(bx - ax), Math.abs(by - ay), 4);
    ctx.fillStyle = "#2a2a28"; ctx.fill();
    ctx.strokeStyle = "#f0efec"; ctx.lineWidth = 1.5; ctx.stroke();
    const [fx, fy] = T(hl + 0.05, 0), [lx, ly] = T(hl - 0.04, 0.06), [rx, ry] = T(hl - 0.04, -0.06);
    ctx.fillStyle = "#3987e5";
    ctx.beginPath(); ctx.moveTo(fx, fy); ctx.lineTo(lx, ly); ctx.lineTo(rx, ry); ctx.closePath(); ctx.fill();

    // --- wstawka: wirtualna klatka w powiększeniu ---
    if (ad?.geofence?.active && pose) this.drawCage(ctx, w - 168, 30, 152, ad.geofence.radius, pose, ad.geofence.breach, audience, cfg);

    // --- legenda + orientacja ---
    ctx.font = "500 12px Inter Variable, system-ui";
    const items: [string, (x: number, y: number) => void][] = [
      ["tło", (x, y) => { ctx.fillStyle = "#898781"; ctx.fillRect(x - 1.5, y - 1.5, 3, 3); }],
      ["ruch", (x, y) => { ctx.fillStyle = "#f0efec"; ctx.fillRect(x - 2, y - 2, 4, 4); }],
      ["osoba LiDAR", (x, y) => { ctx.strokeStyle = "#c3c2b7"; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, 5, 0, 7); ctx.stroke(); }],
      ["+ OAK", (x, y) => { ctx.strokeStyle = "#3987e5"; ctx.lineWidth = 2; ctx.fillStyle = "rgba(57,135,229,.3)"; ctx.beginPath(); ctx.arc(x, y, 5, 0, 7); ctx.fill(); ctx.stroke(); }],
      ["dłoń", (x, y) => { ctx.fillStyle = "#0ca30c"; ctx.beginPath(); ctx.moveTo(x, y - 5); ctx.lineTo(x + 5, y); ctx.lineTo(x, y + 5); ctx.lineTo(x - 5, y); ctx.fill(); }],
      [`strefa ${Math.round(sm * 100)} cm`, (x, y) => { ctx.strokeStyle = "#c3c2b7"; ctx.setLineDash([3, 3]); ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(x - 6, y); ctx.lineTo(x + 6, y); ctx.stroke(); ctx.setLineDash([]); }],
    ];
    let lx2 = 12;
    const ly2 = h - 10;
    for (const [label, mark] of items) {
      mark(lx2 + 5, ly2 - 4);
      ctx.fillStyle = "#c3c2b7";
      ctx.fillText(label, lx2 + 14, ly2);
      lx2 += 24 + ctx.measureText(label).width;
    }
    ctx.fillStyle = "#898781";
    ctx.fillText(audience ? "widok od strony widza · przód robota ↓" : "widok od strony robota · przód ↑", 12, 18);
  }

  private drawCage(ctx: CanvasRenderingContext2D, x0: number, y0: number, size: number, R: number,
                   pose: { x: number; y: number; th: number }, breach: boolean, audience: boolean, cfg: Config): void {
    roundRect(ctx, x0, y0, size, size + 22, 10);
    ctx.fillStyle = "rgba(13,13,13,0.85)"; ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.12)"; ctx.lineWidth = 1; ctx.stroke();
    const cx = x0 + size / 2, cy = y0 + 22 + size / 2 - 4;
    const kz = (size / 2 - 14) / (R * 1.25);
    const S = (X: number, Y: number): [number, number] => audience ? [cx + Y * kz, cy + X * kz] : [cx - Y * kz, cy - X * kz];
    ctx.fillStyle = "#c3c2b7";
    ctx.font = "600 11px Inter Variable, system-ui";
    ctx.fillText(`KLATKA · ${Math.round(R * 100)} cm`, x0 + 10, y0 + 16);
    ctx.strokeStyle = breach ? "#ec835a" : "#3987e5";
    ctx.lineWidth = 2;
    ctx.setLineDash([4, 4]);
    ctx.beginPath(); ctx.arc(cx, cy, R * kz, 0, Math.PI * 2); ctx.stroke();
    ctx.setLineDash([]);
    ctx.strokeStyle = "#2c2c2a"; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(cx, cy, 0.75 * R * kz, 0, Math.PI * 2); ctx.stroke();
    ctx.fillStyle = "#898781";
    ctx.beginPath(); ctx.arc(cx, cy, 2.5, 0, Math.PI * 2); ctx.fill();
    // robot (obrys obrócony o kąt z ICP)
    const c = Math.cos(pose.th), s = Math.sin(pose.th);
    const P = (a: number, b: number) => S(pose.x + c * a - s * b, pose.y + s * a + c * b);
    const hl = cfg.half_length, hw = cfg.half_width;
    const pts = [P(hl, hw), P(hl, -hw), P(-hl, -hw), P(-hl, hw)];
    ctx.beginPath(); pts.forEach(([px, py], i) => (i ? ctx.lineTo(px, py) : ctx.moveTo(px, py))); ctx.closePath();
    ctx.fillStyle = "rgba(42,42,40,0.9)"; ctx.fill();
    ctx.strokeStyle = "#f0efec"; ctx.lineWidth = 1.5; ctx.stroke();
    const [fx, fy] = P(hl + 0.03, 0), [ox, oy] = P(0, 0);
    ctx.strokeStyle = "#3987e5"; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(ox, oy); ctx.lineTo(fx, fy); ctx.stroke();
  }
}
