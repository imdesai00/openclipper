import { TestBed } from '@angular/core/testing';
import { describe, expect, it, vi } from 'vitest';

import { ClipTimeline } from './clip-timeline.component';

function setup(start = 10_000, end = 20_000) {
  const f = TestBed.createComponent(ClipTimeline);
  f.componentRef.setInput('durationMs', 60_000);
  f.componentRef.setInput('startMs', start);
  f.componentRef.setInput('endMs', end);
  f.componentRef.setInput('cutsMs', [30_000]);
  f.detectChanges();
  const starts: number[] = [];
  const ends: number[] = [];
  f.componentInstance.startChange.subscribe((v) => starts.push(v));
  f.componentInstance.endChange.subscribe((v) => ends.push(v));
  return { f, starts, ends };
}

const key = (k: string, shiftKey = false) =>
  new KeyboardEvent('keydown', { key: k, shiftKey, cancelable: true });

describe('ClipTimeline', () => {
  it('exposes both handles as labelled sliders with real values', () => {
    const { f } = setup();
    const sliders = Array.from(
      (f.nativeElement as HTMLElement).querySelectorAll('[role=slider]'),
    ) as HTMLElement[];
    expect(sliders.map((s) => s.getAttribute('aria-label'))).toEqual(['Clip start', 'Clip end']);
    expect(sliders[0].getAttribute('aria-valuenow')).toBe('10000');
    expect(sliders[0].getAttribute('aria-valuetext')).toBe('0:10.00');
    expect(sliders[1].getAttribute('aria-valuenow')).toBe('20000');
  });

  it('moves a handle one frame with the arrows and one second with Shift', () => {
    const { f, starts, ends } = setup();
    const c = f.componentInstance;
    const e1 = key('ArrowRight');
    c.onKey(e1, 'start');
    c.onKey(key('ArrowLeft', true), 'end');
    expect(starts).toEqual([10_040]);
    expect(ends).toEqual([19_000]);
    expect(e1.defaultPrevented).toBe(true);
    const other = key('a');
    c.onKey(other, 'start');
    expect(other.defaultPrevented).toBe(false); // other keys are left alone
  });

  it('never lets the handles cross, leave the media, or squeeze the clip below a second', () => {
    const { f, starts, ends } = setup(10_000, 11_500);
    const c = f.componentInstance;
    c.moveTo('start', 11_400);
    c.moveTo('end', 10_100);
    c.moveTo('start', -5000);
    c.moveTo('end', 99_000);
    expect(starts).toEqual([10_500, 0]); // at least one second before the end
    expect(ends).toEqual([11_000, 60_000]); // at least one second after the start, never past the media
  });

  it('turns a click on the bar into a seek, but not a click on a handle', () => {
    const { f } = setup();
    const seek = vi.fn();
    f.componentInstance.seek.subscribe(seek);
    const bar = (f.nativeElement as HTMLElement).querySelector('div')!;
    vi.spyOn(bar, 'getBoundingClientRect').mockReturnValue({ left: 0, width: 600 } as DOMRect);
    f.componentInstance.onBarDown({ target: bar, clientX: 300 } as unknown as PointerEvent);
    expect(seek).toHaveBeenCalledWith(30_000);
    const handle = (f.nativeElement as HTMLElement).querySelector('[role=slider]')!;
    seek.mockClear();
    f.componentInstance.onBarDown({ target: handle, clientX: 300 } as unknown as PointerEvent);
    expect(seek).not.toHaveBeenCalled();
  });
});
