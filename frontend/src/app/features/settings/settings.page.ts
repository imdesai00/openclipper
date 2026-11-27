import { Component, OnInit, inject, signal } from '@angular/core';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';

import { errorMessage } from '../../core/api';
import { MediaApi } from '../../core/media-api.service';
import { AsrStatus, Features, ModelProgress } from '../../core/models';
import { TranscriptApi } from '../../core/transcript-api.service';
import { languageName } from '../sources/transcript.section';

@Component({
  selector: 'app-settings-page',
  imports: [ReactiveFormsModule, FormsModule],
  template: `
    <h1 class="text-2xl font-semibold tracking-tight">Settings</h1>

    <section class="card mt-6 max-w-2xl" aria-labelledby="ingest">
      <h2 id="ingest" class="text-base font-semibold">Link downloads (YouTube)</h2>
      <p class="mt-1 text-sm text-zinc-700 dark:text-zinc-300">
        Lets people add a YouTube video by pasting its link. It is off by default. Only single video
        links work; channels and playlists are refused, and DRM-protected videos are never
        downloaded.
      </p>
      <p
        class="mt-2 rounded-md border border-amber-700 p-3 text-sm text-amber-900 dark:border-amber-500 dark:text-amber-200"
        role="note"
      >
        Downloading from YouTube can violate YouTube's Terms of Service, and may put a YouTube API
        project used for publishing at risk. It works best from a home internet connection. Everyone
        who uses it confirms they have the right to use the content.
      </p>

      @if (features(); as f) {
        <form [formGroup]="form" (ngSubmit)="save()" class="mt-4 space-y-4" novalidate>
          <label class="flex items-center gap-2 text-sm">
            <input type="checkbox" class="size-4" formControlName="enabled" /> Allow YouTube links
          </label>
          <div>
            <label class="label" for="limit">Downloads allowed per day (whole instance)</label>
            <input
              id="limit"
              type="number"
              min="1"
              max="1000"
              class="input w-32"
              formControlName="daily_limit"
            />
          </div>
          <p class="text-sm" role="status">
            Download service:
            @if (f.ingest_available) {
              <strong>running</strong>
            } @else if (f.ingest_enabled) {
              <strong class="text-red-800 dark:text-red-300">not running.</strong> Start it with
              <code class="font-mono">docker compose --profile ingest up -d</code>.
            } @else {
              not needed while links are off.
            }
          </p>
          @if (error()) {
            <p class="alert-error" role="alert">{{ error() }}</p>
          }
          @if (saved()) {
            <p class="text-sm text-green-800 dark:text-green-300" role="status">Saved.</p>
          }
          <button class="btn btn-primary" type="submit" [disabled]="busy() || form.invalid">
            Save
          </button>
        </form>
      } @else if (loadError()) {
        <p class="alert-error mt-3" role="alert">{{ loadError() }}</p>
      } @else {
        <p class="mt-3 text-sm text-zinc-600 dark:text-zinc-400">Loading…</p>
      }
    </section>

    <section class="card mt-6 max-w-2xl" aria-labelledby="speech">
      <h2 id="speech" class="text-base font-semibold">Speech recognition</h2>
      <p class="mt-1 text-sm text-zinc-700 dark:text-zinc-300">
        Turns speech into a transcript with word timings. It runs on this machine; nothing is sent
        to another service. Models are downloaded once, from the model's public host, the first time
        you choose them.
      </p>
      @if (asr(); as a) {
        <p class="mt-3 text-sm" role="status">
          Transcription service:
          @if (a.available) {
            <strong>running</strong> ({{ a.device }}, {{ a.compute_type }})
          } @else {
            <strong class="text-red-800 dark:text-red-300">not running.</strong> Start it with
            <code class="font-mono">docker compose up -d worker-asr</code>.
          }
        </p>
        <form class="mt-4 space-y-4" (ngSubmit)="saveSpeech()">
          <div class="grid gap-3 sm:grid-cols-2">
            <div>
              <label class="label" for="s-model">Default model</label>
              <select id="s-model" class="input" name="model" [(ngModel)]="speechModel">
                @for (m of a.models; track m.name) {
                  <option [value]="m.name">{{ m.name }} ({{ m.mb }} MB)</option>
                }
              </select>
            </div>
            <div>
              <label class="label" for="s-lang">Default language</label>
              <select id="s-lang" class="input" name="language" [(ngModel)]="speechLang">
                <option value="">Detect automatically</option>
                @for (c of a.languages; track c) {
                  <option [value]="c">{{ languageName(c) }}</option>
                }
              </select>
            </div>
          </div>
          @if (speechError()) {
            <p class="alert-error" role="alert">{{ speechError() }}</p>
          }
          @if (speechSaved()) {
            <p class="text-sm text-green-800 dark:text-green-300" role="status">Saved.</p>
          }
          <button class="btn btn-primary" type="submit" [disabled]="speechBusy()">
            Save speech defaults
          </button>
        </form>

        <h3 class="mt-6 text-sm font-semibold">Models on this server</h3>
        <ul class="mt-2 divide-y divide-zinc-200 text-sm dark:divide-zinc-800">
          @for (m of a.models; track m.name) {
            <li class="py-2">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <span>
                  <strong>{{ m.name }}</strong> · {{ m.mb }} MB · {{ m.note }}
                  @if (!m.multilingual) {
                    <span class="badge ml-1">English only</span>
                  }
                </span>
                @if (m.downloaded) {
                  <span class="badge">Downloaded</span>
                } @else {
                  <button
                    type="button"
                    class="btn btn-secondary"
                    [disabled]="!a.available || downloading() === m.name"
                    [attr.aria-label]="'Download model ' + m.name"
                    (click)="download(m.name)"
                  >
                    Download
                  </button>
                }
              </div>
              @if (progress()[m.name]; as p) {
                @if (p.error) {
                  <p class="alert-error mt-2" role="alert">{{ p.error }}</p>
                } @else if (!p.done) {
                  <div
                    class="mt-2 h-1.5 overflow-hidden rounded bg-zinc-200 dark:bg-zinc-800"
                    role="progressbar"
                    [attr.aria-valuenow]="p.percent"
                    aria-valuemin="0"
                    aria-valuemax="100"
                    [attr.aria-label]="'Downloading ' + m.name"
                  >
                    <div class="h-full bg-accent" [style.width.%]="p.percent"></div>
                  </div>
                  <p class="mt-1 text-xs text-zinc-600 dark:text-zinc-400">{{ p.status }}</p>
                }
              }
            </li>
          }
        </ul>
      } @else if (speechLoadError()) {
        <p class="alert-error mt-3" role="alert">{{ speechLoadError() }}</p>
      } @else {
        <p class="mt-3 text-sm text-zinc-600 dark:text-zinc-400">Loading…</p>
      }
    </section>
  `,
})
export class SettingsPage implements OnInit {
  private readonly api = inject(MediaApi);
  private readonly fb = inject(FormBuilder).nonNullable;
  private readonly speech = inject(TranscriptApi);

