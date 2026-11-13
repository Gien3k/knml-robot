import { fit, fmt, roundRect } from "../canvas";
import type { Config, Scan, State } from "../types";

const DEG = Math.PI / 180;
const C = {
  paint: "#f3f1ea", concrete: "#9a9d9f", gravel: "#64686b", line: "#2b2f33",
  route: "#6cb4ff", caution: "#f2b33d", stop: "#e5484d",
};

/**
 * Radar LiDAR 360° w układzie robota (X – przód, Y – lewo).
 * Widok od strony widza: przód robota skierowany w DÓŁ ekranu – do odwiedzających.
 * Tło stoiska rysowane jako kontur (połączone sąsiednie echa), obiekty ruchome jako
 * jasne punkty, ludzie z krótkim śladem ruchu, zamiar ruchu robota kolorem trasy.
 */
export class Radar {
  private scan: { x: Float32Array; y: Float32Array; ok: Uint8Array; fg: Uint8Array } | null = null;
  private trails = new Map<number, { x: number; y: number; t: number }[]>();
  private hatch: CanvasPattern | null = null;
  state: State | null = null;
  range = 3.5;

  constructor(private canvas: HTMLCanvasElement, private cfg: () => Config) {}

  pushScan(s: Scan): void {
    const n = s.r.length;
    const x = new Float32Array(n), y = new Float32Array(n), ok = new Uint8Array(n), fg = new Uint8Array(n);
    for (let i = 0; i < n; i++) {
      const r = s.r[i] / 1000;
      x[i] = r * Math.cos(i * DEG);
      y[i] = r * Math.sin(i * DEG);
      ok[i] = r > 0 ? 1 : 0;
    }
    for (const i of s.fg) fg[i] = 1;
    this.scan = { x, y, ok, fg };
  }

  setState(st: State): void {
    this.state = st;
    const now = performance.now() / 1000;
    const seen = new Set<number>();
    for (const t of st.lidar.tracks ?? []) {
      seen.add(t.id);
      const arr = this.trails.get(t.id) ?? [];
      arr.push({ x: t.x, y: t.y, t: now });
      while (arr.length && now - arr[0].t > 2.5) arr.shift();
      this.trails.set(t.id, arr);
    }
    for (const [id, arr] of this.trails) {
      if (!seen.has(id) && (!arr.length || now - arr[arr.length - 1].t > 1)) this.trails.delete(id);
    }
  }

  private hatchPattern(ctx: CanvasRenderingContext2D): CanvasPattern | null {
    if (!this.hatch) {
      const c = document.createElement("canvas");
      c.width = c.height = 10;
      const g = c.getContext("2d")!;
      g.strokeStyle = "rgba(242,179,61,0.75)";
      g.lineWidth = 2.2;
      g.beginPath(); g.moveTo(-2, 12); g.lineTo(12, -2); g.moveTo(-2, 2); g.lineTo(2, -2); g.moveTo(8, 12); g.lineTo(12, 8); g.stroke();
      this.hatch = ctx.createPattern(c, "repeat");
    }
    return this.hatch;
  }

  draw(audience: boolean): void {
    const { ctx, w, h } = fit(this.canvas);
    ctx.clearRect(0, 0, w, h);
    const cfg = this.cfg();
    const st = this.state;
    const cx = w / 2, cy = h / 2;
    const R = Math.min(w, h) / 2 - 12;
    const k = R / this.range;
    const T = (X: number, Y: number): [number, number] => audience ? [cx + Y * k, cy + X * k] : [cx - Y * k, cy - X * k];
    const ang = (a: number) => (audience ? Math.PI / 2 : -Math.PI / 2) - a;

    // --- skala ---
    ctx.lineWidth = 1;
    ctx.strokeStyle = C.line;
    for (const r of [1, 2, 3]) { ctx.beginPath(); ctx.arc(cx, cy, r * k, 0, Math.PI * 2); ctx.stroke(); }
    for (let a = 0; a < 360; a += 15) {
      const len = a % 90 === 0 ? 12 : a % 45 === 0 ? 8 : 5;
      const ca = Math.cos(a * DEG), sa = Math.sin(a * DEG);
      ctx.beginPath(); ctx.moveTo(cx + ca * R, cy + sa * R); ctx.lineTo(cx + ca * (R - len), cy + sa * (R - len)); ctx.stroke();
    }
    ctx.fillStyle = C.gravel;
    ctx.font = "500 13px Barlow, system-ui";
    for (const r of [1, 2, 3]) ctx.fillText(`${r} m`, cx + 6, cy - r * k - 6);

    ctx.save();
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, Math.PI * 2); ctx.clip();

