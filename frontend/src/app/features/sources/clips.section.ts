import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { errorMessage } from '../../core/api';
import { ClipsApi } from '../../core/clips-api.service';
import { ClipSummary, SourceDetail } from '../../core/models';
import { formatMs, parseTime } from '../clips/clip-math';

@Component({
  selector: 'app-clips-section',
  imports: [FormsModule, RouterLink],
  template: `
    <section class="card" aria-labelledby="clips-h">
      <h2 id="clips-h" class="text-base font-semibold">Clips</h2>
      <p class="mt-1 text-sm text-zinc-700 dark:text-zinc-300">
        A clip is a piece of this video, cut, reframed and captioned. Open one from a highlight
        above, or make one from any start and end time.
      </p>
      @if (error()) {
        <p class="alert-error mt-3" role="alert">{{ error() }}</p>
      }
      @if (canWrite() && source().status === 'ready') {
        <form class="mt-3 flex flex-wrap items-end gap-3" (ngSubmit)="create()">
          <div>
            <label class="label" for="nc-start">Starts at</label>
            <input id="nc-start" class="input w-28 font-mono" name="start" [(ngModel)]="start" />
          </div>
          <div>
            <label class="label" for="nc-end">Ends at</label>
            <input id="nc-end" class="input w-28 font-mono" name="end" [(ngModel)]="end" />
          </div>
          <button type="submit" class="btn btn-primary" [disabled]="busy()">Make a clip</button>
        </form>
      }
      @if (clips().length) {
        <ul class="mt-4 grid gap-3 sm:grid-cols-2" aria-label="Clips">
          @for (c of clips(); track c.id) {
            <li class="flex gap-3 rounded-md border border-zinc-200 p-3 dark:border-zinc-800">
              @if (c.thumbnail_url) {
                <img
                  [src]="c.thumbnail_url"
                  alt=""
                  class="h-20 w-12 rounded object-cover"
                  loading="lazy"
                />
              }
              <div class="min-w-0">
                <a [routerLink]="['/clips', c.id]" class="font-semibold underline">{{ c.title }}</a>
                <p class="text-xs text-zinc-600 dark:text-zinc-400">
                  {{ fmt(c.start_ms) }}–{{ fmt(c.end_ms) }} · {{ c.aspect }} · version
                  {{ c.current_version }}
                </p>
                <p class="text-xs">{{ state(c) }}</p>
              </div>
            </li>
          }
        </ul>
      } @else if (loaded()) {
        <p class="mt-3 text-sm text-zinc-700 dark:text-zinc-300">No clips yet.</p>
      }
    </section>
  `,
})
export class ClipsSection {
  readonly source = input.required<SourceDetail>();
  readonly canWrite = input(false);

  private readonly api = inject(ClipsApi);
  private readonly router = inject(Router);

  readonly clips = signal<ClipSummary[]>([]);
  readonly loaded = signal(false);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly fmt = formatMs;
  start = '0:00';
  end = '0:30';
  readonly sourceId = computed(() => this.source().id);

  constructor() {
    effect(() => {
      this.sourceId();
      untracked(() => void this.load());
    });
  }

  private async load(): Promise<void> {
    try {
      this.clips.set(await this.api.list(this.source().id));
    } catch (e) {
      this.error.set(errorMessage(e));
    } finally {
      this.loaded.set(true);
    }
  }

  state(c: ClipSummary): string {
    const s = c.best_render_state;
    if (s === 'pass') return 'Rendered';
    if (s === 'warn') return 'Rendered, with warnings to look at';
    if (s === 'fail') return 'Rendered, but it failed the quality checks';
    return s ? 'Rendering…' : 'Not rendered yet';
  }

  async create(): Promise<void> {
    const a = parseTime(this.start);
    const b = parseTime(this.end);
    if (a === null || b === null || b <= a) {
      this.error.set('Type the start and the end like 1:23.4, with the end after the start.');
      return;
    }
    this.busy.set(true);
    this.error.set('');
    try {
      const clip = await this.api.create(this.source().id, a, b);
      await this.router.navigate(['/clips', clip.id]);
    } catch (e) {
      this.error.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }
}
