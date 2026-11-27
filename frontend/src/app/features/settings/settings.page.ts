import { Component, OnInit, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';

import { errorMessage } from '../../core/api';
import { MediaApi } from '../../core/media-api.service';
import { Features } from '../../core/models';

@Component({
  selector: 'app-settings-page',
  imports: [ReactiveFormsModule],
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
  `,
})
export class SettingsPage implements OnInit {
  private readonly api = inject(MediaApi);
  private readonly fb = inject(FormBuilder).nonNullable;

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
