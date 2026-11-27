import { DatePipe } from '@angular/common';
import {
  Component,
  DestroyRef,
  OnInit,
  computed,
  inject,
  input,
  signal,
  viewChild,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { errorMessage } from '../../core/api';
import { AuthService } from '../../core/auth.service';
import { formatBytes, formatDuration } from '../../core/format';
import { MediaApi } from '../../core/media-api.service';
import { Features, Project, RealtimeEvent, STAGE_LABELS, Source } from '../../core/models';
import { RealtimeService } from '../../core/realtime.service';
import { UploadHandle, UploadService } from '../../core/upload.service';
import { ConfirmDialogComponent } from '../../shared/confirm-dialog.component';
import { StatusBadgeComponent } from '../../shared/status-badge.component';

interface UploadItem {
  key: number;
  name: string;
  size: number;
  percent: number;
  state: 'uploading' | 'waiting' | 'done' | 'error';
  error: string;
  handle: UploadHandle | null;
}

@Component({
  selector: 'app-project-page',
  imports: [
    ReactiveFormsModule,
    RouterLink,
    StatusBadgeComponent,
    ConfirmDialogComponent,
    DatePipe,
  ],
  template: `
    @if (loadError()) {
      <p class="alert-error" role="alert">{{ loadError() }}</p>
      <a routerLink="/projects" class="mt-3 inline-block text-sm underline">Back to projects</a>
    } @else if (project(); as p) {
      <a routerLink="/projects" class="text-sm text-zinc-600 underline dark:text-zinc-400"
        >Projects</a
      >
      <div class="mt-1 flex flex-wrap items-center justify-between gap-3">
        <h1 class="text-2xl font-semibold tracking-tight">{{ p.name }}</h1>
        @if (p.my_role === 'owner') {
          <button type="button" class="btn btn-secondary" (click)="deleteDlg().open()">
            Delete project
          </button>
        }
      </div>

      @if (canWrite()) {
        <section class="card mt-6" aria-labelledby="add">
          <h2 id="add" class="text-base font-semibold">Add a video</h2>

          <label class="mt-3 flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              class="mt-0.5 size-4"
              [checked]="attested()"
              (change)="attested.set($any($event.target).checked)"
            />
            <span>I own this content or have permission to use it.</span>
          </label>

          <fieldset class="mt-4">
            <legend class="sr-only">How to add the video</legend>
            <div class="flex gap-4 text-sm">
              <label class="flex items-center gap-2">
                <input
                  type="radio"
                  name="mode"
                  value="upload"
                  [checked]="mode() === 'upload'"
                  (change)="mode.set('upload')"
                />
                Upload a file
              </label>
              <label class="flex items-center gap-2">
                <input
                  type="radio"
                  name="mode"
                  value="link"
                  [checked]="mode() === 'link'"
                  (change)="mode.set('link')"
                />
                From a link
              </label>
            </div>
          </fieldset>

          @if (mode() === 'upload') {
            <div
              class="mt-4 rounded-md border-2 border-dashed border-zinc-300 p-6 text-center dark:border-zinc-700"
              [class.border-accent]="dragging()"
              (dragover)="$event.preventDefault(); dragging.set(true)"
              (dragleave)="dragging.set(false)"
              (drop)="onDrop($event)"
            >
              <label class="label inline-block cursor-pointer" for="file"
                >Choose a video file</label
              >
              <input
                id="file"
                type="file"
                class="input mx-auto max-w-sm"
                [attr.accept]="accept()"
                [disabled]="!attested()"
                (change)="onPick($event)"
              />
              <p class="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
                or drop it here. Large files are fine: uploads resume if your connection drops.
                @if (features(); as f) {
                  Up to {{ bytes(f.max_upload_bytes) }}.
                }
              </p>
              @if (!attested()) {
                <p class="mt-2 text-sm text-amber-800 dark:text-amber-300">
                  Tick the box above to enable uploads.
                </p>
              }
            </div>
          } @else {
            <form [formGroup]="linkForm" (ngSubmit)="addLink()" class="mt-4" novalidate>
              <label class="label" for="url">Link to a video</label>
              <div class="flex gap-2">
                <input
                  id="url"
                  class="input"
                  formControlName="url"
                  placeholder="https://"
                  autocomplete="off"
                  aria-describedby="url-help"
                />
                <button
                  class="btn btn-primary"
                  type="submit"
                  [disabled]="linkBusy() || linkForm.invalid || !attested()"
                >
                  Add
                </button>
              </div>
              <p id="url-help" class="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
                Links to video files work. {{ youtubeHelp() }}
                Downloading from a site may break that site's terms of service; you are responsible
                for what you add.
              </p>
              @if (linkError()) {
                <p class="alert-error mt-2" role="alert">{{ linkError() }}</p>
              }
            </form>
          }
        </section>
      }

      @if (uploads().length) {
        <section class="mt-4" aria-label="Uploads in progress">
          <ul class="space-y-2">
            @for (u of uploads(); track u.key) {
              <li class="card py-3">
                <div class="flex items-center justify-between gap-3 text-sm">
                  <span class="truncate font-medium">{{ u.name }}</span>
                  <span class="shrink-0 text-zinc-600 dark:text-zinc-400">
                    @switch (u.state) {
                      @case ('uploading') {
                        {{ u.percent.toFixed(0) }}% of {{ bytes(u.size) }}
                      }
                      @case ('waiting') {
                        Connection lost. Waiting to resume…
                      }
                      @case ('done') {
                        Uploaded, now processing
                      }
                      @case ('error') {
                        Failed
                      }
                    }
                  </span>
                </div>
                @if (u.state === 'uploading' || u.state === 'waiting') {
                  <div
                    class="mt-2 h-2 overflow-hidden rounded bg-zinc-200 dark:bg-zinc-800"
                    role="progressbar"
                    [attr.aria-valuenow]="u.percent.toFixed(0)"
                    aria-valuemin="0"
                    aria-valuemax="100"
                    [attr.aria-label]="'Uploading ' + u.name"
                  >
                    <div class="h-full bg-accent" [style.width.%]="u.percent"></div>
                  </div>
                  <button
                    type="button"
                    class="btn btn-secondary mt-2 py-1"
                    (click)="cancelUpload(u)"
                  >
                    Cancel upload
                  </button>
                }
                @if (u.state === 'error') {
                  <p class="alert-error mt-2" role="alert">{{ u.error }}</p>
                }
              </li>
            }
          </ul>
        </section>
      }

      <section class="mt-6" aria-labelledby="vids">
        <h2 id="vids" class="text-base font-semibold">Videos</h2>
        @if (sourcesError()) {
          <p class="alert-error mt-2" role="alert">{{ sourcesError() }}</p>
        } @else if (sources().length === 0) {
          <div class="card mt-2">
            <p class="text-sm text-zinc-600 dark:text-zinc-400">
              No videos yet.
              @if (canWrite()) {
                Upload a file or add a link above.
              } @else {
                Editors can add videos.
              }
            </p>
          </div>
        } @else {
          <ul class="mt-2 space-y-2">
            @for (s of sources(); track s.id) {
              <li class="card flex items-center gap-4 p-3">
                <a
                  [routerLink]="['/sources', s.id]"
                  class="shrink-0"
                  [attr.aria-label]="'Open ' + s.title"
                >
                  @if (s.thumbnail_url) {
                    <img [src]="s.thumbnail_url" alt="" class="h-16 w-28 rounded object-cover" />
                  } @else {
                    <div
                      class="h-16 w-28 rounded bg-zinc-200 dark:bg-zinc-800"
                      aria-hidden="true"
                    ></div>
                  }
                </a>
                <div class="min-w-0 flex-1">
                  <a
                    [routerLink]="['/sources', s.id]"
                    class="block truncate font-medium hover:underline"
                    >{{ s.title }}</a
                  >
                  <p class="text-sm text-zinc-600 dark:text-zinc-400">
                    {{ s.created_at | date: 'medium' }}
                    @if (s.duration_s) {
                      · {{ duration(s.duration_s) }}
                    }
                    @if (s.size_bytes) {
                      · {{ bytes(s.size_bytes) }}
                    }
                  </p>
                  @if (progress()[s.id]; as pr) {
                    <div
                      class="mt-1 flex items-center gap-2 text-xs text-zinc-600 dark:text-zinc-400"
                    >
                      <span>{{ pr.label }}</span>
                      <div
                        class="h-1.5 w-32 overflow-hidden rounded bg-zinc-200 dark:bg-zinc-800"
                        role="progressbar"
                        [attr.aria-valuenow]="(pr.value * 100).toFixed(0)"
                        aria-valuemin="0"
                        aria-valuemax="100"
                        [attr.aria-label]="pr.label"
                      >
                        <div class="h-full bg-accent" [style.width.%]="pr.value * 100"></div>
                      </div>
                    </div>
                  }
                  @if (s.status === 'failed' && s.error_message) {
                    <p class="mt-1 text-sm text-red-800 dark:text-red-300">{{ s.error_message }}</p>
                  }
                </div>
                <app-status-badge [status]="s.status" />
              </li>
            }
          </ul>
        }
      </section>

      @if (p.my_role === 'owner') {
        <app-confirm-dialog
          #delDialog
          heading="Delete this project?"
          [message]="'This permanently deletes the project, all its videos and every generated file. This cannot be undone.'"
          [typeToConfirm]="p.name"
          actionLabel="Delete project"
          [busy]="deleting()"
          [error]="deleteError()"
          (confirmed)="deleteProject()"
        />
      }
    } @else {
      <p class="text-sm text-zinc-600 dark:text-zinc-400">Loading…</p>
    }
  `,
})
export class ProjectPage implements OnInit {
  readonly id = input.required<string>(); // route param (withComponentInputBinding)

  private readonly api = inject(MediaApi);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly uploader = inject(UploadService);
  private readonly realtime = inject(RealtimeService);
  private readonly destroyRef = inject(DestroyRef);
  private readonly fb = inject(FormBuilder).nonNullable;

  readonly deleteDlg = viewChild.required<ConfirmDialogComponent>('delDialog');

  readonly project = signal<Project | null>(null);
  readonly loadError = signal('');
  readonly sources = signal<Source[]>([]);
  readonly sourcesError = signal('');
  readonly features = signal<Features | null>(null);
  readonly uploads = signal<UploadItem[]>([]);
  readonly progress = signal<Record<string, { label: string; value: number }>>({});
  readonly attested = signal(false);
  readonly mode = signal<'upload' | 'link'>('upload');
  readonly dragging = signal(false);
  readonly linkBusy = signal(false);
  readonly linkError = signal('');
  readonly deleting = signal(false);
  readonly deleteError = signal('');
  private nextKey = 1;

  readonly linkForm = this.fb.group({ url: ['', [Validators.required, Validators.minLength(8)]] });
  readonly bytes = formatBytes;
  readonly duration = formatDuration;

  readonly canWrite = computed(() => {
    const p = this.project();
    return !!p && p.my_role !== 'viewer' && this.auth.user()?.role !== 'viewer';
  });
  readonly accept = computed(() => (this.features()?.allowed_extensions ?? []).join(','));
  readonly youtubeHelp = computed(() => {
    const f = this.features();
    if (!f) return '';
    if (f.ingest_available) return 'YouTube video links are enabled.';
    if (f.ingest_enabled)
      return 'YouTube links are switched on, but the download service is not running.';
    return 'YouTube links are turned off; an admin can enable them in Settings.';
  });

  constructor() {
    this.realtime.events
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe((e) => this.onEvent(e));
  }

  async ngOnInit(): Promise<void> {
    try {
      this.project.set(await this.api.getProject(this.id()));
    } catch (e) {
      this.loadError.set(errorMessage(e));
      return;
    }
    void this.api.features().then(
      (f) => this.features.set(f),
      () => undefined,
    );
    await this.reload();
  }

  private async reload(): Promise<void> {
    try {
      this.sources.set(await this.api.listSources(this.id()));
      this.sourcesError.set('');
    } catch (e) {
      this.sourcesError.set(errorMessage(e));
    }
  }

  private onEvent(e: RealtimeEvent): void {
    if (e.project_id !== this.id() || !e.source_id) return;
    const sid = e.source_id;
    if (e.type === 'job.progress' && e.stage) {
      this.progress.update((m) => ({
        ...m,
        [sid]: { label: STAGE_LABELS[e.stage!] ?? e.stage!, value: e.progress ?? 0 },
      }));
      return;
    }
    if (e.source_status) {
      const status = e.source_status;
      this.sources.update((list) => list.map((s) => (s.id === sid ? { ...s, status } : s)));
    }
    if (e.job_status && e.job_status !== 'running' && e.job_status !== 'queued') {
      this.progress.update((m) => {
        const rest = { ...m };
        delete rest[sid];
        return rest;
      });
      void this.reload(); // pick up thumbnail, duration and error details
    } else if (!this.sources().some((s) => s.id === sid)) {
      void this.reload(); // a source created in another tab or by a teammate
    }
  }

  onPick(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    for (const f of Array.from(input.files ?? [])) this.startUpload(f);
    input.value = '';
  }

  onDrop(ev: DragEvent): void {
    ev.preventDefault();
    this.dragging.set(false);
    if (!this.attested()) return;
    for (const f of Array.from(ev.dataTransfer?.files ?? [])) this.startUpload(f);
  }

  private startUpload(file: File): void {
    const item: UploadItem = {
      key: this.nextKey++,
      name: file.name,
      size: file.size,
      percent: 0,
      state: 'uploading',
      error: '',
      handle: null,
    };
    this.uploads.update((l) => [item, ...l]);
    const patch = (p: Partial<UploadItem>) =>
      this.uploads.update((l) => l.map((u) => (u.key === item.key ? { ...u, ...p } : u)));
    item.handle = this.uploader.start(file, this.id(), {
      onProgress: (percent) => patch({ percent }),
      onDone: () => {
        patch({ state: 'done', percent: 100 });
        void this.reload();
      },
      onError: (error) => patch({ state: 'error', error }),
      onWaiting: () => patch({ state: 'waiting' }),
      onResumed: () => patch({ state: 'uploading' }),
    });
    patch({ handle: item.handle });
  }

  cancelUpload(u: UploadItem): void {
    u.handle?.abort();
    this.uploads.update((l) => l.filter((x) => x.key !== u.key));
  }

  async addLink(): Promise<void> {
    if (this.linkForm.invalid || !this.attested()) return;
    this.linkBusy.set(true);
    this.linkError.set('');
    try {
      await this.api.addLink(this.id(), this.linkForm.getRawValue().url.trim(), true);
      this.linkForm.reset({ url: '' });
      await this.reload();
    } catch (e) {
      this.linkError.set(errorMessage(e));
    } finally {
      this.linkBusy.set(false);
    }
  }

  async deleteProject(): Promise<void> {
    const p = this.project();
    if (!p) return;
    this.deleting.set(true);
    this.deleteError.set('');
    try {
      await this.api.deleteProject(p.id, p.name);
      this.deleteDlg().close();
      await this.router.navigateByUrl('/projects');
    } catch (e) {
      this.deleteError.set(errorMessage(e));
    } finally {
      this.deleting.set(false);
    }
  }
}