  readonly asr = signal<AsrStatus | null>(null);
  readonly speechLoadError = signal('');
  readonly speechError = signal('');
  readonly speechSaved = signal(false);
  readonly speechBusy = signal(false);
  readonly downloading = signal('');
  readonly progress = signal<Record<string, ModelProgress>>({});
  readonly languageName = languageName;
  speechModel = '';
  speechLang = '';

  readonly features = signal<Features | null>(null);
  readonly loadError = signal('');
  readonly error = signal('');
  readonly busy = signal(false);
  readonly saved = signal(false);
  readonly form = this.fb.group({
    enabled: [false],
    daily_limit: [10, [Validators.required, Validators.min(1), Validators.max(1000)]],
  });

  async ngOnInit(): Promise<void> {
    try {
      const f = await this.api.features();
      this.features.set(f);
      this.form.setValue({ enabled: f.ingest_enabled, daily_limit: f.ingest_daily_limit });
    } catch (e) {
      this.loadError.set(errorMessage(e));
    }
    await this.loadSpeech();
  }

  private async loadSpeech(): Promise<void> {
    try {
      const a = await this.speech.asrStatus();
      this.asr.set(a);
      this.speechModel = a.default_model;
      this.speechLang = a.default_language ?? '';
    } catch (e) {
      this.speechLoadError.set(errorMessage(e));
    }
  }

  async saveSpeech(): Promise<void> {
    this.speechBusy.set(true);
    this.speechError.set('');
    this.speechSaved.set(false);
    try {
      await this.speech.saveAsrSettings(this.speechModel, this.speechLang || null);
      this.speechSaved.set(true);
    } catch (e) {
      this.speechError.set(errorMessage(e));
    } finally {
      this.speechBusy.set(false);
    }
  }

  /** Starts the download, then follows the worker's progress until it finishes (or fails visibly). */
  async download(name: string): Promise<void> {
    this.downloading.set(name);
    this.speechError.set('');
    try {
      await this.speech.downloadModel(name);
      for (;;) {
        const p = await this.speech.modelProgress(name);
        this.progress.update((cur) => ({ ...cur, [name]: p }));
        if (p.done) break;
        await new Promise((r) => setTimeout(r, 700));
      }
      await this.loadSpeech();
    } catch (e) {
      this.speechError.set(errorMessage(e));
    } finally {
      this.downloading.set('');
    }
  }

  async save(): Promise<void> {
    this.busy.set(true);
    this.error.set('');
    this.saved.set(false);
    try {
      const v = this.form.getRawValue();
      this.features.set(await this.api.setIngest(v.enabled, v.daily_limit));
      this.saved.set(true);
    } catch (e) {
      this.error.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }
}
