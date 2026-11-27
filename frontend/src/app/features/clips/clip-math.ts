import { CropKeyframe } from '../../core/models';

/** Output sizes and the crop window, mirroring backend/app/services/crop_logic.py so the overlay in the editor is
 *  exactly the window the render will cut. The backend is the authority; clip-math.spec.ts holds its numbers. */
export const ASPECT_SIZES: Record<string, [number, number]> = {
  '9:16': [1080, 1920],
  '1:1': [1080, 1080],
  '16:9': [1920, 1080],
};

export const FRAME_MS = 40; // one frame at 25 fps: the editor's smallest step

const even = (v: number): number => Math.max(2, Math.trunc(v) - (Math.trunc(v) % 2));

export function windowSize(
  srcW: number,
  srcH: number,
  aspect: string,
  zoom: number,
): [number, number] {
  const [aw, ah] = ASPECT_SIZES[aspect];
  const ratio = aw / ah;
  const w = Math.min(srcW, srcH * ratio);
  const h = w / ratio;
  return [even(w / zoom), even(h / zoom)];
}

export function panAt(kfs: CropKeyframe[], tMs: number): [number, number] {
  if (!kfs.length) return [0.5, 0.5];
  if (tMs <= kfs[0].t_ms) return [kfs[0].cx, kfs[0].cy];
  for (let i = 0; i + 1 < kfs.length; i++) {
    const a = kfs[i];
    const b = kfs[i + 1];
    if (tMs <= b.t_ms) {
      const f = (tMs - a.t_ms) / (b.t_ms - a.t_ms);
      return [a.cx + (b.cx - a.cx) * f, a.cy + (b.cy - a.cy) * f];
    }
  }
  const last = kfs[kfs.length - 1];
  return [last.cx, last.cy];
}

const clamp = (v: number, lo: number, hi: number): number => Math.max(lo, Math.min(hi, v));

export interface Win {
  x: number;
  y: number;
  w: number;
  h: number;
}

export function windowAt(
  kfs: CropKeyframe[],
  srcW: number,
  srcH: number,
  aspect: string,
  zoom: number,
  tMs: number,
): Win {
  const [w, h] = windowSize(srcW, srcH, aspect, zoom);
  const [cx, cy] = panAt(kfs, tMs);
  return {
    x: Math.round(clamp(cx * srcW - w / 2, 0, srcW - w)),
    y: Math.round(clamp(cy * srcH - h / 2, 0, srcH - h)),
    w,
    h,
  };
}

/** A pointer position (fractions of the picture) to the window centre that keeps the window inside the picture. */
export function centreFor(
  px: number,
  py: number,
  srcW: number,
  srcH: number,
  aspect: string,
  zoom: number,
): [number, number] {
  const [w, h] = windowSize(srcW, srcH, aspect, zoom);
  const hx = w / srcW / 2;
  const hy = h / srcH / 2;
  return [
    Math.round(clamp(px, hx, 1 - hx) * 10000) / 10000,
    Math.round(clamp(py, hy, 1 - hy) * 10000) / 10000,
  ];
}

export function upsertKeyframe(kfs: CropKeyframe[], k: CropKeyframe): CropKeyframe[] {
  return [...kfs.filter((x) => x.t_ms !== k.t_ms), k].sort((a, b) => a.t_ms - b.t_ms);
}

export function snapToFrame(ms: number): number {
  return Math.round(ms / FRAME_MS) * FRAME_MS;
}

/** "1:23.40" style time with hundredths: precise enough to see a single frame. */
export function formatMs(ms: number): string {
  const total = Math.max(0, ms) / 1000;
  const m = Math.floor(total / 60);
  const s = total - m * 60;
  return `${m}:${s.toFixed(2).padStart(5, '0')}`;
}

/** Parse "83.4", "1:23.4" or "0:01:23.4" into milliseconds; null when it is not a time. */
export function parseTime(text: string): number | null {
  const parts = text.trim().split(':');
  if (!parts.length || parts.length > 3 || parts.some((p) => !/^\d+(\.\d+)?$/.test(p))) return null;
  const nums = parts.map(Number);
  const seconds = nums.reduce((acc, n) => acc * 60 + n, 0);
  return Math.round(seconds * 1000);
}
