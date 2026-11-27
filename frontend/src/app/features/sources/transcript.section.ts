import {
  Component,
  computed,
  effect,
  inject,
  input,
  OnInit,
  output,
  signal,
  untracked,
} from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { FormsModule } from '@angular/forms';

import { apiError, errorMessage } from '../../core/api';
import { formatDuration } from '../../core/format';
import {
  AsrStatus,
  SourceDetail,
  STAGE_LABELS,
  TranscriptMeta,
  TranscriptSegment,
} from '../../core/models';
import { TranscriptApi } from '../../core/transcript-api.service';
import { StatusBadgeComponent } from '../../shared/status-badge.component';

/** Human name of a language code, in the viewer's own language (the browser knows them; we do not hardcode 100). */
export function languageName(code: string | null): string {
  if (!code) return 'Unknown';
  try {
    return (
      new Intl.DisplayNames([navigator.language || 'en'], { type: 'language' }).of(code) ?? code
    );
  } catch {
    return code;
  }
}

@Component({
  selector: 'app-transcript-section',
  imports: [FormsModule, NgTemplateOutlet, StatusBadgeComponent],
  template: `
    <section class="card" aria-labelledby="transcript-h">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <h2 id="transcript-h" class="text-base font-semibold">Transcript</h2>
        @if (meta(); as m) {
          <p class="text-sm text-zinc-600 dark:text-zinc-400">
            {{ language(m.language) }} · {{ m.word_count }} words · version {{ m.version }}
            @if (m.kind !== 'asr') {
              ({{ m.kind === 'edit' ? 'edited' : 'restored' }})
            }
          </p>
        }
      </div>

      @if (error()) {
        <p class="alert-error mt-3" role="alert">
          {{ error() }}
          @if (conflict()) {
            <button type="button" class="ml-2 underline" (click)="reload()">Show the latest</button>
          }
        </p>
      }

      @if (running()) {
        <div class="mt-4" role="status" aria-live="polite">
          <p class="text-sm">
            Transcribing. This can take a while on a CPU; you can leave this page.
          </p>
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
      } @else if (!meta()) {
        @if (failedJob(); as j) {
          <p class="alert-error mt-4" role="alert">
            Transcription failed: {{ j.error_message ?? 'unknown error' }}
          </p>
        }
        @if (source().has_audio === false) {
          <p class="mt-3 text-sm text-zinc-700 dark:text-zinc-300">
            This file has no sound, so there is nothing to transcribe.
          </p>
        } @else if (source().status !== 'ready') {
          <p class="mt-3 text-sm text-zinc-700 dark:text-zinc-300">
            You can transcribe once the file has finished processing.
          </p>
        } @else if (canWrite()) {
          <ng-container *ngTemplateOutlet="form" />
        } @else {
          <p class="mt-3 text-sm text-zinc-700 dark:text-zinc-300">
            This video has no transcript yet.
          </p>
        }
      } @else {
        <div class="mt-3 flex flex-wrap gap-2">
          @if (canWrite()) {
            <button
              type="button"
              class="btn btn-secondary"
              [attr.aria-pressed]="editMode()"
              (click)="editMode.set(!editMode())"
            >
              {{ editMode() ? 'Done editing' : 'Edit text' }}
            </button>
          }
          <button type="button" class="btn btn-secondary" (click)="download('txt')">
            Export TXT
          </button>
          <button type="button" class="btn btn-secondary" (click)="download('srt')">
            Export SRT
          </button>
          <button type="button" class="btn btn-secondary" (click)="download('vtt')">
            Export VTT
          </button>
          <button
            type="button"
            class="btn btn-secondary"
            [attr.aria-expanded]="historyOpen()"
            (click)="toggleHistory()"
          >
            History
          </button>
        </div>

        @if (historyOpen()) {
          <ul
            class="mt-3 divide-y divide-zinc-200 text-sm dark:divide-zinc-800"
            aria-label="Versions"
          >
            @for (v of versions(); track v.id) {
              <li class="flex flex-wrap items-center justify-between gap-2 py-2">
                <span>
                  Version {{ v.version }} · {{ kindLabel(v) }} ·
                  {{ v.created_at.slice(0, 16).replace('T', ' ') }}
                  @if (v.is_current) {
                    <strong> (current)</strong>
                  }
                </span>
                @if (canWrite() && !v.is_current) {
                  <button type="button" class="btn btn-secondary" (click)="restore(v.version)">
                    Restore this version
                  </button>
                }
              </li>
            }
          </ul>
        }

        <ol
          class="mt-4 max-h-[32rem] space-y-1 overflow-y-auto pr-1"
          aria-label="Transcript segments"
        >
          @for (seg of segments(); track seg.idx) {
            <li
              class="flex gap-3 rounded-md p-2"
              [class.bg-indigo-50]="seg.idx === activeIdx()"
              [class.dark:bg-indigo-950]="seg.idx === activeIdx()"
              [attr.aria-current]="seg.idx === activeIdx() ? 'true' : null"
            >
              <button
                type="button"
                class="shrink-0 font-mono text-xs text-zinc-600 underline dark:text-zinc-400"
                [attr.aria-label]="'Play from ' + time(seg.start_ms)"
                (click)="seek.emit(seg.start_ms / 1000)"
              >
                {{ time(seg.start_ms) }}
              </button>
              @if (editingIdx() === seg.idx) {
                <div class="min-w-0 flex-1">
                  <label class="sr-only" [for]="'seg-' + seg.idx"
                    >Text of segment at {{ time(seg.start_ms) }}</label
                  >
                  <textarea
                    [id]="'seg-' + seg.idx"
                    class="input"
                    rows="3"
                    maxlength="2000"
                    [ngModel]="draft()"
                    (ngModelChange)="draft.set($event)"
                  ></textarea>
                  <div class="mt-2 flex gap-2">
                    <button
                      type="button"
                      class="btn btn-primary"
                      [disabled]="busy() || !draft().trim()"
                      (click)="saveEdit(seg)"
                    >
                      Save
                    </button>
                    <button type="button" class="btn btn-secondary" (click)="editingIdx.set(null)">
                      Cancel
                    </button>
                  </div>
                </div>
              } @else {
                <p class="min-w-0 flex-1 text-sm">
                  {{ seg.text.trim() }}
                  @if (seg.edited) {
                    <span class="badge ml-1">edited</span>
                  }
                  @if (editMode()) {
                    <button
                      type="button"
                      class="ml-2 text-xs underline"
                      [attr.aria-label]="'Edit segment at ' + time(seg.start_ms)"
                      (click)="startEdit(seg)"
                    >
                      Edit
                    </button>
                  }
                </p>
              }
            </li>
          }
        </ol>
        @if (segments().length < (meta()?.segment_count ?? 0)) {
          <button
            type="button"
            class="btn btn-secondary mt-3"
            [disabled]="busy()"
            (click)="loadMore()"
          >
            Show more
          </button>
        }

        @if (canWrite() && source().status === 'ready') {
          <details class="mt-4 text-sm">
            <summary class="cursor-pointer">Transcribe again…</summary>
            <p class="mt-2 text-zinc-700 dark:text-zinc-300">
              Choose another model or language. Your edits stay in the history.
            </p>
            <ng-container *ngTemplateOutlet="form" />
          </details>
        }
      }
    </section>

    <ng-template #form>
      <form class="mt-3 space-y-3" (ngSubmit)="start(false)">
        @if (asr(); as a) {
          @if (!a.available) {
            <p class="alert-error" role="alert">
              The transcription service is not running. Start it with
              <code class="font-mono">docker compose up -d worker-asr</code>.
            </p>
          }
          <div class="grid gap-3 sm:grid-cols-2">
            <div>
              <label class="label" for="asr-model">Speech model</label>
              <select id="asr-model" class="input" name="model" [(ngModel)]="model">
                @for (m of a.models; track m.name) {
                  <option [value]="m.name">
                    {{ m.name }} ({{ m.mb }} MB{{ m.downloaded ? '' : ', downloads on first use' }})
                  </option>
                }
              </select>
            </div>
            <div>
              <label class="label" for="asr-lang">Spoken language</label>
              <select id="asr-lang" class="input" name="language" [(ngModel)]="lang">
                <option value="">Detect automatically</option>
                @for (c of a.languages; track c) {
                  <option [value]="c">{{ language(c) }}</option>
                }
              </select>
            </div>
          </div>
          <button type="submit" class="btn btn-primary" [disabled]="busy() || !a.available">
            Transcribe
          </button>
        } @else {
          <p class="text-sm text-zinc-600 dark:text-zinc-400">Loading…</p>
        }
      </form>
    </ng-template>
  `,
})
export class TranscriptSection implements OnInit {
  readonly source = input.required<SourceDetail>();
  readonly canWrite = input(false);
  /** Playback position of the player, in seconds. */
  readonly currentS = input(0);
  readonly seek = output<number>();
  /** Something changed on the server (a job was started, a version was added): the page should reload the source. */
  readonly changed = output<void>();