    // --- pole widzenia kamery ---
    const hf = (cfg.oak_hfov / 2) * DEG;
    const [ox, oy] = T(cfg.half_length, 0);
    ctx.beginPath();
    ctx.moveTo(ox, oy);
    ctx.arc(ox, oy, R * 1.5, ang(hf), ang(-hf), false);
    ctx.closePath();
    ctx.fillStyle = "rgba(108,180,255,0.045)";
    ctx.fill();
    ctx.strokeStyle = "rgba(108,180,255,0.28)";
    ctx.stroke();

    // --- kontur stoiska i obiekty ruchome ---
    const sc = this.scan;
    if (sc) {
      const n = sc.x.length;
      ctx.strokeStyle = "rgba(243,241,234,0.34)";
      ctx.lineWidth = 1.6;
      ctx.lineJoin = "round";
      ctx.beginPath();
      for (let i = 0; i < n; i++) {
        const j = (i + 1) % n;
        if (!sc.ok[i] || sc.fg[i] || !sc.ok[j] || sc.fg[j]) continue;
        if (Math.hypot(sc.x[i] - sc.x[j], sc.y[i] - sc.y[j]) > 0.25) continue;
        const [x1, y1] = T(sc.x[i], sc.y[i]), [x2, y2] = T(sc.x[j], sc.y[j]);
        ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
      }
      ctx.stroke();
      ctx.fillStyle = "rgba(243,241,234,0.3)";
      for (let i = 0; i < n; i++) {
        if (!sc.ok[i] || sc.fg[i]) continue;
        const [px, py] = T(sc.x[i], sc.y[i]);
        ctx.fillRect(px - 0.9, py - 0.9, 1.8, 1.8);
      }
      ctx.fillStyle = C.paint;
      for (let i = 0; i < n; i++) {
        if (!sc.ok[i] || !sc.fg[i]) continue;
        const [px, py] = T(sc.x[i], sc.y[i]);
        ctx.beginPath(); ctx.arc(px, py, 2, 0, Math.PI * 2); ctx.fill();
      }
    }

