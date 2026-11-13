import { fmt, roundRect } from "../canvas";
import type { FrameHeader } from "../types";
import type { Rect } from "./video";

const C = {
  ink: "#f0efec", ink2: "#c3c2b7", muted: "#898781", blue: "#3987e5",
  good: "#0ca30c", warning: "#fab219", critical: "#d03b3b", surface: "rgba(13,13,13,0.78)",
};

function chip(ctx: CanvasRenderingContext2D, x: number, y: number, text: string, mark: string, placed?: number[][]): void {
  ctx.font = "600 14px Inter Variable, system-ui";
  const tw = ctx.measureText(text).width;
  const w = tw + 28, h = 24;
  // unikanie nakładania się etykiet: przesuwamy w dół aż do wolnego miejsca
  if (placed) {
    for (let k = 0; k < 12; k++) {
      const hit = placed.some(([px, py, pw, ph]) => x < px + pw && x + w > px && y - h < py + ph && y > py);
      if (!hit) break;
      y += h + 4;
    }
    placed.push([x, y - h, w, h]);
  }
  roundRect(ctx, x, y - h, w, h, 6);
  ctx.fillStyle = C.surface;
  ctx.fill();
  ctx.fillStyle = mark;
  ctx.beginPath(); ctx.arc(x + 11, y - h / 2, 4, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = C.ink;
  ctx.fillText(text, x + 20, y - 7);
}

function corners(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, color: string, lw = 2): void {
  const l = Math.min(22, w / 3, h / 3);
  ctx.strokeStyle = color;
  ctx.lineWidth = lw;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(x, y + l); ctx.lineTo(x, y); ctx.lineTo(x + l, y);
  ctx.moveTo(x + w - l, y); ctx.lineTo(x + w, y); ctx.lineTo(x + w, y + l);
  ctx.moveTo(x + w, y + h - l); ctx.lineTo(x + w, y + h); ctx.lineTo(x + w - l, y + h);
  ctx.moveTo(x + l, y + h); ctx.lineTo(x, y + h); ctx.lineTo(x, y + h - l);
  ctx.stroke();
}

/** bbox znormalizowany -> piksele (z uwzględnieniem lustra) */
function box(r: Rect, b: number[], mirror: boolean): [number, number, number, number] {
  let x1 = b[0], x2 = b[2];
  if (mirror) { const t = 1 - x2; x2 = 1 - x1; x1 = t; }
  return [r.x + x1 * r.w, r.y + b[1] * r.h, (x2 - x1) * r.w, (b[3] - b[1]) * r.h];
}

export function oakOverlay(ctx: CanvasRenderingContext2D, r: Rect, hdr: FrameHeader, mirror: boolean, view: Rect): void {
  // detekcje osób (sieć na VPU) + odległość 3D; najpierw bliższe (ważniejsze etykiety)
  const placed: number[][] = [];
  const dets = [...(hdr.dets ?? [])].sort((a, b) => a.xyz[2] - b.xyz[2]);
  for (const d of dets) {
    const [x, y, w, h] = box(r, d.bbox, mirror);
    const person = d.label === "osoba";
    ctx.globalAlpha = person ? 1 : 0.7;
    ctx.strokeStyle = "rgba(57,135,229,0.25)";
    ctx.lineWidth = 1;
    roundRect(ctx, x, y, w, h, 6);
    ctx.stroke();
    corners(ctx, x, y, w, h, person ? C.blue : C.ink2);
    const dist = Math.hypot(d.xyz[0], d.xyz[2]);
    chip(ctx, x, Math.max(26, y - 6), `${d.label} ${Math.round(d.conf * 100)}% · ${fmt(dist, 2)} m`, person ? C.blue : C.ink2, placed);
    ctx.globalAlpha = 1;
  }
  // dłoń + gest
  const hand = hdr.hand;
  if (hand) {
    const [x, y, w, h] = box(r, hand.bbox, mirror);
    const col = hand.gesture === "OPEN_PALM" ? C.good : hand.gesture === "FIST" ? C.critical : C.warning;
    ctx.strokeStyle = col;
    ctx.lineWidth = 3;
    roundRect(ctx, x - 6, y - 6, w + 12, h + 12, 10);
    ctx.stroke();
    const name = hand.gesture === "OPEN_PALM" ? "OTWARTA DŁOŃ" : hand.gesture === "FIST" ? "PIĘŚĆ" : "NIEPEWNY GEST";
    chip(ctx, x - 6, Math.max(26, y - 12), `${name} ${Math.round(hand.conf * 100)}% · ${fmt(hand.z, 2)} m`, col, placed);
  }
  // pasek skrętu (perspektywa widza)
  const bw = 360, bx = view.w / 2 - bw / 2, by = view.h - 30;
  roundRect(ctx, bx - 14, by - 22, bw + 28, 40, 10);
  ctx.fillStyle = C.surface;
  ctx.fill();
  ctx.fillStyle = "#383835";
  ctx.fillRect(bx, by - 2, bw, 4);
  ctx.fillRect(bx + bw / 2 - 1, by - 10, 2, 20);
  ctx.font = "600 12px Inter Variable, system-ui";
  ctx.fillStyle = C.muted;
  ctx.fillText("SKRĘT ←", bx, by - 8);
  ctx.textAlign = "right";
  ctx.fillText("→", bx + bw, by - 8);
  ctx.textAlign = "left";
  if (hand) {
    const off = (mirror ? -1 : 1) * hand.offset;
    const px = bx + bw / 2 + off * (bw / 2);
    ctx.fillStyle = hand.gesture === "OPEN_PALM" ? C.good : C.ink2;
    ctx.beginPath(); ctx.arc(px, by, 8, 0, Math.PI * 2); ctx.fill();
  }
}

export function fpvOverlay(ctx: CanvasRenderingContext2D, _r: Rect, hdr: FrameHeader, _mirror: boolean, view: Rect): void {
  const cx = view.w / 2, cy = view.h / 2;
  ctx.strokeStyle = "rgba(240,239,236,0.75)";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(cx - 18, cy); ctx.lineTo(cx - 6, cy);
  ctx.moveTo(cx + 6, cy); ctx.lineTo(cx + 18, cy);
  ctx.moveTo(cx, cy - 18); ctx.lineTo(cx, cy - 6);
  ctx.moveTo(cx, cy + 6); ctx.lineTo(cx, cy + 18);
  ctx.stroke();
  ctx.beginPath(); ctx.arc(cx, cy, 30, 0, Math.PI * 2); ctx.strokeStyle = "rgba(57,135,229,0.6)"; ctx.stroke();
  // wskaźnik głowicy pan-tilt
  const pan = hdr.pan ?? 0, tilt = hdr.tilt ?? 0;
  ctx.font = "600 12px JetBrains Mono, monospace";
  const txt = `PAN ${pan >= 0 ? "+" : ""}${pan.toFixed(0)}°  TILT ${tilt.toFixed(0)}°`;
  roundRect(ctx, 10, 10, ctx.measureText(txt).width + 16, 22, 5);
  ctx.fillStyle = C.surface; ctx.fill();
  ctx.fillStyle = C.ink; ctx.fillText(txt, 18, 25);
  // pasek kąta pan (-150..150)
  const bw = view.w - 40, bx = 20, by = view.h - 14;
  ctx.fillStyle = "rgba(13,13,13,0.6)"; ctx.fillRect(bx, by - 2, bw, 4);
  ctx.fillStyle = C.blue;
  ctx.fillRect(bx + bw / 2 - (pan / 150) * (bw / 2) - 2, by - 6, 4, 12); // pan>0 = w lewo robota
}
