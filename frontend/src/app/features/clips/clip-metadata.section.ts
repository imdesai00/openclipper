import { Component, OnDestroy, OnInit, computed, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { errorMessage } from '../../core/api';
import { ClipsApi } from '../../core/clips-api.service';
import { ClipMetadata, MetadataFields, Platform } from '../../core/models';
import { StatusBadgeComponent } from '../../shared/status-badge.component';

const PLATFORM_LABEL: Record<Platform, string> = {
  generic: 'Any site',
  youtube: 'YouTube',
  instagram: 'Instagram',
};

interface Draft {
  title: string;
  hook: string;
  description: string;
  short_description: string;
  hashtags: string;
  tags: string;
  thumbnail_text: string;
  category: string;
  audience: string;
}

const toDraft = (m: MetadataFields): Draft => ({
  ...m,
  hashtags: m.hashtags.join(' '),
  tags: m.tags.join(', '),
});

const split = (text: string, sep: RegExp): string[] =>
  text
    .split(sep)
    .map((t) => t.trim())
    .filter(Boolean);

@Component({
  selector: 'app-clip-metadata',
  imports: [FormsModule, StatusBadgeComponent],
  template: `
    <section aria-labelledby="meta-h">
      <h2 id="meta-h" class="text-base font-semibold">Titles and descriptions</h2>
      <p class="mt-1 text-sm text-zinc-700 dark:text-zinc-300">
        Written from what is said in this clip, separately for each place you might post it. You can
        edit everything.
      </p>
      @if (error()) {
        <p class="alert-error mt-3" role="alert">{{ error() }}</p>
      }
      @if (running()) {
        <p class="mt-3 text-sm" role="status" aria-live="polite">
          Writing… <app-status-badge status="running" />
        </p>
      } @else if (failedJob(); as j) {
        <p class="alert-error mt-3" role="alert">
          Writing failed: {{ j.error_message ?? 'unknown error' }}
        </p>
      }

      @if (data(); as d) {
        <div role="tablist" aria-label="Platform" class="mt-4 flex gap-1">
          @for (p of platforms; track p) {
            <button
              type="button"
              role="tab"
              class="btn btn-secondary"
              [attr.aria-selected]="platform() === p"
              [attr.aria-controls]="'meta-panel'"
              [class.font-semibold]="platform() === p"
              (click)="platform.set(p)"
            >
              {{ label(p) }}
            </button>
          }
        </div>
        @if (current(); as m) {
          <div id="meta-panel" role="tabpanel" class="mt-4 space-y-3">
            @if (m.last_error) {
              <p class="alert-error" role="alert">
                The last attempt for {{ label(platform()) }} failed: {{ m.last_error }}
              </p>
            }
            <p class="text-xs text-zinc-600 dark:text-zinc-400">
              @if (m.generated_by) {
                Written by {{ m.generated_by }}.
              }
              @if (m.edited) {
                <span class="badge ml-1">edited by you</span>
              }
            </p>

            @for (f of fields; track f.key) {
              <div>
                <label class="label" [for]="'meta-' + f.key">
                  {{ f.label }}
                  @if (limit(f.key); as lim) {
                    <span
                      class="ml-1 font-normal"
                      [class.text-red-700]="count(f.key) > lim"
                      [class.dark:text-red-300]="count(f.key) > lim"
                    >
                      ({{ count(f.key) }} / {{ lim }})
                    </span>
                  }
                </label>
                @if (f.long) {
                  <textarea
                    [id]="'meta-' + f.key"
                    class="input"
                    rows="4"
                    [name]="f.key"
                    [ngModel]="field(f.key)"
                    (ngModelChange)="setField(f.key, $event)"
                    [disabled]="!canWrite()"
                  ></textarea>
                } @else {
                  <input
                    [id]="'meta-' + f.key"
                    class="input"
                    type="text"
                    [name]="f.key"
                    [ngModel]="field(f.key)"
                    (ngModelChange)="setField(f.key, $event)"
                    [disabled]="!canWrite()"
                  />
                }
              </div>
            }

            @for (e of m.errors; track e) {
              <p class="field-error" role="alert">{{ e }}</p>
            }
            @for (w of m.warnings; track w) {
              <p class="text-sm text-amber-800 dark:text-amber-300" role="note">{{ w }}</p>
            }
            @if (overLimit()) {
              <p class="field-error" role="alert">
                Over a platform limit: shorten the marked fields to save.
              </p>
            }

            @if (canWrite()) {
              <div class="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  class="btn btn-primary"
                  [disabled]="busy() || running() || overLimit()"
                  (click)="save()"
                >
                  Save {{ label(platform()) }} text
                </button>
                <button
                  type="button"
                  class="btn btn-secondary"
                  [disabled]="busy() || running()"
                  (click)="generate([platform()])"
                >
                  Write {{ label(platform()) }} text with AI
                </button>
                <button
                  type="button"
                  class="btn btn-secondary"
                  [disabled]="busy() || running()"
                  (click)="generate(platforms)"
                >
                  Write all three
                </button>
              </div>
              @if (needsConfirm()) {
                <div class="rounded-md border border-amber-600 p-3 text-sm" role="alert">
                  <p>{{ confirmMessage() }}</p>
                  <div class="mt-2 flex gap-2">
                    <button type="button" class="btn btn-danger" (click)="confirmOverwrite()">
                      Replace my text
                    </button>
                    <button
                      type="button"
                      class="btn btn-secondary"
                      (click)="needsConfirm.set(false)"
                    >
                      Keep my text
                    </button>
                  </div>
                </div>
              }
              @if (saved()) {
                <p class="text-sm text-green-800 dark:text-green-300" role="status">Saved.</p>
              }
            }
          </div>
        }
      } @else if (!error()) {
        <p class="mt-3 text-sm text-zinc-600 dark:text-zinc-400">Loading…</p>
      }
    </section>
  `,
})
export class ClipMetadataSection implements OnInit, OnDestroy {
  readonly clipId = input.required<string>();
  readonly canWrite = input(false);

  private readonly api = inject(ClipsApi);
  private timer: ReturnType<typeof setTimeout> | null = null;
  private pending: Platform[] = [];

  readonly platforms: Platform[] = ['generic', 'youtube', 'instagram'];
  readonly label = (p: Platform): string => PLATFORM_LABEL[p];
  readonly fields: { key: keyof Draft; label: string; long?: boolean }[] = [
    { key: 'title', label: 'Title' },
    { key: 'hook', label: 'Opening line' },
    { key: 'description', label: 'Description or caption', long: true },
    { key: 'short_description', label: 'Short description' },
    { key: 'hashtags', label: 'Hashtags (separate with spaces)' },
    { key: 'tags', label: 'Tags (separate with commas)' },
    { key: 'thumbnail_text', label: 'Words for the cover image' },
    { key: 'category', label: 'Category' },
    { key: 'audience', label: 'Who it is for' },
  ];

  readonly data = signal<ClipMetadata | null>(null);
  readonly platform = signal<Platform>('generic');
  readonly drafts = signal<Record<Platform, Draft> | null>(null);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly saved = signal(false);
  readonly needsConfirm = signal(false);

  readonly current = computed(() => this.data()?.platforms[this.platform()] ?? null);
  readonly running = computed(() => {
    const s = this.data()?.job?.status;
    return s === 'queued' || s === 'running';
  });
  readonly failedJob = computed(() => {
    const j = this.data()?.job;
    return j && (j.status === 'failed' || j.status === 'cancelled') ? j : null;
  });
  readonly overLimit = computed(() => this.fields.some((f) => this.over(f.key)));

  async ngOnInit(): Promise<void> {
    await this.load();
  }

  ngOnDestroy(): void {
    if (this.timer) clearTimeout(this.timer);
  }

  private async load(keepDrafts = false): Promise<void> {
    try {
      const d = await this.api.metadata(this.clipId());
      this.data.set(d);
      if (!keepDrafts || !this.drafts()) {
        this.drafts.set({
          generic: toDraft(d.platforms.generic),
          youtube: toDraft(d.platforms.youtube),
          instagram: toDraft(d.platforms.instagram),
        });
      }
      this.error.set('');
      if (this.running()) this.schedulePoll();
    } catch (e) {
      this.error.set(errorMessage(e));
    }
  }

  private schedulePoll(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.poll(), 1500);
  }

  private async poll(): Promise<void> {
    await this.load(true);
    if (!this.running()) {
      // the job finished: show what it wrote (the fields the person has not touched)
      const d = this.data();
      if (d)
        this.drafts.set({
          generic: toDraft(d.platforms.generic),
          youtube: toDraft(d.platforms.youtube),
          instagram: toDraft(d.platforms.instagram),
        });
    }
  }

  field(key: keyof Draft): string {
    return this.drafts()?.[this.platform()][key] ?? '';
  }

  setField(key: keyof Draft, value: string): void {
    this.saved.set(false);
    this.drafts.update((d) =>
      d ? { ...d, [this.platform()]: { ...d[this.platform()], [key]: value } } : d,
    );
  }

  limit(key: keyof Draft): number | null {
    const lim = this.data()?.limits[this.platform()] ?? {};
    if (key === 'title' || key === 'description' || key === 'short_description')
      return lim[key] ?? null;
    if (key === 'thumbnail_text') return lim['thumbnail_text'] ?? null;
    return null;
  }

  /** What the platform will count. An Instagram caption includes its hashtags. */
  count(key: keyof Draft): number {
    const text = this.field(key);
    if (key === 'description' && this.platform() === 'instagram') {
      const tags = split(this.field('hashtags'), /[\s,]+/).length
        ? split(this.field('hashtags'), /[\s,]+/).join(' ')
        : '';
      return text.length + (text && tags ? 1 : 0) + tags.length;
    }
    return text.length;
  }

  private over(key: keyof Draft): boolean {
    const lim = this.limit(key);
    return lim !== null && this.count(key) > lim;
  }

  async save(): Promise<void> {
    const d = this.drafts()?.[this.platform()];
    if (!d) return;
    this.busy.set(true);
    this.error.set('');
    try {
      await this.api.saveMetadata(this.clipId(), this.platform(), {
        ...d,
        hashtags: split(d.hashtags, /[\s,]+/),
        tags: split(d.tags, /,/),
      });
      await this.load();
      this.saved.set(true);
    } catch (e) {
      this.error.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }

  confirmMessage(): string {
    return `You edited the text for ${this.pending.map((p) => PLATFORM_LABEL[p]).join(', ')}. Writing it again replaces your edits.`;
  }

  async generate(platforms: Platform[], overwrite = false): Promise<void> {
    this.busy.set(true);
    this.error.set('');
    this.needsConfirm.set(false);
    try {
      await this.api.generateMetadata(this.clipId(), platforms, overwrite);
      await this.load(true);
    } catch (e) {
      const body = (e as { error?: { code?: string; details?: string[] } }).error;
      if (body?.code === 'edited_metadata') {
        this.pending = (body.details ?? platforms) as Platform[];
        this.needsConfirm.set(true);
      } else {
        this.error.set(errorMessage(e));
      }
    } finally {
      this.busy.set(false);
    }
  }

  confirmOverwrite(): Promise<void> {
    return this.generate(this.pending, true);
  }
}
