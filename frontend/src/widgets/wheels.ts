import { fit, roundRect } from "../canvas";
import type { Config, State } from "../types";

type WK = "fl" | "rl" | "fr" | "rr";
const WHEELS: { k: WK; X: number; Y: number }[] = [
  { k: "fl", X: 1, Y: 1 }, { k: "rl", X: -1, Y: 1 }, { k: "fr", X: 1, Y: -1 }, { k: "rr", X: -1, Y: -1 },
];

/** Widok z góry na podwozie 4WD: animowany bieżnik każdego koła, kierunek, obroty i łuk toru jazdy. */
export class WheelsViz {
  private phase: Record<WK, number> = { fl: 0, rl: 0, fr: 0, rr: 0 };

  constructor(private canvas: HTMLCanvasElement, private cfg: () => Config) {}

  draw(st: State | null, audience: boolean, dt: number): void {
    const { ctx, w, h } = fit(this.canvas);
    ctx.clearRect(0, 0, w, h);
    const cfg = this.cfg();
    const cx = w / 2, cy = h / 2 + 4;
    const fdir = audience ? 1 : -1;          // kierunek "przód" na ekranie (1 = w dół)
    const side = audience ? 1 : -1;          // lewa strona robota: prawo ekranu w widoku widza
    const bodyW = 78, bodyH = 112, wheelW = 22, wheelH = 48;
    const circ = Math.PI * cfg.wheel_diameter;

    // łuk toru jazdy (0,6 s)
    const L = st?.drive.wheels.fl.v ?? 0, R = st?.drive.wheels.fr.v ?? 0;
    const v = (L + R) / 2, om = (R - L) / cfg.track_width;
    const kpx = 260;
    if (Math.abs(v) > 0.01 || Math.abs(om) > 0.05) {
      let X = 0, Y = 0, th = 0;
      ctx.strokeStyle = "rgba(57,135,229,0.85)";
      ctx.lineWidth = 2;
      ctx.setLineDash([5, 5]);
      ctx.beginPath();
      ctx.moveTo(cx, cy);
      for (let i = 0; i < 30; i++) {
        const d = 0.6 / 30;
        X += v * Math.cos(th) * d; Y += v * Math.sin(th) * d; th += om * d;
        ctx.lineTo(cx + side * Y * kpx, cy + fdir * X * kpx);
      }
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // nadwozie
    roundRect(ctx, cx - bodyW / 2, cy - bodyH / 2, bodyW, bodyH, 12);
    ctx.fillStyle = "#2a2a28"; ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,0.18)"; ctx.lineWidth = 1.5; ctx.stroke();
    // znacznik przodu
    const fy = cy + fdir * (bodyH / 2 - 12);
    ctx.fillStyle = "#3987e5";
    ctx.beginPath();
    ctx.moveTo(cx, fy + fdir * 8); ctx.lineTo(cx - 9, fy - fdir * 4); ctx.lineTo(cx + 9, fy - fdir * 4);
    ctx.closePath(); ctx.fill();
    ctx.fillStyle = "#898781";
    ctx.font = "600 11px Inter Variable, system-ui";
    ctx.textAlign = "center";
    ctx.fillText(audience ? "PRZÓD ▼" : "PRZÓD ▲", cx, cy + fdir * (bodyH / 2 + 14) + 4);
    const colX = bodyW / 2 + wheelW / 2 + 6;
    ctx.fillText("LEWE", cx + side * colX, cy - bodyH / 2 - 14);
    ctx.fillText("PRAWE", cx - side * colX, cy - bodyH / 2 - 14);

    // koła
    for (const wh of WHEELS) {
      const data = st?.drive.wheels[wh.k] ?? { v: 0, rpm: 0 };
      this.phase[wh.k] = (this.phase[wh.k] + (data.v / circ) * dt) % 1;
      const x = cx + side * wh.Y * (bodyW / 2 + wheelW / 2 + 6) - wheelW / 2;
      const y = cy + fdir * wh.X * (bodyH / 2 - wheelH / 2 - 2) - wheelH / 2;
      const moving = Math.abs(data.v) > 0.005;
      const col = !moving ? "#5c5b56" : data.v > 0 ? "#3987e5" : "#d95926";
      ctx.save();
      roundRect(ctx, x, y, wheelW, wheelH, 6);
      ctx.fillStyle = "#151514"; ctx.fill();
      ctx.clip();
      // bieżnik – paski przesuwają się w kierunku ruchu powierzchni koła
      const sp = 8;
      const off = (((this.phase[wh.k] * sp * 6) % sp) + sp) % sp;
      ctx.strokeStyle = col;
      ctx.lineWidth = 3;
      for (let yy = -sp; yy < wheelH + sp; yy += sp) {
        const py = y + (fdir > 0 ? yy + off : yy - off);
        ctx.beginPath(); ctx.moveTo(x + 3, py); ctx.lineTo(x + wheelW - 3, py); ctx.stroke();
      }
      ctx.restore();
      roundRect(ctx, x, y, wheelW, wheelH, 6);
      ctx.strokeStyle = moving ? col : "rgba(255,255,255,0.15)"; ctx.lineWidth = 1.5; ctx.stroke();

      // strzałka kierunku + obroty
      const outer = x + wheelW / 2 + side * wh.Y * (wheelW / 2 + 22);
      const ay = y + wheelH / 2;
      if (moving) {
        const d = fdir * Math.sign(data.v);
        ctx.fillStyle = col;
        ctx.beginPath();
        ctx.moveTo(outer, ay + d * 9); ctx.lineTo(outer - 7, ay - d * 5); ctx.lineTo(outer + 7, ay - d * 5);
        ctx.closePath(); ctx.fill();
      }
      ctx.fillStyle = "#f0efec";
      ctx.font = "600 12px JetBrains Mono, monospace";
      ctx.fillText(`${Math.round(Math.abs(data.rpm))}`, outer, ay + 26);
      ctx.fillStyle = "#898781";
      ctx.font = "500 10px Inter Variable, system-ui";
      ctx.fillText("obr/min", outer, ay + 38);
    }
    ctx.textAlign = "left";
  }
}
