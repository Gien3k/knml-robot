import { fmt, roundRect } from "../canvas";
import type { FrameHeader } from "../types";
import type { Rect } from "./video";

const PAINT = "#f3f1ea";
const SHADE = "rgba(10,11,12,0.55)";
const GO = "#3fc27a", STOP = "#e5484d", CAUTION = "#f2b33d";

function label(ctx: CanvasRenderingContext2D, x: number, y: number, text: string, placed: number[][], accent?: string, maxW = 1e9): void {
  ctx.font = "600 16px Barlow, system-ui";
  const w = ctx.measureText(text).width + (accent ? 26 : 18), h = 27;
  x = Math.min(Math.max(8, x), maxW - w - 8);
  for (let k = 0; k < 12; k++) {
    if (!placed.some(([px, py, pw, ph]) => x < px + pw && x + w > px && y - h < py + ph && y > py)) break;
    y += h + 4;
  }
  placed.push([x, y - h, w, h]);
  roundRect(ctx, x, y - h, w, h, 5);
  ctx.fillStyle = "rgba(22,24,26,0.8)";
  ctx.fill();
  let tx = x + 9;
  if (accent) {
    ctx.fillStyle = accent;
    roundRect(ctx, x + 8, y - h + 7, 4, h - 14, 2);
    ctx.fill();
    tx += 9;
  }
  ctx.fillStyle = PAINT;
  ctx.fillText(text, tx, y - 8);
}

function brackets(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number): void {
  const l = Math.min(26, w * 0.28, h * 0.28);
  const path = () => {
    ctx.beginPath();
    ctx.moveTo(x, y + l); ctx.lineTo(x, y); ctx.lineTo(x + l, y);
    ctx.moveTo(x + w - l, y); ctx.lineTo(x + w, y); ctx.lineTo(x + w, y + l);
    ctx.moveTo(x + w, y + h - l); ctx.lineTo(x + w, y + h); ctx.lineTo(x + w - l, y + h);
    ctx.moveTo(x + l, y + h); ctx.lineTo(x, y + h); ctx.lineTo(x, y + h - l);
  };
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  path(); ctx.strokeStyle = SHADE; ctx.lineWidth = 4; ctx.stroke();
  path(); ctx.strokeStyle = PAINT; ctx.lineWidth = 2; ctx.stroke();
}

function box(r: Rect, b: number[], mirror: boolean): [number, number, number, number] {
  let x1 = b[0], x2 = b[2];
  if (mirror) { const t = 1 - x2; x2 = 1 - x1; x1 = t; }
  return [r.x + x1 * r.w, r.y + b[1] * r.h, (x2 - x1) * r.w, (b[3] - b[1]) * r.h];
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** Nakładka na obraz OAK-D: osoby z odległością 3D i rozpoznany gest. */
export function oakOverlay(ctx: CanvasRenderingContext2D, r: Rect, hdr: FrameHeader, mirror: boolean, view: Rect): void {
  const placed: number[][] = [[0, 0, 480, 54]];   // miejsce na podpis kamery
  const dets = [...(hdr.dets ?? [])].sort((a, b) => a.xyz[2] - b.xyz[2]);
  for (const d of dets) {
    const [x, y, w, h] = box(r, d.bbox, mirror);
    if (w < 8 || h < 8) continue;
    brackets(ctx, x, y, w, h);
    const dist = Math.hypot(d.xyz[0], d.xyz[2]);
    label(ctx, x, Math.max(30, y + 34), `${cap(d.label)} ${fmt(dist, 1)} m`, placed, undefined, view.w);
  }
  const hand = hdr.hand;
  if (hand) {
    const [x, y, w, h] = box(r, hand.bbox, mirror);
    const col = hand.gesture === "OPEN_PALM" ? GO : hand.gesture === "FIST" ? STOP : CAUTION;
    const name = hand.gesture === "OPEN_PALM" ? "Otwarta dłoń" : hand.gesture === "FIST" ? "Pięść" : "Dłoń";
    roundRect(ctx, x - 8, y - 8, w + 16, h + 16, 12);
    ctx.strokeStyle = SHADE; ctx.lineWidth = 5; ctx.stroke();
    ctx.strokeStyle = col; ctx.lineWidth = 3; ctx.stroke();
    label(ctx, x - 8, Math.max(30, y - 14), name, placed, col, view.w);
  }
}
