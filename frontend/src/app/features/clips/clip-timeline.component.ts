import {
  AfterViewInit,
  Component,
  ElementRef,
  OnDestroy,
  computed,
  effect,
  input,
  output,
  viewChild,
} from '@angular/core';

import { FRAME_MS, formatMs, snapToFrame } from './clip-math';

/** Waveform with scene cuts, the clip's range and the playhead. The range handles are real sliders: drag them, or focus
 *  one and use the arrow keys (one frame; Shift for one second). */
@Component({
  selector: 'app-clip-timeline',
  template: `
    <div
      #bar
      class="relative h-24 w-full touch-none select-none overflow-hidden rounded-md border border-zinc-300 bg-zinc-100 dark:border-zinc-700 dark:bg-zinc-900"
      (pointerdown)="onBarDown($event)"
    >
      <canvas #wave class="absolute inset-0 h-full w-full" aria-hidden="true"></canvas>
      @for (c of cutPercents(); track c) {
        <span
          class="absolute inset-y-0 w-px bg-amber-500/70"
          [style.left.%]="c"
          aria-hidden="true"
        ></span>
      }
      <div
        class="pointer-events-none absolute inset-y-0 bg-indigo-500/20"
        [style.left.%]="startPct()"
        [style.width.%]="endPct() - startPct()"
        aria-hidden="true"
      ></div>
      <span
        class="pointer-events-none absolute inset-y-0 w-0.5 bg-red-500"
        [style.left.%]="playPct()"
        aria-hidden="true"
      ></span>
      <button
        type="button"
        role="slider"
        class="absolute inset-y-0 w-3 -translate-x-1/2 cursor-ew-resize rounded bg-indigo-600 focus-visible:outline-2"
        [style.left.%]="startPct()"
        aria-label="Clip start"
        [attr.aria-valuemin]="0"
        [attr.aria-valuemax]="endMs() - minLengthMs"
        [attr.aria-valuenow]="startMs()"
        [attr.aria-valuetext]="fmt(startMs())"
        [disabled]="disabled()"
        (pointerdown)="beginDrag($event, 'start')"
        (keydown)="onKey($event, 'start')"
      ></button>
      <button
        type="button"
        role="slider"
        class="absolute inset-y-0 w-3 -translate-x-1/2 cursor-ew-resize rounded bg-indigo-600 focus-visible:outline-2"
        [style.left.%]="endPct()"
        aria-label="Clip end"
        [attr.aria-valuemin]="startMs() + minLengthMs"
        [attr.aria-valuemax]="durationMs()"
        [attr.aria-valuenow]="endMs()"
        [attr.aria-valuetext]="fmt(endMs())"
        [disabled]="disabled()"
        (pointerdown)="beginDrag($event, 'end')"
        (keydown)="onKey($event, 'end')"
      ></button>
    </div>
  `,
})
export class ClipTimeline implements AfterViewInit, OnDestroy {
  readonly durationMs = input.required<number>();
  readonly startMs = input.required<number>();
  readonly endMs = input.required<number>();
  readonly playheadMs = input(0);
  readonly peaks = input<number[][]>([]);
  readonly cutsMs = input<number[]>([]);
  readonly disabled = input(false);
  readonly startChange = output<number>();
  readonly endChange = output<number>();
  readonly seek = output<number>();

  readonly minLengthMs = 1000;
  readonly fmt = formatMs;
  private readonly bar = viewChild.required<ElementRef<HTMLDivElement>>('bar');
  private readonly wave = viewChild.required<ElementRef<HTMLCanvasElement>>('wave');
  private ro: ResizeObserver | null = null;
  private drag: 'start' | 'end' | null = null;

  readonly startPct = computed(() => this.pct(this.startMs()));
  readonly endPct = computed(() => this.pct(this.endMs()));
  readonly playPct = computed(() => this.pct(this.playheadMs()));
  readonly cutPercents = computed(() => this.cutsMs().map((c) => this.pct(c)));

  constructor() {
    effect(() => {
      this.peaks();
      this.draw();
    });
  }

  ngAfterViewInit(): void {
    this.draw();
    if (typeof ResizeObserver !== 'undefined') {
      this.ro = new ResizeObserver(() => this.draw());
      this.ro.observe(this.bar().nativeElement);
    }
  }

  ngOnDestroy(): void {
    this.ro?.disconnect();
  }

  private pct(ms: number): number {
    const d = this.durationMs();
    return d > 0 ? Math.max(0, Math.min(100, (ms / d) * 100)) : 0;
  }

  private msAt(clientX: number): number {
    const r = this.bar().nativeElement.getBoundingClientRect();
    const f = r.width > 0 ? (clientX - r.left) / r.width : 0;
    return snapToFrame(Math.max(0, Math.min(1, f)) * this.durationMs());
  }

  onBarDown(e: PointerEvent): void {
    if ((e.target as HTMLElement).getAttribute('role') === 'slider') return;
    this.seek.emit(this.msAt(e.clientX));
  }

  beginDrag(e: PointerEvent, which: 'start' | 'end'): void {
    e.preventDefault();
    e.stopPropagation();
    this.drag = which;
    const move = (ev: PointerEvent): void => this.moveTo(this.drag!, this.msAt(ev.clientX));
    const up = (): void => {
      this.drag = null;
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  /** Keep at least `minLengthMs` between the handles and both inside the media. */
  moveTo(which: 'start' | 'end', ms: number): void {
    if (which === 'start') {
      this.startChange.emit(Math.max(0, Math.min(ms, this.endMs() - this.minLengthMs)));
    } else {
      this.endChange.emit(
        Math.min(this.durationMs(), Math.max(ms, this.startMs() + this.minLengthMs)),
      );
    }
  }

  onKey(e: KeyboardEvent, which: 'start' | 'end'): void {
    const step = e.shiftKey ? 1000 : FRAME_MS;
    const cur = which === 'start' ? this.startMs() : this.endMs();
    if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') this.moveTo(which, cur - step);
    else if (e.key === 'ArrowRight' || e.key === 'ArrowUp') this.moveTo(which, cur + step);
    else return;
    e.preventDefault();
  }

  private context(el: HTMLCanvasElement): CanvasRenderingContext2D | null {
    try {
      return el.getContext('2d');
    } catch {
      return null; // no canvas (a test environment): the timeline still works without the picture of the sound
    }
  }

  private draw(): void {
    const el = this.wave?.()?.nativeElement;
    if (!el) return;
    const ctx = this.context(el);
    if (!ctx) return;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.max(1, Math.floor(el.clientWidth * dpr));
    const h = Math.max(1, Math.floor(el.clientHeight * dpr));
    el.width = w;
    el.height = h;
    ctx.clearRect(0, 0, w, h);
    const peaks = this.peaks();
    if (!peaks.length) return;
    ctx.fillStyle = getComputedStyle(el).color || '#71717a';
    ctx.globalAlpha = 0.55;
    for (let x = 0; x < w; x++) {
      const [lo, hi] = peaks[Math.min(peaks.length - 1, Math.floor((x / w) * peaks.length))];
      const top = h / 2 - (Math.max(hi, 0) / 127) * (h / 2);
      const bottom = h / 2 - (Math.min(lo, 0) / 127) * (h / 2);
      ctx.fillRect(x, top, 1, Math.max(1, bottom - top));
    }
  }
}
