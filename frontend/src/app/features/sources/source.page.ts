import {
  Component,
  DestroyRef,
  ElementRef,
  OnInit,
  computed,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { Router, RouterLink } from '@angular/router';

import { errorMessage } from '../../core/api';
import { AuthService } from '../../core/auth.service';
import { formatBytes, formatDuration } from '../../core/format';
import { MediaApi } from '../../core/media-api.service';
import { Project, RealtimeEvent, STAGE_LABELS, SourceDetail, Stage } from '../../core/models';
import { RealtimeService } from '../../core/realtime.service';
import { ConfirmDialogComponent } from '../../shared/confirm-dialog.component';
import { StatusBadgeComponent } from '../../shared/status-badge.component';
import { HighlightsSection } from './highlights.section';
import { TranscriptSection } from './transcript.section';

@Component({
  selector: 'app-source-page',
  imports: [
    RouterLink,
    StatusBadgeComponent,
    ConfirmDialogComponent,
    TranscriptSection,
    HighlightsSection,
  ],
  template: `
    @if (loadError()) {
      <p class="alert-error" role="alert">{{ loadError() }}</p>
      <a routerLink="/projects" class="mt-3 inline-block text-sm underline">Back to projects</a>
    } @else if (src(); as s) {
      <a
        [routerLink]="['/projects', s.project_id]"
        class="text-sm text-zinc-600 underline dark:text-zinc-400"
      >
        {{ project()?.name ?? 'Project' }}
      </a>
      <div class="mt-1 flex flex-wrap items-center gap-3">
        <h1 class="min-w-0 break-words text-2xl font-semibold tracking-tight">{{ s.title }}</h1>
        <app-status-badge [status]="s.status" />
      </div>

      <div class="mt-6 grid gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <section [attr.aria-label]="s.has_video ? 'Video' : 'Audio'">
          @if (s.proxy_url && s.has_video) {
            <video
              #player
              class="w-full rounded-lg bg-black"
              controls
              preload="metadata"
              [src]="s.proxy_url"
              (error)="onMediaError()"
              (timeupdate)="onTime($event)"
            >
              Your browser cannot play this video.
            </video>
            <p class="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
              This is a lightweight copy for editing. Your original file is kept unchanged.
            </p>
          } @else if (s.proxy_url) {
            <div class="card">
              <p class="mb-3 text-sm text-zinc-700 dark:text-zinc-300">
                Audio only: this file has no picture.
              </p>
              <audio
                #player
                class="w-full"
                controls
                preload="metadata"
                [src]="s.proxy_url"
                (error)="onMediaError()"
                (timeupdate)="onTime($event)"
              >
                Your browser cannot play this audio.
              </audio>
            </div>
          } @else if (s.status === 'failed') {
            <div class="card">
              <h2 class="text-base font-semibold">This video could not be processed</h2>
              <p class="alert-error mt-2" role="alert">{{ s.error_message ?? 'Unknown error' }}</p>
            </div>
          } @else {
            <div class="card text-sm text-zinc-600 dark:text-zinc-400">
              The video will appear here when processing finishes.
            </div>
          }
        </section>

        <aside class="space-y-4">
          <section class="card" aria-labelledby="steps">
            <h2 id="steps" class="text-base font-semibold">Processing</h2>
            <ol class="mt-3 space-y-3">
              @for (st of stages(); track st.stage) {
                <li>
                  <div class="flex items-center justify-between gap-2 text-sm">
                    <span>{{ label(st.stage) }}</span>
                    <app-status-badge [status]="st.status" />
                  </div>
                  @if (st.status === 'running') {
                    <div
                      class="mt-1 h-1.5 overflow-hidden rounded bg-zinc-200 dark:bg-zinc-800"
                      role="progressbar"
                      [attr.aria-valuenow]="(st.progress * 100).toFixed(0)"
                      aria-valuemin="0"
                      aria-valuemax="100"
                      [attr.aria-label]="label(st.stage)"
                    >
                      <div class="h-full bg-accent" [style.width.%]="st.progress * 100"></div>
                    </div>
                  }
                  @if (st.status === 'failed' && st.error_message) {
                    <p class="mt-1 text-sm text-red-800 dark:text-red-300">
                      {{ st.error_message }}
                    </p>
                  }
                </li>
              }
            </ol>
            @if (actionError()) {
              <p class="alert-error mt-3" role="alert">{{ actionError() }}</p>
            }
            @if (canWrite()) {
              <div class="mt-4 flex flex-wrap gap-2">
                @if (s.job && (s.job.status === 'failed' || s.job.status === 'cancelled')) {
                  <button
                    type="button"
                    class="btn btn-primary"
                    [disabled]="busy()"
                    (click)="retry()"
                  >
                    Retry
                  </button>
                }
                @if (s.job && (s.job.status === 'running' || s.job.status === 'queued')) {
                  <button
                    type="button"
                    class="btn btn-secondary"
                    [disabled]="busy()"
                    (click)="cancel()"
                  >
                    Cancel
                  </button>
                }
                @if (s.status === 'ready') {
                  <button
                    type="button"
                    class="btn btn-secondary"
                    [disabled]="busy()"
                    (click)="reprocess()"
                  >
                    Process again
                  </button>
                }
                <button
                  type="button"
                  class="btn btn-secondary"
                  [disabled]="busy()"
                  (click)="deleteDlg().open()"
                >
                  Delete
                </button>
              </div>
            }
          </section>

          <section class="card" aria-labelledby="facts">
            <h2 id="facts" class="text-base font-semibold">Details</h2>
            <dl class="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
              <dt class="text-zinc-600 dark:text-zinc-400">Length</dt>
              <dd>{{ duration(s.duration_s) }}</dd>
              <dt class="text-zinc-600 dark:text-zinc-400">Size</dt>
              <dd>{{ bytes(s.size_bytes) }}</dd>
              <dt class="text-zinc-600 dark:text-zinc-400">Resolution</dt>
              <dd>{{ s.width ? s.width + ' × ' + s.height : '—' }}</dd>
              <dt class="text-zinc-600 dark:text-zinc-400">Video</dt>
              <dd>{{ s.has_video ? (s.video_codec ?? '—') : 'None (audio only)' }}</dd>
              <dt class="text-zinc-600 dark:text-zinc-400">Audio</dt>
              <dd>{{ s.has_audio === false ? 'None' : (s.audio_codec ?? '—') }}</dd>
              <dt class="text-zinc-600 dark:text-zinc-400">Source</dt>
              <dd class="break-all">
                {{ s.kind === 'upload' ? 'Uploaded file' : (s.origin_url ?? 'Link') }}
              </dd>
              @if (s.media_hash) {
                <dt class="text-zinc-600 dark:text-zinc-400">Fingerprint</dt>
                <dd class="font-mono text-xs break-all" [title]="s.media_hash">
                  {{ s.media_hash.slice(0, 16) }}…
                </dd>
              }
            </dl>
          </section>
        </aside>
      </div>

      @if (s.status === 'ready') {
        <div class="mt-6">
          <app-transcript-section
            [source]="s"
            [canWrite]="canWrite()"
            [currentS]="time()"
            (seek)="seekTo($event)"
            (changed)="refresh()"
          />
        </div>
      }

      @if (s.status === 'ready') {
        <div class="mt-6">
          <app-highlights-section
            [source]="s"
            [canWrite]="canWrite()"
            (preview)="previewClip($event)"
            (changed)="refresh()"
          />
        </div>
      }

      @if (canWrite()) {
        <app-confirm-dialog
          #delDialog
          heading="Delete this video?"
          message="The video and all files generated from it are permanently removed. This cannot be undone."
          actionLabel="Delete video"
          [busy]="busy()"
          [error]="actionError()"
          (confirmed)="remove()"
        />
      }
    } @else {
      <p class="text-sm text-zinc-600 dark:text-zinc-400">Loading…</p>
    }
  `,
})
export class SourcePage implements OnInit {
  readonly id = input.required<string>();

  private readonly api = inject(MediaApi);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly realtime = inject(RealtimeService);
  private readonly destroyRef = inject(DestroyRef);

  readonly player = viewChild<ElementRef<HTMLMediaElement>>('player');
  readonly deleteDlg = viewChild.required<ConfirmDialogComponent>('delDialog');

  readonly src = signal<SourceDetail | null>(null);
  readonly project = signal<Project | null>(null);
  readonly loadError = signal('');
  readonly actionError = signal('');
  readonly busy = signal(false);
  readonly time = signal(0);
  private readonly stopAt = signal<number | null>(null);
  readonly bytes = formatBytes;
  readonly duration = formatDuration;
  readonly label = (s: string): string => STAGE_LABELS[s] ?? s;
  readonly stages = computed(() => this.src()?.job?.stages ?? []);
  readonly canWrite = computed(() => {
    const p = this.project();
    return !!p && p.my_role !== 'viewer' && this.auth.user()?.role !== 'viewer';
  });

  constructor() {
    this.realtime.events
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((e) => this.onEvent(e));
  }

  async ngOnInit(): Promise<void> {
    await this.load();
    const s = this.src();
    if (s)
      void this.api.getProject(s.project_id).then(
        (p) => this.project.set(p),
        () => undefined,
      );
  }

  private async load(): Promise<void> {
    try {
      this.src.set(await this.api.getSource(this.id()));
      this.loadError.set('');
    } catch (e) {
      this.loadError.set(errorMessage(e));
    }
  }

  private onEvent(e: RealtimeEvent): void {
    if (e.source_id !== this.id()) return;
    if (e.job_kind === 'transcribe_source' || e.job_kind === 'find_highlights') {
      this.onSideJobEvent(
        e,
        e.job_kind === 'transcribe_source' ? 'transcription_job' : 'highlights_job',
      );
      return;
    }
    if (e.type === 'job.progress' && e.stage) {
      this.patchStage(e.stage, { status: 'running', progress: e.progress ?? 0 });
      return;
    }
    if (e.stage && e.stage_status)
      this.patchStage(e.stage, { status: e.stage_status, progress: e.progress ?? 0 });
    if (e.job_status && e.job_status !== 'running' && e.job_status !== 'queued')
      void this.load(); // final state, proxy URL
    else if (e.source_status) this.src.update((s) => (s ? { ...s, status: e.source_status! } : s));
  }

  /** Transcription and highlight detection run as their own jobs; they never touch the source's own status. */
  private onSideJobEvent(e: RealtimeEvent, field: 'transcription_job' | 'highlights_job'): void {
    if (e.job_status && e.job_status !== 'running' && e.job_status !== 'queued') {
      void this.load();
      return;
    }
    if (e.stage) {
      const patch: Partial<Stage> = {
        status: e.stage_status ?? 'running',
        progress: e.progress ?? 0,
        message: e.message ?? null,
      };
      this.src.update((s) => {
        const j = s?.[field];
        if (!s || !j) return s;
        const stages = j.stages.map((st) => (st.stage === e.stage ? { ...st, ...patch } : st));
        return { ...s, [field]: { ...j, status: 'running', stages } };
      });
    }
  }

  private patchStage(stage: string, patch: Partial<Stage>): void {
    this.src.update((s) => {
      if (!s?.job) return s;
      const stages = s.job.stages.map((st) => (st.stage === stage ? { ...st, ...patch } : st));
      return { ...s, job: { ...s.job, stages } };
    });
  }

  onTime(e: Event): void {
    const el = e.target as HTMLMediaElement;
    this.time.set(el.currentTime);
    const stop = this.stopAt();
    if (stop !== null && el.currentTime >= stop) {
      el.pause(); // a preview plays one clip and stops, instead of running on into the rest of the video
      this.stopAt.set(null);
    }
  }

  previewClip(range: { start: number; end: number }): void {
    this.stopAt.set(range.end);
    this.seekTo(range.start);
  }

  seekTo(seconds: number): void {
    const el = this.player()?.nativeElement;
    if (!el) return;
    el.currentTime = seconds;
    void el.play().catch(() => undefined); // the browser may refuse autoplay; the position still moved
  }

  refresh(): Promise<void> {
    return this.load();
  }

  /** Signed media links expire after ~10 minutes; fetch fresh ones and continue where playback stopped. */
  async onMediaError(): Promise<void> {
    const v = this.player()?.nativeElement;
    const at = v?.currentTime ?? 0;
    await this.load();
    queueMicrotask(() => {
      const el = this.player()?.nativeElement;
      if (el) {
        el.currentTime = at;
      }
    });
  }

  private async act(fn: () => Promise<unknown>): Promise<void> {
    this.busy.set(true);
    this.actionError.set('');
    try {
      await fn();
      await this.load();
    } catch (e) {
      this.actionError.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }

  retry(): Promise<void> {
    return this.act(() => this.api.retryJob(this.src()!.job!.id));
  }
  cancel(): Promise<void> {
    return this.act(() => this.api.cancelJob(this.src()!.job!.id));
  }
  reprocess(): Promise<void> {
    return this.act(() => this.api.reprocess(this.id()));
  }

  async remove(): Promise<void> {
    const pid = this.src()?.project_id;
    this.busy.set(true);
    this.actionError.set('');
    try {
      await this.api.deleteSource(this.id());
      this.deleteDlg().close();
      await this.router.navigate(['/projects', pid]);
    } catch (e) {
      this.actionError.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }
}
