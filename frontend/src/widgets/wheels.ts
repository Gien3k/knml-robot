import { fit, roundRect } from "../canvas";
import type { Config, State } from "../types";

type WK = "fl" | "rl" | "fr" | "rr";
const WHEELS: { k: WK; X: number; Y: number }[] = [
  { k: "fl", X: 1, Y: 1 }, { k: "rl", X: -1, Y: 1 }, { k: "fr", X: 1, Y: -1 }, { k: "rr", X: -1, Y: -1 },
];

/** Podwozie z góry: bieżnik każdego z 4 kół przesuwa się z prędkością i w kierunku obrotu koła. */
export class WheelsViz {
  private phase: Record<WK, number> = { fl: 0, rl: 0, fr: 0, rr: 0 };

  constructor(private canvas: HTMLCanvasElement, private cfg: () => Config) {}

  draw(st: State | null, audience: boolean, dt: number): void {
    const { ctx, w, h } = fit(this.canvas);
    ctx.clearRect(0, 0, w, h);
    const cfg = this.cfg();
    const fdir = audience ? 1 : -1;
    const side = audience ? 1 : -1;
    const cx = w / 2, cy = h / 2 - 8;
    const bodyW = 66, bodyH = 100, wheelW = 18, wheelH = 42;
    const circ = Math.PI * cfg.wheel_diameter;

    roundRect(ctx, cx - bodyW / 2, cy - bodyH / 2, bodyW, bodyH, 10);
    ctx.fillStyle = "#1d2023"; ctx.fill();
    ctx.strokeStyle = "rgba(243,241,234,0.45)"; ctx.lineWidth = 1.5; ctx.stroke();
    const fy = cy + fdir * (bodyH / 2 - 10);
    ctx.fillStyle = "#f3f1ea";
    ctx.beginPath();
    ctx.moveTo(cx, fy + fdir * 6); ctx.lineTo(cx - 8, fy - fdir * 4); ctx.lineTo(cx + 8, fy - fdir * 4);
    ctx.closePath(); ctx.fill();
    ctx.fillStyle = "#64686b";
    ctx.font = "500 13px Barlow, system-ui";
    ctx.textAlign = "center";
    ctx.fillText("przód", cx, cy + fdir * (bodyH / 2 + 18) + 4);
    ctx.textAlign = "left";

    for (const wh of WHEELS) {
      const v = st?.drive.wheels[wh.k]?.v ?? 0;
      this.phase[wh.k] = (this.phase[wh.k] + (v / circ) * dt) % 1;
      const x = cx + side * wh.Y * (bodyW / 2 + wheelW / 2 + 5) - wheelW / 2;
      const y = cy + fdir * wh.X * (bodyH / 2 - wheelH / 2 - 4) - wheelH / 2;
      const moving = Math.abs(v) > 0.005;
      ctx.save();
      roundRect(ctx, x, y, wheelW, wheelH, 5);
      ctx.fillStyle = "#0f1012"; ctx.fill();
      ctx.clip();
      const sp = 7;
      const off = (((this.phase[wh.k] * sp * 6) % sp) + sp) % sp;
      ctx.strokeStyle = moving ? "#f3f1ea" : "#3a3f44";
      ctx.lineWidth = 2.5;
      for (let yy = -sp; yy < wheelH + sp; yy += sp) {
        const py = y + (fdir > 0 ? yy + off : yy - off);
        ctx.beginPath(); ctx.moveTo(x + 3, py); ctx.lineTo(x + wheelW - 3, py); ctx.stroke();
      }
      ctx.restore();
      if (moving) {
        const ax = x + wheelW / 2 + side * wh.Y * (wheelW / 2 + 12);
        const ay = y + wheelH / 2;
        const d = fdir * Math.sign(v);
        ctx.strokeStyle = "#f3f1ea";
        ctx.lineWidth = 2;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.beginPath();
        ctx.moveTo(ax - 5, ay - d * 3); ctx.lineTo(ax, ay + d * 3); ctx.lineTo(ax + 5, ay - d * 3);
        ctx.stroke();
      }
    }
  }
}
