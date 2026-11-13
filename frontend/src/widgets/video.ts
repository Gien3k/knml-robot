import { fit } from "../canvas";
import { wsUrl } from "../net";
import type { FrameHeader } from "../types";

export interface Rect { x: number; y: number; w: number; h: number }
export type Overlay = (ctx: CanvasRenderingContext2D, r: Rect, hdr: FrameHeader, mirror: boolean, view: Rect) => void;

/**
 * Kafelek wideo: binarny WebSocket [u32 długość nagłówka][JSON][JPEG].
 * Dekodowanie przez createImageBitmap (poza głównym wątkiem); gdy dekoder jest zajęty,
 * trzymamy tylko NAJNOWSZĄ klatkę – starsze są porzucane (niskie opóźnienie).
 */
export class VideoTile {
  private bmp: ImageBitmap | null = null;
  header: FrameHeader | null = null;
  private lastAt = 0;
  private busy = false;
  private pending: ArrayBuffer | null = null;
  private frames = 0;
  private fpsT = performance.now();
  fps = 0;
  private dec = new TextDecoder();

  constructor(
    private canvas: HTMLCanvasElement,
    private stream: string,
    private opts: { mirror?: () => boolean; overlay?: Overlay; fit?: "contain" | "cover"; label: string },
  ) {}

  start(): void {
    const open = () => {
      const ws = new WebSocket(wsUrl(`/ws/video/${this.stream}`));
      ws.binaryType = "arraybuffer";
      ws.onmessage = (e) => this.onData(e.data as ArrayBuffer);
      ws.onclose = () => setTimeout(open, 1000);
      ws.onerror = () => ws.close();
    };
    open();
  }

  private onData(buf: ArrayBuffer): void {
    if (this.busy) { this.pending = buf; return; }
    void this.decode(buf);
  }

  private async decode(buf: ArrayBuffer): Promise<void> {
    this.busy = true;
    try {
      const n = new DataView(buf).getUint32(0);
      const hdr = JSON.parse(this.dec.decode(new Uint8Array(buf, 4, n))) as FrameHeader;
      const bmp = await createImageBitmap(new Blob([new Uint8Array(buf, 4 + n)], { type: "image/jpeg" }));
      this.bmp?.close();
      this.bmp = bmp;
      this.header = hdr;
      this.lastAt = performance.now();
      this.frames++;
    } catch {
      /* uszkodzona klatka – pomijamy */
    } finally {
      this.busy = false;
      const p = this.pending;
      this.pending = null;
      if (p) void this.decode(p);
    }
  }

  get stale(): boolean { return performance.now() - this.lastAt > 1500; }

  draw(): void {
    const now = performance.now();
    if (now - this.fpsT > 1000) { this.fps = (this.frames * 1000) / (now - this.fpsT); this.frames = 0; this.fpsT = now; }
    const { ctx, w, h } = fit(this.canvas);
    ctx.fillStyle = "#0b0b0b";
    ctx.fillRect(0, 0, w, h);
    const view = { x: 0, y: 0, w, h };
    if (this.bmp && this.header) {
      const iw = this.bmp.width, ih = this.bmp.height;
      const s = this.opts.fit === "cover" ? Math.max(w / iw, h / ih) : Math.min(w / iw, h / ih);
      const r = { w: iw * s, h: ih * s, x: (w - iw * s) / 2, y: (h - ih * s) / 2 };
      const mirror = this.opts.mirror?.() ?? false;
      ctx.save();
      if (mirror) { ctx.translate(w, 0); ctx.scale(-1, 1); }
      ctx.drawImage(this.bmp, r.x, r.y, r.w, r.h);
      ctx.restore();
      ctx.save();
      ctx.beginPath(); ctx.rect(0, 0, w, h); ctx.clip();
      this.opts.overlay?.(ctx, r, this.header, mirror, view);
      ctx.restore();
    }
    if (this.stale) {
      ctx.fillStyle = "rgba(13,13,13,0.72)";
      ctx.fillRect(0, 0, w, h);
      ctx.fillStyle = "#c3c2b7";
      ctx.font = "600 16px Inter Variable, system-ui";
      ctx.textAlign = "center";
      ctx.fillText(`${this.opts.label}: brak sygnału – łączenie…`, w / 2, h / 2);
      ctx.textAlign = "left";
    }
  }
}
