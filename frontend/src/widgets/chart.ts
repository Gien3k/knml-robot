import { fit } from "../canvas";

export interface Series { color: string; label?: string }
export interface Threshold { v: number; color: string; label: string }

/** Wykres czasowy (jedna oś Y, przesuwne okno). Linie 2 px, stonowana siatka, etykiety przy końcach. */
export class TimeChart {
  private data: { t: number; v: (number | null)[] }[] = [];

  constructor(
    private canvas: HTMLCanvasElement,
    private opts: {
      series: Series[]; min: number; max: number; window: number;
      thresholds?: Threshold[]; zero?: boolean; ticks?: number[]; fmt?: (v: number) => string;
    },
  ) {}

  setRange(min: number, max: number, ticks?: number[]): void {
    this.opts.min = min;
    this.opts.max = max;
    if (ticks) this.opts.ticks = ticks;
  }

  setThresholds(t: Threshold[]): void { this.opts.thresholds = t; }

  push(t: number, v: (number | null)[]): void {
    this.data.push({ t, v });
    const cut = t - this.opts.window - 1;
    while (this.data.length && this.data[0].t < cut) this.data.shift();
  }

  draw(now: number): void {
    const { ctx, w, h } = fit(this.canvas);
    ctx.clearRect(0, 0, w, h);
    const o = this.opts;
    const L = 40, R = o.series.some((s) => s.label) ? 74 : 46, T = 6, B = 6;
    const pw = w - L - R, ph = h - T - B;
    const X = (t: number) => L + ((t - (now - o.window)) / o.window) * pw;
    const Y = (v: number) => T + (1 - (Math.min(o.max, Math.max(o.min, v)) - o.min) / (o.max - o.min)) * ph;
    const f = o.fmt ?? ((v: number) => v.toFixed(1).replace(".", ","));

    ctx.font = "500 11px JetBrains Mono, monospace";
    ctx.textBaseline = "middle";
    for (const tv of o.ticks ?? [o.min, (o.min + o.max) / 2, o.max]) {
      const y = Math.round(Y(tv)) + 0.5;
      ctx.strokeStyle = tv === 0 && o.zero ? "#383835" : "#2c2c2a";
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(L + pw, y); ctx.stroke();
      ctx.fillStyle = "#898781";
      ctx.textAlign = "right";
      ctx.fillText(f(tv), L - 6, y);
    }
    for (const th of o.thresholds ?? []) {
      const y = Math.round(Y(th.v)) + 0.5;
      ctx.strokeStyle = th.color;
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(L + pw, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#c3c2b7";
      ctx.textAlign = "left";
      ctx.fillText(th.label, L + pw + 6, y);
    }

    ctx.save();
    ctx.beginPath(); ctx.rect(L, 0, pw, h); ctx.clip();
    o.series.forEach((s, i) => {
      ctx.strokeStyle = s.color;
      ctx.lineWidth = 2;
      ctx.lineJoin = "round";
      ctx.lineCap = "round";
      ctx.beginPath();
      let pen = false;
      for (const d of this.data) {
        const v = d.v[i];
        if (v === null || v === undefined) { pen = false; continue; }
        const x = X(d.t), y = Y(v);
        if (!pen) { ctx.moveTo(x, y); pen = true; } else ctx.lineTo(x, y);
      }
      ctx.stroke();
    });
    ctx.restore();

    // etykiety bezpośrednie przy ostatnim punkcie (tekst w kolorze atramentu, znacznik w kolorze serii)
    const last = this.data[this.data.length - 1];
    if (last) {
      const placed: number[] = [];
      o.series.forEach((s, i) => {
        const v = last.v[i];
        if (v === null || v === undefined || !s.label) return;
        let y = Y(v);
        for (const p of placed) if (Math.abs(p - y) < 13) y = p + (y >= p ? 13 : -13);
        placed.push(y);
        ctx.fillStyle = s.color;
        ctx.beginPath(); ctx.arc(L + pw + 6, y, 3.5, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = "#f0efec";
        ctx.textAlign = "left";
        ctx.fillText(`${s.label} ${f(v)}`, L + pw + 13, y);
      });
    }
    ctx.textBaseline = "alphabetic";
  }
}
