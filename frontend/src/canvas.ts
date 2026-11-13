/**
 * Dopasowuje bufor canvasa do rzeczywistej liczby pikseli ekranu (scena jest skalowana
 * transformacją CSS) i ustawia transformację tak, by rysować w pikselach projektu 1920x1080.
 */
export function fit(canvas: HTMLCanvasElement): { ctx: CanvasRenderingContext2D; w: number; h: number } {
  const ctx = canvas.getContext("2d")!;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const pw = Math.max(1, Math.round(rect.width * dpr)), ph = Math.max(1, Math.round(rect.height * dpr));
  if (canvas.width !== pw || canvas.height !== ph) { canvas.width = pw; canvas.height = ph; }
  ctx.setTransform(pw / Math.max(1, w), 0, 0, ph / Math.max(1, h), 0, 0);
  return { ctx, w, h };
}

export function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number): void {
  const rr = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + rr, y);
  ctx.arcTo(x + w, y, x + w, y + h, rr);
  ctx.arcTo(x + w, y + h, x, y + h, rr);
  ctx.arcTo(x, y + h, x, y, rr);
  ctx.arcTo(x, y, x + w, y, rr);
  ctx.closePath();
}

export function css(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export const fmt = (v: number | null | undefined, d = 2): string =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : v.toFixed(d).replace(".", ",");