    // --- strefy bezpieczeństwa ---
    const hl = cfg.half_length, hw = cfg.half_width;
    const sz = st?.lidar.safety;
    const sm = sz?.safety_m ?? cfg.safety_m, em = sz?.estop_m ?? cfg.estop_m;
    const zone = (d: number) => {
      const [x1, y1] = T(hl + d, hw + d), [x2, y2] = T(-hl - d, -hw - d);
      roundRect(ctx, Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1), d * k);
    };
    const band = (X1: number, X2: number, Y1: number, Y2: number) => {
      const a = T(X1, Y1), b = T(X2, Y2);
      ctx.fillRect(Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.abs(b[0] - a[0]), Math.abs(b[1] - a[1]));
    };
    zone(sm);
    ctx.fillStyle = "rgba(243,241,234,0.035)";
    ctx.fill();
    if (sz?.blocked && sz.state !== "ESTOP") {
      ctx.save();
      zone(sm); ctx.clip();
      ctx.fillStyle = this.hatchPattern(ctx) ?? "rgba(242,179,61,0.3)";
      if (sz.blocked.front) band(hl, hl + sm, -hw - sm, hw + sm);
      if (sz.blocked.rear) band(-hl, -hl - sm, -hw - sm, hw + sm);
      if (sz.blocked.left) band(-hl - sm, hl + sm, hw, hw + sm);
      if (sz.blocked.right) band(-hl - sm, hl + sm, -hw, -hw - sm);
      ctx.restore();
    }
    zone(sm);
    ctx.setLineDash([4, 4]);
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = sz?.state === "WARN" ? C.caution : "rgba(154,157,159,0.7)";
    ctx.stroke();
    ctx.setLineDash([]);
    if (sz?.state === "ESTOP") {
      zone(em);
      ctx.fillStyle = "rgba(229,72,77,0.4)";
      ctx.fill();
      zone(sm);
      ctx.strokeStyle = C.stop;
      ctx.lineWidth = 2.5;
      ctx.stroke();
    }

    // --- wirtualna klatka (tylko tryb maty) ---
    const ad = st?.adas;
    const pose = st?.lidar.pose;
    if (ad?.geofence?.active && pose) {
      const c = Math.cos(-pose.th), s = Math.sin(-pose.th);
      const [px, py] = T(c * -pose.x - s * -pose.y, s * -pose.x + c * -pose.y);
      ctx.strokeStyle = ad.geofence.breach ? C.caution : C.route;
      ctx.setLineDash([3, 4]);
      ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(px, py, ad.geofence.radius * k, 0, Math.PI * 2); ctx.stroke();
      ctx.setLineDash([]);
    }

    // --- ślady ruchu ludzi ---
    const now = performance.now() / 1000;
    ctx.lineCap = "round";
    for (const arr of this.trails.values()) {
      for (let i = 1; i < arr.length; i++) {
        const a = Math.max(0, 1 - (now - arr[i].t) / 2.5);
        const [x1, y1] = T(arr[i - 1].x, arr[i - 1].y), [x2, y2] = T(arr[i].x, arr[i].y);
        ctx.strokeStyle = `rgba(243,241,234,${0.32 * a})`;
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
      }
    }

    // --- planowany ruch robota (zamiar z gestu, po filtrach bezpieczeństwa) ---
    const v = ad?.intent.v ?? 0, om = ad?.intent.w ?? 0;
    if (Math.abs(v) > 0.01 || Math.abs(om) > 0.05) {
      let X = 0, Y = 0, th = 0;
      const pts: [number, number][] = [T(0, 0)];
      const scale = 2.2 / Math.max(0.05, cfg.max_speed);   // wydłużenie dla czytelności
      for (let i = 0; i < 40; i++) {
        const d = 1.2 / 40;
        X += v * scale * Math.cos(th) * d; Y += v * scale * Math.sin(th) * d; th += om * d * 1.4;
        pts.push(T(X, Y));
      }
      ctx.strokeStyle = C.route;
      ctx.lineWidth = 4;
      ctx.beginPath();
      pts.forEach(([px, py], i) => (i ? ctx.lineTo(px, py) : ctx.moveTo(px, py)));
      ctx.stroke();
      const [ex, ey] = pts[pts.length - 1];
      ctx.fillStyle = C.route;
      ctx.beginPath(); ctx.arc(ex, ey, 5, 0, Math.PI * 2); ctx.fill();
    }

    // --- ludzie ---
    const fused = ad?.fused ?? {};
    const labels: number[][] = [];
    ctx.font = "500 15px Barlow, system-ui";
    for (const t of st?.lidar.tracks ?? []) {
      if (t.w < 0.12) continue;          // wąskie echo to ręka, nie sylwetka
      const [px, py] = T(t.x, t.y);
      const r = Math.max(0.17, t.w / 2) * k;
      const isF = String(t.id) in fused;
      ctx.beginPath(); ctx.arc(px, py, r, 0, Math.PI * 2);
      ctx.fillStyle = isF ? "rgba(108,180,255,0.22)" : "rgba(243,241,234,0.08)";
      ctx.fill();
      ctx.strokeStyle = isF ? C.route : C.paint;
      ctx.lineWidth = 1.8;
      ctx.stroke();
      const txt = `${fmt(t.d, 1)} m`;
      const tw = ctx.measureText(txt).width;
      let lx = px + r + 7, ly = py + 5;
      for (let n = 0; n < 6 && labels.some(([x, y, w2, h2]) => lx < x + w2 && lx + tw > x && ly - 13 < y + h2 && ly > y); n++) ly += 16;
      labels.push([lx, ly - 13, tw, 16]);
      ctx.fillStyle = C.paint;
      ctx.fillText(txt, lx, ly);
    }

    // --- dłoń z kamery (3D) ---
    const hand = st?.oak.hand;
    if (hand) {
      const z = hand.z, xc = hand.offset * z * Math.tan(hf);
      const [px, py] = T(z + 0.1, -xc);
      ctx.fillStyle = C.route;
      ctx.strokeStyle = "#16181a";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(px, py - 7); ctx.lineTo(px + 7, py); ctx.lineTo(px, py + 7); ctx.lineTo(px - 7, py);
      ctx.closePath(); ctx.fill(); ctx.stroke();
    }

    // --- robot (obrys w skali, koła) ---
    const [ax, ay] = T(hl, hw), [bx, by] = T(-hl, -hw);
    const rx = Math.min(ax, bx), ry = Math.min(ay, by), rw = Math.abs(bx - ax), rh = Math.abs(by - ay);
    ctx.fillStyle = "#0f1012";
    for (const [X, Y] of [[hl - 0.045, hw], [-hl + 0.045, hw], [hl - 0.045, -hw], [-hl + 0.045, -hw]]) {
      const [wx, wy] = T(X, Y);
      const ww = 0.028 * k, wh = 0.07 * k;
      ctx.fillRect(wx - ww / 2, wy - wh / 2, ww, wh);
    }
    roundRect(ctx, rx, ry, rw, rh, 3);
    ctx.fillStyle = "#2b2f33"; ctx.fill();
    ctx.strokeStyle = C.paint; ctx.lineWidth = 1.5; ctx.stroke();
    const [fx, fy] = T(hl - 0.01, 0), [lx2, ly2] = T(hl - 0.07, 0.045), [rx2, ry2] = T(hl - 0.07, -0.045);
    ctx.fillStyle = C.paint;
    ctx.beginPath(); ctx.moveTo(fx, fy); ctx.lineTo(lx2, ly2); ctx.lineTo(rx2, ry2); ctx.closePath(); ctx.fill();

    ctx.restore();
  }
}
