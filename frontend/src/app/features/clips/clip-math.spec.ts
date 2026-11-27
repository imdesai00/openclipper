import { describe, expect, it } from 'vitest';

import {
  centreFor,
  formatMs,
  panAt,
  parseTime,
  snapToFrame,
  upsertKeyframe,
  windowAt,
  windowSize,
} from './clip-math';

// These are the numbers backend/tests/unit/test_captions_crop_render_qc.py asserts for the same inputs.
describe('clip-math (mirrors the backend crop window)', () => {
  it('finds the largest window of each aspect and shrinks it with zoom', () => {
    expect(windowSize(1920, 1080, '9:16', 1)).toEqual([606, 1080]);
    expect(windowSize(1920, 1080, '1:1', 1)).toEqual([1080, 1080]);
    expect(windowSize(1920, 1080, '16:9', 1)).toEqual([1920, 1080]);
    expect(windowSize(1920, 1080, '9:16', 2)).toEqual([302, 540]);
    const [w, h] = windowSize(1281, 721, '9:16', 1.3);
    expect(w % 2).toBe(0);
    expect(h % 2).toBe(0);
  });

  it('interpolates pan between keyframes and holds outside them', () => {
    const k = [
      { t_ms: 1000, cx: 0.2, cy: 0.5 },
      { t_ms: 3000, cx: 0.8, cy: 0.5 },
    ];
    expect(panAt(k, 0)).toEqual([0.2, 0.5]);
    expect(panAt(k, 2000)[0]).toBeCloseTo(0.5);
    expect(panAt(k, 4000)).toEqual([0.8, 0.5]);
    expect(panAt([], 123)).toEqual([0.5, 0.5]);
  });

  it('never lets the window leave the picture', () => {
    const k = [
      { t_ms: 0, cx: 0, cy: 0 },
      { t_ms: 1000, cx: 1, cy: 1 },
    ];
    for (const t of [0, 250, 500, 1000]) {
      const w = windowAt(k, 1920, 1080, '9:16', 1, t);
      expect(w.x).toBeGreaterThanOrEqual(0);
      expect(w.x).toBeLessThanOrEqual(1920 - w.w);
      expect(w.y).toBeGreaterThanOrEqual(0);
      expect(w.y).toBeLessThanOrEqual(1080 - w.h);
    }
  });

  it('turns a pointer position into a centre that keeps the window inside', () => {
    const [cx] = centreFor(0, 0.5, 1920, 1080, '9:16', 1);
    expect(cx).toBeCloseTo(606 / 1920 / 2, 3); // pushed in from the left edge by half a window
    expect(centreFor(0.5, 0.5, 1920, 1080, '9:16', 1)).toEqual([0.5, 0.5]);
  });

  it('keeps keyframes sorted and replaces one at the same time', () => {
    const out = upsertKeyframe(
      upsertKeyframe([{ t_ms: 500, cx: 0.5, cy: 0.5 }], { t_ms: 100, cx: 0.1, cy: 0.5 }),
      { t_ms: 500, cx: 0.9, cy: 0.5 },
    );
    expect(out.map((k) => [k.t_ms, k.cx])).toEqual([
      [100, 0.1],
      [500, 0.9],
    ]);
  });

  it('formats and parses times to the hundredth, and snaps to a frame', () => {
    expect(formatMs(83400)).toBe('1:23.40');
    expect(formatMs(-5)).toBe('0:00.00');
    expect(parseTime('83.4')).toBe(83400);
    expect(parseTime('1:23.4')).toBe(83400);
    expect(parseTime('0:01:23.4')).toBe(83400);
    expect(parseTime('abc')).toBeNull();
    expect(parseTime('1:2:3:4')).toBeNull();
    expect(snapToFrame(1013)).toBe(1000);
    expect(snapToFrame(1021)).toBe(1040);
  });
});