  private readonly api = inject(TranscriptApi);

  readonly asr = signal<AsrStatus | null>(null);
  readonly meta = signal<TranscriptMeta | null>(null);
  readonly segments = signal<TranscriptSegment[]>([]);
  readonly versions = signal<TranscriptMeta[]>([]);
  readonly historyOpen = signal(false);
  readonly editMode = signal(false);
  readonly editingIdx = signal<number | null>(null);
  readonly draft = signal('');
  readonly busy = signal(false);
  readonly error = signal('');
  readonly conflict = signal(false);
  model = '';
  lang = '';

  readonly language = languageName;
  readonly time = (ms: number): string => formatDuration(ms / 1000);
  readonly stageLabel = (s: string): string => STAGE_LABELS[s] ?? s;
  readonly kindLabel = (v: TranscriptMeta): string =>
    v.kind === 'asr' ? 'recognised' : v.kind === 'edit' ? 'edited' : 'restored';

  readonly job = computed(() => this.source().transcription_job);
  readonly running = computed(() => {
    const s = this.job()?.status;
    return s === 'queued' || s === 'running';
  });
  readonly failedJob = computed(() => {
    const j = this.job();
    return j && (j.status === 'failed' || j.status === 'cancelled') ? j : null;
  });
  readonly jobStages = computed(() => this.job()?.stages ?? []);
  readonly activeIdx = computed(() => {
    const ms = this.currentS() * 1000;
    let found = -1;
    for (const s of this.segments()) {
      if (s.start_ms <= ms && ms <= s.end_ms + 300) found = s.idx;
    }
    return found;
  });

