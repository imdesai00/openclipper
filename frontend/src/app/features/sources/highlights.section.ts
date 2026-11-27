import {
  Component,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';

import { errorMessage } from '../../core/api';
import { formatDuration } from '../../core/format';
import { ClipsApi } from '../../core/clips-api.service';
import { HighlightsApi } from '../../core/highlights-api.service';
import {
  HIGHLIGHT_CATEGORIES,
  HighlightCandidate,
  HighlightRun,
  SourceDetail,
  STAGE_LABELS,
} from '../../core/models';
import { StatusBadgeComponent } from '../../shared/status-badge.component';

const DROP_REASONS: Record<string, string> = {
  unknown_segment: 'named a part of the video it was never shown',
  reversed: 'ended before it started',
  no_words: 'pointed at a part with no words',
  too_long: 'could not be trimmed to the maximum length',
  too_short: 'could not be stretched to the minimum length',
  overlaps_stronger: 'overlapped a stronger clip',
};

const PART_LABELS: Record<string, string> = {
  llm: 'Model’s own score',
  category: 'Matches the categories you asked for',
  standalone: 'Makes sense on its own',
  density: 'Speech density',
  scene: 'Starts and ends on scene changes',
  energy: 'Audio energy',
};

@Component({
  selector: 'app-highlights-section',
  imports: [FormsModule, StatusBadgeComponent],
  template: `
    <section class="card" aria-labelledby="highlights-h">
      <h2 id="highlights-h" class="text-base font-semibold">Highlights</h2>
      <p class="mt-1 text-sm text-zinc-700 dark:text-zinc-300">
        The AI suggests short clips. Each one is a stretch of the real transcript, so what you read
        is exactly what was said.
      </p>

      @if (error()) {
        <p class="alert-error mt-3" role="alert">{{ error() }}</p>
      }

      @if (!source().transcript) {
        <p class="mt-3 text-sm text-zinc-700 dark:text-zinc-300">
          Transcribe this video first; highlights are found in its transcript.
        </p>
      } @else {
        @if (running()) {
          <div class="mt-4" role="status" aria-live="polite">
            <p class="text-sm">Looking for highlights. A local model can take a few minutes.</p>
            <ol class="mt-3 space-y-2">
              @for (st of jobStages(); track st.stage) {
                <li class="text-sm">
                  <div class="flex items-center justify-between gap-2">
                    <span>{{ stageLabel(st.stage) }}</span>
                    <app-status-badge [status]="st.status" />
                  </div>
                  @if (st.status === 'running') {
                    <div
                      class="mt-1 h-1.5 overflow-hidden rounded bg-zinc-200 dark:bg-zinc-800"
                      role="progressbar"
                      [attr.aria-valuenow]="(st.progress * 100).toFixed(0)"
                      aria-valuemin="0"
                      aria-valuemax="100"
                      [attr.aria-label]="stageLabel(st.stage)"
                    >
                      <div class="h-full bg-accent" [style.width.%]="st.progress * 100"></div>
                    </div>
                    @if (st.message) {
                      <p class="mt-1 text-xs text-zinc-600 dark:text-zinc-400">{{ st.message }}</p>
                    }
                  }
                </li>
              }
            </ol>
          </div>
        } @else {
          @if (failedJob(); as j) {
            <p class="alert-error mt-4" role="alert">
              Finding highlights failed: {{ j.error_message ?? 'unknown error' }}
            </p>
          }
          @if (canWrite() && source().status === 'ready') {
            <form class="mt-4 space-y-3" (ngSubmit)="start()">
              <div>
                <label class="label" for="hl-prompt">What should it look for? (optional)</label>
                <textarea
                  id="hl-prompt"
                  class="input"
                  rows="2"
                  maxlength="1000"
                  name="prompt"
                  placeholder="For example: the most useful advice, in 30 to 60 seconds each"
                  [(ngModel)]="prompt"
                ></textarea>
              </div>
              <div class="grid grid-cols-3 gap-3">
                <div>
                  <label class="label" for="hl-n">How many</label>
                  <input
                    id="hl-n"
                    class="input"
                    type="number"
                    min="1"
                    max="30"
                    name="n"
                    [(ngModel)]="count"
                  />
                </div>
                <div>
                  <label class="label" for="hl-min">Shortest (s)</label>
                  <input
                    id="hl-min"
                    class="input"
                    type="number"
                    min="5"
                    max="600"
                    name="min"
                    [(ngModel)]="minS"
                  />
                </div>
                <div>
                  <label class="label" for="hl-max">Longest (s)</label>
                  <input
                    id="hl-max"
                    class="input"
                    type="number"
                    min="5"
                    max="600"
                    name="max"
                    [(ngModel)]="maxS"
                  />
                </div>
              </div>
              <details class="text-sm">
                <summary class="cursor-pointer">Only these kinds of moments…</summary>
                <fieldset class="mt-2 grid grid-cols-2 gap-1">
                  <legend class="sr-only">Categories</legend>
                  @for (c of categories; track c.key) {
                    <label class="flex items-center gap-2">
                      <input
                        type="checkbox"
                        class="size-4"
                        [checked]="picked().includes(c.key)"
                        (change)="toggle(c.key)"
                      />
                      {{ c.label }}
                    </label>
                  }
                </fieldset>
              </details>
              <button type="submit" class="btn btn-primary" [disabled]="busy()">
                Find highlights
              </button>
            </form>
          } @else if (!source().highlights) {
            <p class="mt-3 text-sm text-zinc-700 dark:text-zinc-300">No highlights yet.</p>
          }

          @if (run(); as r) {
            <div class="mt-6">
              <h3 class="text-sm font-semibold">
                {{ r.candidates.length }} suggested
                {{ r.candidates.length === 1 ? 'clip' : 'clips' }}
              </h3>
              <p class="mt-1 text-xs text-zinc-600 dark:text-zinc-400">{{ r.score_note }}</p>
              @if (r.run.stats.windows_failed.length) {
                <p class="alert-error mt-2" role="alert">
                  {{ r.run.stats.windows_failed.length }} of {{ r.run.stats.windows }} parts of the
                  video could not be analysed, so clips there may be missing.
                </p>
              }
              @if (dropNotes().length) {
                <p class="mt-2 text-xs text-zinc-600 dark:text-zinc-400">
                  The model suggested more, but these were left out:
                  {{ dropNotes().join('; ') }}.
                </p>
              }
              @if (!r.candidates.length) {
                <p class="mt-3 text-sm">
                  Nothing usable came back. Try a different request, or a larger model.
                </p>
              }
              <ol class="mt-4 space-y-4" aria-label="Suggested clips">
                @for (c of r.candidates; track c.id) {
                  <li
                    class="rounded-lg border border-zinc-200 p-4 dark:border-zinc-800"
                    [attr.aria-label]="'Clip ' + c.rank + ': ' + c.title"
                  >
                    <div class="flex flex-wrap items-start justify-between gap-2">
                      <div class="min-w-0">
                        <h4 class="font-semibold">{{ c.rank }}. {{ c.title }}</h4>
                        <p class="text-sm text-zinc-600 dark:text-zinc-400">
                          {{ time(c.start_ms) }}–{{ time(c.end_ms) }} ·
                          {{ (c.duration_ms / 1000).toFixed(0) }} s ·
                          <span class="badge">{{ category(c.category) }}</span>
                          @if (c.status !== 'new') {
                            <span class="badge ml-1">{{
                              c.status === 'accepted' ? 'Accepted' : 'Rejected'
                            }}</span>
                          }
                        </p>
                      </div>
                      <p class="text-right text-sm">
                        <strong class="text-lg">{{ c.final_score.toFixed(0) }}</strong>/100<br />
                        <span class="text-xs text-zinc-600 dark:text-zinc-400"
                          >AI relevance score</span
                        >
                      </p>
                    </div>
                    <p class="mt-2 text-sm italic">“{{ c.hook }}”</p>
                    <blockquote
                      class="mt-2 border-l-2 border-zinc-300 pl-3 text-sm dark:border-zinc-700"
                    >
                      {{ c.text }}
                    </blockquote>
                    <p class="mt-2 text-sm">{{ c.explanation }}</p>
                    <details class="mt-2 text-xs text-zinc-600 dark:text-zinc-400">
                      <summary class="cursor-pointer">How this score was made</summary>
                      <ul class="mt-1 space-y-0.5">
                        @for (p of parts(c); track p.key) {
                          <li>
                            {{ p.label }}: {{ p.value.toFixed(0) }} (weight
                            {{ (p.weight * 100).toFixed(0) }}%)
                          </li>
                        }
                      </ul>
                      <p class="mt-1">
                        The model said {{ c.llm_score }} for relevance, {{ c.standalone_score }} for
                        making sense on its own, and {{ c.confidence }} for its own confidence. Made
                        by {{ c.model_name }} ({{ c.provider_name }}). The weights are rules of
                        thumb, not measured against real results.
                      </p>
                    </details>
                    <div class="mt-3 flex flex-wrap gap-2">
                      <button
                        type="button"
                        class="btn btn-secondary"
                        [attr.aria-label]="'Preview clip ' + c.rank"
                        (click)="preview.emit({ start: c.start_ms / 1000, end: c.end_ms / 1000 })"
                      >
                        Preview
                      </button>
                      @if (canWrite()) {
                        <button
                          type="button"
                          class="btn btn-primary"
                          [attr.aria-label]="'Open clip ' + c.rank + ' in the editor'"
                          [disabled]="busy()"
                          (click)="openInEditor(c)"
                        >
                          Open in editor
                        </button>
                        <button
                          type="button"
                          class="btn btn-secondary"
                          [attr.aria-pressed]="c.status === 'accepted'"
                          [attr.aria-label]="'Accept clip ' + c.rank"
                          [disabled]="busy()"
                          (click)="review(c, c.status === 'accepted' ? 'new' : 'accepted')"
                        >
                          Accept
                        </button>
                        <button
                          type="button"
                          class="btn btn-secondary"
                          [attr.aria-pressed]="c.status === 'rejected'"
                          [attr.aria-label]="'Reject clip ' + c.rank"
                          [disabled]="busy()"
                          (click)="review(c, c.status === 'rejected' ? 'new' : 'rejected')"
                        >
                          Reject
                        </button>
                      }
                    </div>
                  </li>
                }
              </ol>
            </div>
          }
        }
      }
    </section>
  `,
})
export class HighlightsSection {
  readonly source = input.required<SourceDetail>();
  readonly canWrite = input(false);
  /** Ask the page to play this stretch (seconds). */
  readonly preview = output<{ start: number; end: number }>();
  /** A job was started: the page should reload the source. */
  readonly changed = output<void>();

  private readonly api = inject(HighlightsApi);
  private readonly clipsApi = inject(ClipsApi);
  private readonly router = inject(Router);

  readonly run = signal<HighlightRun | null>(null);
  readonly picked = signal<string[]>([]);
  readonly busy = signal(false);
  readonly error = signal('');
  prompt = '';
  count = 8;
  minS = 20;
  maxS = 90;

  readonly categories = Object.entries(HIGHLIGHT_CATEGORIES).map(([key, label]) => ({
    key,
    label,
  }));
  readonly time = (ms: number): string => formatDuration(ms / 1000);
  readonly stageLabel = (s: string): string => STAGE_LABELS[s] ?? s;
  readonly category = (k: string): string => HIGHLIGHT_CATEGORIES[k] ?? k;

  readonly job = computed(() => this.source().highlights_job);
  readonly running = computed(() => {
    const s = this.job()?.status;
    return s === 'queued' || s === 'running';
  });
  readonly failedJob = computed(() => {
    const j = this.job();
    return j && (j.status === 'failed' || j.status === 'cancelled') ? j : null;
  });
  readonly jobStages = computed(() => this.job()?.stages ?? []);
  readonly dropNotes = computed(() => {
    const d = this.run()?.run.stats.dropped ?? {};
    return Object.entries(d)
      .filter(([, n]) => n > 0)
      .map(([k, n]) => `${n} ${DROP_REASONS[k] ?? k}`);
  });

  constructor() {
    // Whenever the newest run changes (a job finished), show it.
    effect(() => {
      const id = this.source().highlights?.run_id ?? null;
      untracked(() => void this.load(id));
    });
  }

  private async load(runId: string | null): Promise<void> {
    if (!runId) {
      this.run.set(null);
      return;
    }
    if (this.run()?.run.id === runId) return;
    try {
      this.run.set(await this.api.run(this.source().id, runId));
      this.error.set('');
    } catch (e) {
      this.error.set(errorMessage(e));
    }
  }

  parts(c: HighlightCandidate): { key: string; label: string; value: number; weight: number }[] {
    return Object.entries(c.score_parts)
      .filter(([k]) => k !== 'unavailable')
      .map(([k, v]) => {
        const p = v as { value: number; weight: number };
        return { key: k, label: PART_LABELS[k] ?? k, value: p.value, weight: p.weight };
      });
  }

  toggle(key: string): void {
    this.picked.update((p) => (p.includes(key) ? p.filter((k) => k !== key) : [...p, key]));
  }

  async start(): Promise<void> {
    this.busy.set(true);
    this.error.set('');
    try {
      const r = await this.api.find(this.source().id, {
        prompt: this.prompt.trim(),
        categories: this.picked(),
        max_candidates: Number(this.count),
        min_seconds: Number(this.minS),
        max_seconds: Number(this.maxS),
      });
      if (r.reused && r.run) await this.load(r.run.id); // the same request was answered before: no new job
      this.changed.emit();
    } catch (e) {
      this.error.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }

  /** Turn a suggestion into a clip (asking twice opens the same one) and go and edit it. */
  async openInEditor(c: HighlightCandidate): Promise<void> {
    this.busy.set(true);
    this.error.set('');
    try {
      const clip = await this.clipsApi.fromHighlight(c.id);
      await this.router.navigate(['/clips', clip.id]);
    } catch (e) {
      this.error.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }

  async review(c: HighlightCandidate, status: 'new' | 'accepted' | 'rejected'): Promise<void> {
    this.busy.set(true);
    this.error.set('');
    try {
      const updated = await this.api.review(c.id, status);
      this.run.update((r) =>
        r ? { ...r, candidates: r.candidates.map((x) => (x.id === c.id ? updated : x)) } : r,
      );
    } catch (e) {
      this.error.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }
}