  constructor() {
    // Whenever the server's current version changes (a finished job, an edit, a restore), show it.
    effect(() => {
      const version = this.source().transcript?.version ?? null;
      untracked(() => void this.sync(version));
    });
  }

  async ngOnInit(): Promise<void> {
    try {
      const a = await this.api.asrStatus();
      this.asr.set(a);
      this.model = a.default_model;
      this.lang = a.default_language ?? '';
    } catch {
      /* the form simply stays on "Loading" until the user reloads; transcripts still display */
    }
  }

  private async sync(version: number | null): Promise<void> {
    if (version === null) {
      this.meta.set(null);
      this.segments.set([]);
      return;
    }
    if (this.meta()?.version === version) return;
    await this.fetch(0);
  }

  private async fetch(offset: number): Promise<void> {
    try {
      const p = await this.api.get(this.source().id, offset);
      this.meta.set(p.transcript);
      this.segments.update((cur) => (offset === 0 ? p.segments : [...cur, ...p.segments]));
      this.error.set('');
    } catch (e) {
      this.error.set(errorMessage(e));
    }
  }

  loadMore(): Promise<void> {
    return this.fetch(this.segments().length);
  }

  async reload(): Promise<void> {
    this.conflict.set(false);
    this.error.set('');
    this.editingIdx.set(null);
    this.changed.emit();
    await this.fetch(0);
  }

  private async act(fn: () => Promise<unknown>): Promise<boolean> {
    this.busy.set(true);
    this.error.set('');
    this.conflict.set(false);
    try {
      await fn();
      return true;
    } catch (e) {
      this.error.set(errorMessage(e));
      return false;
    } finally {
      this.busy.set(false);
    }
  }

  async start(force: boolean): Promise<void> {
    const ok = await this.act(() =>
      this.api.transcribe(this.source().id, {
        language: this.lang || null,
        model: this.model || null,
        force,
      }),
    );
    if (ok) this.changed.emit();
  }

  startEdit(seg: TranscriptSegment): void {
    this.editingIdx.set(seg.idx);
    this.draft.set(seg.text.trim());
  }

  async saveEdit(seg: TranscriptSegment): Promise<void> {
    const m = this.meta();
    if (!m) return;
    const text = this.draft().trim();
    const idx = seg.idx;
    let failedConflict = false;
    const ok = await this.act(async () => {
      try {
        await this.api.edit(this.source().id, m.version, [{ segment_idx: idx, text }]);
      } catch (e) {
        failedConflict = apiError(e)?.code === 'version_conflict';
        throw e;
      }
    });
    if (ok) {
      this.editingIdx.set(null);
      this.changed.emit();
    } else if (failedConflict) {
      this.conflict.set(true);
    }
  }

  async toggleHistory(): Promise<void> {
    const open = !this.historyOpen();
    this.historyOpen.set(open);
    if (open)
      await this.act(async () => this.versions.set(await this.api.versions(this.source().id)));
  }

  async restore(version: number): Promise<void> {
    const ok = await this.act(() => this.api.restore(this.source().id, version));
    if (ok) {
      this.historyOpen.set(false);
      this.changed.emit();
    }
  }

  download(format: 'txt' | 'srt' | 'vtt'): Promise<boolean> {
    return this.act(() => this.api.exportFile(this.source().id, format));
  }
}
