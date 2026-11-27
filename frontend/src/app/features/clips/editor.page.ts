import {
  Component,
  DestroyRef,
  ElementRef,
  HostListener,
  OnInit,
  computed,
  effect,
  inject,
  input,
  signal,
  untracked,
  viewChild,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { apiError, errorMessage } from '../../core/api';
import { AuthService } from '../../core/auth.service';
import { ClipsApi } from '../../core/clips-api.service';
import { formatBytes } from '../../core/format';
import { MediaApi } from '../../core/media-api.service';
import {
  Aspect,
  CaptionTemplate,
  Clip,
  ClipRender,
  ClipWord,
  CropKeyframe,
  CropSpec,
  PreviewStyle,
  Project,
  SourceDetail,
} from '../../core/models';
import { ConfirmDialogComponent } from '../../shared/confirm-dialog.component';
import { StatusBadgeComponent } from '../../shared/status-badge.component';
import {
  ASPECT_SIZES,
  FRAME_MS,
  centreFor,
  formatMs,
  parseTime,
  snapToFrame,
  upsertKeyframe,
  windowAt,
} from './clip-math';
import { ClipMetadataSection } from './clip-metadata.section';
import { ClipTimeline } from './clip-timeline.component';

interface Draft {
  title: string;
  start_ms: number;
  end_ms: number;
  aspect: Aspect;
  crop: CropSpec;
  background: { type: 'blur' | 'solid'; color: string };
  audio: { gain_db: number; mute: boolean };
  caption_template_id: string | null;
  caption_overrides: Record<string, unknown>;
}

const fromClip = (c: Clip): Draft => ({
  title: c.title,
  start_ms: c.version.start_ms,
  end_ms: c.version.end_ms,
  aspect: c.version.aspect,
  crop: structuredClone(c.version.crop),
  background: { ...c.version.background },
  audio: { ...c.version.audio },
  caption_template_id: c.version.caption_template_id,
  caption_overrides: { ...c.version.caption_overrides },
});

type Tab = 'shape' | 'captions' | 'sound' | 'text' | 'renders';
const TABS: { key: Tab; label: string }[] = [
  { key: 'shape', label: 'Shape and crop' },
  { key: 'captions', label: 'Captions' },
  { key: 'sound', label: 'Sound' },
  { key: 'text', label: 'Titles and text' },
  { key: 'renders', label: 'Renders' },
];

@Component({
  selector: 'app-clip-editor',
  imports: [
    FormsModule,
    RouterLink,
    StatusBadgeComponent,
    ClipTimeline,
    ClipMetadataSection,
    ConfirmDialogComponent,
  ],
  template: `
    @if (loadError()) {
      <p class="alert-error" role="alert">{{ loadError() }}</p>
      <a routerLink="/projects" class="mt-3 inline-block text-sm underline">Back to projects</a>
    } @else if (clip(); as c) {
      <a
        [routerLink]="['/sources', c.source_id]"
        class="text-sm text-zinc-600 underline dark:text-zinc-400"
      >
        {{ source()?.title ?? 'Video' }}
      </a>
      <div class="mt-1 flex flex-wrap items-center gap-3">
        <h1 class="sr-only">Editing {{ c.title }}</h1>
        <label class="sr-only" for="clip-title">Clip name</label>
        <input
          id="clip-title"
          class="input max-w-md text-lg font-semibold"
          [ngModel]="draft().title"
          (ngModelChange)="patch({ title: $event })"
          [disabled]="!canWrite()"
          maxlength="200"
        />
        <span class="badge">version {{ c.current_version }}</span>
        @if (dirty()) {
          <span class="badge border-amber-600 text-amber-800 dark:text-amber-300">
            unsaved changes
          </span>
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

      <div class="mt-4 grid gap-6 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div class="min-w-0 space-y-4">
          <section aria-label="Preview">
            <div
              #stage
              class="relative mx-auto w-full max-w-2xl overflow-hidden rounded-lg bg-black"
              [style.aspect-ratio]="stageRatio()"
            >
              @if (hasVideo() && source()?.proxy_url) {
                <video
                  #media
                  class="absolute inset-0 h-full w-full"
                  [src]="source()!.proxy_url"
                  preload="metadata"
                  playsinline
                  (timeupdate)="onTime()"
                  (loadedmetadata)="onTime()"
                  (error)="onMediaError()"
                ></video>
              } @else {
                <div
                  class="absolute inset-0 flex items-center justify-center bg-[#1B1B2F] p-4 text-center text-sm text-zinc-200"
                >
                  Audio only: the finished clip shows a moving waveform behind the captions.
                </div>
              }
              @if (hasVideo() && draft().crop.mode === 'fill' && win(); as w) {
                <div
                  class="absolute cursor-move border-2 border-white shadow-[0_0_0_9999px_rgba(0,0,0,0.55)]"
                  [style.left.%]="(w.x / srcW()) * 100"
                  [style.top.%]="(w.y / srcH()) * 100"
                  [style.width.%]="(w.w / srcW()) * 100"
                  [style.height.%]="(w.h / srcH()) * 100"
                  (pointerdown)="beginPan($event)"
                  role="img"
                  aria-label="Crop window: drag to reframe"
                >
                  <div
                    class="pointer-events-none absolute left-0 top-0 origin-top-left"
                    [style]="captionBoxStyle(w)"
                  >
                    @if (activeCue(); as cue) {
                      <div [style]="containerStyle()">
                        <div [style]="textStyle()" data-testid="caption-preview">
                          @for (line of cue.lines; track $index) {
                            <div>
                              @for (word of line; track $index) {
                                <span [style.color]="wordColor(word)">{{ word.text }} </span>
                              }
                            </div>
                          }
                        </div>
                      </div>
                    }
                  </div>
                </div>
              }
            </div>
            <div class="mx-auto mt-2 flex max-w-2xl flex-wrap items-center gap-2">
              <button type="button" class="btn btn-secondary" (click)="playClip()">
                Play clip
              </button>
              <button type="button" class="btn btn-secondary" (click)="togglePlay()">
                {{ playing() ? 'Pause' : 'Play' }}
              </button>
              <span class="font-mono text-sm" aria-live="off">{{ fmt(timeMs()) }}</span>
              @if (canWrite()) {
                <button type="button" class="btn btn-secondary" (click)="setStart(timeMs())">
                  Start here (I)
                </button>
                <button type="button" class="btn btn-secondary" (click)="setEnd(timeMs())">
                  End here (O)
                </button>
              }
              <span class="text-sm text-zinc-600 dark:text-zinc-400">
                {{ fmt(draft().end_ms - draft().start_ms) }} long
              </span>
            </div>
          </section>

          <section aria-label="Timeline">
            <app-clip-timeline
              [durationMs]="durationMs()"
              [startMs]="draft().start_ms"
              [endMs]="draft().end_ms"
              [playheadMs]="timeMs()"
              [peaks]="peaks()"
              [cutsMs]="cuts()"
              [disabled]="!canWrite()"
              (startChange)="setStart($event)"
              (endChange)="setEnd($event)"
              (seek)="seekTo($event)"
            />
            <div class="mt-2 grid grid-cols-2 gap-3 text-sm">
              <div>
                <label class="label" for="t-start">Starts at</label>
                <div class="flex gap-1">
                  <input
                    id="t-start"
                    class="input font-mono"
                    [value]="fmt(draft().start_ms)"
                    (change)="typeTime($event, 'start')"
                    [disabled]="!canWrite()"
                  />
                  <button
                    type="button"
                    class="btn btn-secondary"
                    aria-label="Start one frame earlier"
                    (click)="setStart(draft().start_ms - frame)"
                    [disabled]="!canWrite()"
                  >
                    −
                  </button>
                  <button
                    type="button"
                    class="btn btn-secondary"
                    aria-label="Start one frame later"
                    (click)="setStart(draft().start_ms + frame)"
                    [disabled]="!canWrite()"
                  >
                    +
                  </button>
                </div>
              </div>
              <div>
                <label class="label" for="t-end">Ends at</label>
                <div class="flex gap-1">
                  <input
                    id="t-end"
                    class="input font-mono"
                    [value]="fmt(draft().end_ms)"
                    (change)="typeTime($event, 'end')"
                    [disabled]="!canWrite()"
                  />
                  <button
                    type="button"
                    class="btn btn-secondary"
                    aria-label="End one frame earlier"
                    (click)="setEnd(draft().end_ms - frame)"
                    [disabled]="!canWrite()"
                  >
                    −
                  </button>
                  <button
                    type="button"
                    class="btn btn-secondary"
                    aria-label="End one frame later"
                    (click)="setEnd(draft().end_ms + frame)"
                    [disabled]="!canWrite()"
                  >
                    +
                  </button>
                </div>
              </div>
            </div>
          </section>

          <section class="card" aria-labelledby="words-h">
            <h2 id="words-h" class="text-base font-semibold">What is said</h2>
            @if (words().length) {
              <p class="mt-1 text-xs text-zinc-600 dark:text-zinc-400">
                Click a word to jump there. Words outside the clip are dimmed.
              </p>
              <p class="mt-2 max-h-48 overflow-y-auto text-sm leading-7" data-testid="words">
                @for (w of words(); track $index) {
                  <button
                    type="button"
                    class="rounded px-0.5 hover:bg-indigo-100 dark:hover:bg-indigo-950"
                    [class.opacity-50]="!inside(w)"
                    [class.bg-indigo-100]="pickedWord() === w"
                    [class.dark:bg-indigo-950]="pickedWord() === w"
                    (click)="pickWord(w)"
                  >
                    {{ w.text }}
                  </button>
                  {{ ' ' }}
                }
              </p>
              @if (canWrite() && pickedWord()) {
                <div class="mt-2 flex gap-2">
                  <button
                    type="button"
                    class="btn btn-secondary"
                    (click)="setStart(pickedWord()!.start_ms)"
                  >
                    Start the clip at this word
                  </button>
                  <button
                    type="button"
                    class="btn btn-secondary"
                    (click)="setEnd(pickedWord()!.end_ms)"
                  >
                    End the clip after this word
                  </button>
                </div>
              }
            } @else {
              <p class="mt-1 text-sm text-zinc-700 dark:text-zinc-300">
                There is no transcript for this clip, so there are no captions. Transcribe the video
                first.
              </p>
            }
          </section>
        </div>

        <div class="min-w-0">
          <div role="tablist" aria-label="Editor sections" class="flex flex-wrap gap-1">
            @for (t of tabs; track t.key) {
              <button
                type="button"
                role="tab"
                class="btn btn-secondary"
                [attr.aria-selected]="tab() === t.key"
                [attr.aria-controls]="'panel-' + t.key"
                [class.font-semibold]="tab() === t.key"
                (click)="tab.set(t.key)"
              >
                {{ t.label }}
              </button>
            }
          </div>

          <div class="card mt-3" [id]="'panel-' + tab()" role="tabpanel">
            @switch (tab()) {
              @case ('shape') {
                <div class="space-y-4">
                  <div>
                    <label class="label" for="aspect">Shape</label>
                    <select
                      id="aspect"
                      class="input"
                      [ngModel]="draft().aspect"
                      (ngModelChange)="patch({ aspect: $event })"
                      [disabled]="!canWrite()"
                    >
                      <option value="9:16">Vertical 9:16 (Shorts, Reels)</option>
                      <option value="1:1">Square 1:1</option>
                      <option value="16:9">Wide 16:9</option>
                    </select>
                  </div>
                  @if (hasVideo()) {
                    <fieldset>
                      <legend class="label">How the picture fits</legend>
                      <label class="mr-4 inline-flex items-center gap-2 text-sm">
                        <input
                          type="radio"
                          name="mode"
                          value="fill"
                          [checked]="draft().crop.mode === 'fill'"
                          (change)="setCrop({ mode: 'fill' })"
                          [disabled]="!canWrite()"
                        />
                        Fill the frame (crop)
                      </label>
                      <label class="inline-flex items-center gap-2 text-sm">
                        <input
                          type="radio"
                          name="mode"
                          value="fit"
                          [checked]="draft().crop.mode === 'fit'"
                          (change)="setCrop({ mode: 'fit' })"
                          [disabled]="!canWrite()"
                        />
                        Show all of it, on a background
                      </label>
                    </fieldset>
                    @if (draft().crop.mode === 'fill') {
                      <div>
                        <label class="label" for="zoom">
                          Zoom ({{ draft().crop.zoom.toFixed(1) }}×)
                        </label>
                        <input
                          id="zoom"
                          type="range"
                          class="w-full"
                          min="1"
                          max="4"
                          step="0.1"
                          [value]="draft().crop.zoom"
                          (input)="setCrop({ zoom: +$any($event.target).value })"
                          [disabled]="!canWrite()"
                        />
                        <p class="text-xs text-zinc-600 dark:text-zinc-400">
                          The zoom stays the same through the clip. Drag the white frame over the
                          picture to move it; each move at a different time makes the frame glide.
                        </p>
                      </div>
                      <div>
                        <h3 class="text-sm font-semibold">Frame positions</h3>
                        @if (draft().crop.keyframes.length) {
                          <ul class="mt-1 divide-y divide-zinc-200 text-sm dark:divide-zinc-800">
                            @for (k of draft().crop.keyframes; track k.t_ms) {
                              <li class="flex items-center justify-between gap-2 py-1">
                                <button
                                  type="button"
                                  class="underline"
                                  (click)="seekTo(draft().start_ms + k.t_ms)"
                                >
                                  at {{ fmt(k.t_ms) }}
                                </button>
                                <span class="text-zinc-600 dark:text-zinc-400">
                                  {{ (k.cx * 100).toFixed(0) }}% across,
                                  {{ (k.cy * 100).toFixed(0) }}% down
                                </span>
                                @if (canWrite()) {
                                  <button
                                    type="button"
                                    class="btn btn-secondary"
                                    [attr.aria-label]="'Remove the position at ' + fmt(k.t_ms)"
                                    (click)="removeKeyframe(k)"
                                  >
                                    Remove
                                  </button>
                                }
                              </li>
                            }
                          </ul>
                        } @else {
                          <p class="text-sm text-zinc-700 dark:text-zinc-300">
                            Centred for the whole clip.
                          </p>
                        }
                      </div>
                    } @else {
                      <div class="space-y-2">
                        <fieldset>
                          <legend class="label">Background</legend>
                          <label class="mr-4 inline-flex items-center gap-2 text-sm">
                            <input
                              type="radio"
                              name="bg"
                              value="blur"
                              [checked]="draft().background.type === 'blur'"
                              (change)="
                                patch({ background: { ...draft().background, type: 'blur' } })
                              "
                              [disabled]="!canWrite()"
                            />
                            Blurred copy of the picture
                          </label>
                          <label class="inline-flex items-center gap-2 text-sm">
                            <input
                              type="radio"
                              name="bg"
                              value="solid"
                              [checked]="draft().background.type === 'solid'"
                              (change)="
                                patch({ background: { ...draft().background, type: 'solid' } })
                              "
                              [disabled]="!canWrite()"
                            />
                            One colour
                          </label>
                        </fieldset>
                        @if (draft().background.type === 'solid') {
                          <div>
                            <label class="label" for="bgc">Colour</label>
                            <input
                              id="bgc"
                              type="color"
                              [value]="draft().background.color"
                              (input)="
                                patch({
                                  background: {
                                    ...draft().background,
                                    color: $any($event.target).value,
                                  },
                                })
                              "
                              [disabled]="!canWrite()"
                            />
                          </div>
                        }
                      </div>
                    }
                  } @else {
                    <div>
                      <label class="label" for="bgc2">Background colour behind the waveform</label>
                      <input
                        id="bgc2"
                        type="color"
                        [value]="audioBg()"
                        (input)="
                          patch({ background: { type: 'solid', color: $any($event.target).value } })
                        "
                        [disabled]="!canWrite()"
                      />
                    </div>
                  }
                </div>
              }
              @case ('captions') {
                <div class="space-y-3">
                  <label class="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      [checked]="capsOn()"
                      (change)="setOverride('enabled', $any($event.target).checked)"
                      [disabled]="!canWrite()"
                    />
                    Show captions
                  </label>
                  <div>
                    <label class="label" for="tpl">Template</label>
                    <select
                      id="tpl"
                      class="input"
                      [ngModel]="draft().caption_template_id"
                      (ngModelChange)="setTemplate($event)"
                      [disabled]="!canWrite()"
                    >
                      @for (t of templates(); track t.id) {
                        <option [value]="t.id">{{ t.name }}</option>
                      }
                    </select>
                    <p class="mt-1 text-xs text-zinc-600 dark:text-zinc-400">
                      The preview over the video is a close approximation. A server preview (the
                      Renders tab) shows exactly what the finished clip will look like.
                    </p>
                  </div>
                  @if (style(); as st) {
                    <div class="grid grid-cols-2 gap-3">
                      <div>
                        <label class="label" for="font">Font</label>
                        <select
                          id="font"
                          class="input"
                          [ngModel]="st['font_family']"
                          (ngModelChange)="setOverride('font_family', $event)"
                          [disabled]="!canWrite()"
                        >
                          @for (f of fonts(); track f) {
                            <option [value]="f">{{ f }}</option>
                          }
                        </select>
                      </div>
                      <div>
                        <label class="label" for="anim">Movement</label>
                        <select
                          id="anim"
                          class="input"
                          [ngModel]="st['animation']"
                          (ngModelChange)="setOverride('animation', $event)"
                          [disabled]="!canWrite()"
                        >
                          <option value="none">None</option>
                          <option value="highlight">Highlight the spoken word</option>
                          <option value="karaoke">Karaoke fill</option>
                          <option value="pop">Pop the spoken word</option>
                        </select>
                      </div>
                      <div>
                        <label class="label" for="size"
                          >Size ({{ st['font_size_pct'] }}% of the height)</label
                        >
                        <input
                          id="size"
                          type="range"
                          class="w-full"
                          min="2"
                          max="9"
                          step="0.2"
                          [value]="st['font_size_pct']"
                          (input)="setOverride('font_size_pct', +$any($event.target).value)"
                          [disabled]="!canWrite()"
                        />
                      </div>
                      <div>
                        <label class="label" for="pos">Position</label>
                        <select
                          id="pos"
                          class="input"
                          [ngModel]="st['position']"
                          (ngModelChange)="setOverride('position', $event)"
                          [disabled]="!canWrite()"
                        >
                          <option value="bottom">Bottom</option>
                          <option value="middle">Middle</option>
                          <option value="top">Top</option>
                        </select>
                      </div>
                      <div>
                        <label class="label" for="col">Text colour</label>
                        <input
                          id="col"
                          type="color"
                          [value]="st['color']"
                          (input)="setOverride('color', $any($event.target).value)"
                          [disabled]="!canWrite()"
                        />
                      </div>
                      <div>
                        <label class="label" for="hl">Highlight colour</label>
                        <input
                          id="hl"
                          type="color"
                          [value]="st['highlight_color']"
                          (input)="setOverride('highlight_color', $any($event.target).value)"
                          [disabled]="!canWrite()"
                        />
                      </div>
                    </div>
                    <label class="flex items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        [checked]="st['uppercase'] === true"
                        (change)="setOverride('uppercase', $any($event.target).checked)"
                        [disabled]="!canWrite()"
                      />
                      Capital letters
                    </label>
                  }
                  <div class="flex flex-wrap gap-2">
                    <button
                      type="button"
                      class="btn btn-secondary"
                      (click)="downloadCaptions('srt')"
                    >
                      Download SRT
                    </button>
                    <button
                      type="button"
                      class="btn btn-secondary"
                      (click)="downloadCaptions('vtt')"
                    >
                      Download VTT
                    </button>
                  </div>
                </div>
              }
              @case ('sound') {
                <div class="space-y-3">
                  <label class="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      [checked]="draft().audio.mute"
                      (change)="
                        patch({ audio: { ...draft().audio, mute: $any($event.target).checked } })
                      "
                      [disabled]="!canWrite()"
                    />
                    Mute the clip
                  </label>
                  <div>
                    <label class="label" for="gain"
                      >Volume change ({{ draft().audio.gain_db > 0 ? '+' : ''
                      }}{{ draft().audio.gain_db }} dB)</label
                    >
                    <input
                      id="gain"
                      type="range"
                      class="w-full"
                      min="-24"
                      max="24"
                      step="1"
                      [value]="draft().audio.gain_db"
                      (input)="
                        patch({ audio: { ...draft().audio, gain_db: +$any($event.target).value } })
                      "
                      [disabled]="!canWrite() || draft().audio.mute"
                    />
                    <p class="text-xs text-zinc-600 dark:text-zinc-400">
                      The final render is then levelled to a standard loudness (−14 LUFS), so this
                      is for balancing, not for making it louder overall.
                    </p>
                  </div>
                </div>
              }
              @case ('text') {
                <app-clip-metadata [clipId]="c.id" [canWrite]="canWrite()" />
              }
              @case ('renders') {
                <div class="space-y-4">
                  @if (canWrite()) {
                    <div class="flex flex-wrap gap-2">
                      <button
                        type="button"
                        class="btn btn-secondary"
                        [disabled]="busy()"
                        (click)="renderNow('preview')"
                      >
                        Make a quick preview
                      </button>
                      <button
                        type="button"
                        class="btn btn-primary"
                        [disabled]="busy()"
                        (click)="renderNow('final')"
                      >
                        Render the final clip
                      </button>
                    </div>
                    <p class="text-xs text-zinc-600 dark:text-zinc-400">
                      Unsaved changes are saved as a new version first. A preview is small and fast;
                      the final render is full size with levelled sound and a quality check.
                    </p>
                  }
                  @if (c.renders.length === 0) {
                    <p class="text-sm text-zinc-700 dark:text-zinc-300">Nothing rendered yet.</p>
                  }
                  <ul class="space-y-3" aria-label="Renders">
                    @for (r of c.renders; track r.id) {
                      <li
                        class="rounded-md border border-zinc-200 p-3 text-sm dark:border-zinc-800"
                        [attr.aria-label]="
                          (r.kind === 'final' ? 'Final render' : 'Preview') +
                          ' of version ' +
                          r.version
                        "
                      >
                        <div class="flex flex-wrap items-center justify-between gap-2">
                          <strong
                            >{{ r.kind === 'final' ? 'Final' : 'Preview' }} · version
                            {{ r.version }}</strong
                          >
                          <app-status-badge [status]="badge(r)" />
                        </div>
                        @if (r.state === 'running' || r.state === 'pending') {
                          @for (st of r.job?.stages ?? []; track st.stage) {
                            @if (st.status === 'running') {
                              <p class="mt-1 text-xs">{{ st.message ?? stageLabel(st.stage) }}</p>
                              <div
                                class="mt-1 h-1.5 overflow-hidden rounded bg-zinc-200 dark:bg-zinc-800"
                                role="progressbar"
                                [attr.aria-valuenow]="(st.progress * 100).toFixed(0)"
                                aria-valuemin="0"
                                aria-valuemax="100"
                                [attr.aria-label]="stageLabel(st.stage)"
                              >
                                <div
                                  class="h-full bg-accent"
                                  [style.width.%]="st.progress * 100"
                                ></div>
                              </div>
                            }
                          }
                        }
                        @if (r.state === 'failed') {
                          <p class="alert-error mt-2" role="alert">
                            {{ r.error ?? 'The render failed.' }}
                          </p>
                        }
                        @if (r.state === 'done') {
                          <p class="mt-1 text-xs text-zinc-600 dark:text-zinc-400">
                            {{ r.width }}×{{ r.height }} · {{ fmt(r.duration_ms ?? 0) }} ·
                            {{ bytes(r.size_bytes) }}
                            @if (r.loudness_lufs !== null) {
                              · {{ r.loudness_lufs.toFixed(1) }} LUFS
                            }
                          </p>
                          @if (r.qc_report; as qc) {
                            <details class="mt-2" [open]="r.qc_status !== 'pass'">
                              <summary class="cursor-pointer">
                                Quality checks: <strong>{{ qcLabel(r.qc_status) }}</strong>
                              </summary>
                              <ul class="mt-1 space-y-0.5 text-xs">
                                @for (k of qc.checks; track k.check) {
                                  <li
                                    [class.text-red-800]="k.severity === 'fail'"
                                    [class.dark:text-red-300]="k.severity === 'fail'"
                                    [class.text-amber-800]="k.severity === 'warn'"
                                    [class.dark:text-amber-300]="k.severity === 'warn'"
                                  >
                                    {{
                                      k.severity === 'pass'
                                        ? 'OK'
                                        : k.severity === 'warn'
                                          ? 'Look at this'
                                          : 'Problem'
                                    }}: {{ k.message }}
                                  </li>
                                }
                              </ul>
                            </details>
                          }
                          @if (r.play_url) {
                            <details class="mt-2">
                              <summary class="cursor-pointer">Watch it</summary>
                              <video
                                class="mt-2 max-h-96 w-full rounded bg-black"
                                controls
                                preload="metadata"
                                [src]="r.play_url"
                              ></video>
                            </details>
                          }
                          @if (r.download_url) {
                            <a class="btn btn-primary mt-2" [href]="r.download_url" download
                              >Download the MP4</a
                            >
                          }
                        }
                      </li>
                    }
                  </ul>
                </div>
              }
            }
          </div>
        </div>
      </div>

      @if (canWrite()) {
        <div
          class="mt-4 flex flex-wrap items-center gap-2 border-t border-zinc-200 pt-4 dark:border-zinc-800"
        >
          <button
            type="button"
            class="btn btn-primary"
            [disabled]="busy() || !dirty()"
            (click)="save()"
          >
            Save as version {{ c.current_version + 1 }}
          </button>
          <button
            type="button"
            class="btn btn-secondary"
            [disabled]="busy() || !dirty()"
            (click)="discard()"
          >
            Discard changes
          </button>
          <button
            type="button"
            class="btn btn-secondary ml-auto"
            [disabled]="busy()"
            (click)="delDlg().open()"
          >
            Delete this clip
          </button>
          <span class="text-xs text-zinc-600 dark:text-zinc-400">
            Keys: Space play · I / O start and end · ← → one frame · J K L back, pause, forward
          </span>
        </div>
        <app-confirm-dialog
          #delDialog
          heading="Delete this clip?"
          message="The clip, its versions and every file rendered from it are permanently removed. The video is not touched."
          actionLabel="Delete clip"
          [busy]="busy()"
          [error]="error()"
          (confirmed)="remove()"
        />
      }
    } @else {
      <p class="text-sm text-zinc-600 dark:text-zinc-400">Loading…</p>
    }
  `,
})
export class ClipEditorPage implements OnInit {
  readonly id = input.required<string>();

  private readonly api = inject(ClipsApi);
  private readonly media = inject(MediaApi);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);

  readonly mediaEl = viewChild<ElementRef<HTMLMediaElement>>('media');
  readonly stageEl = viewChild<ElementRef<HTMLDivElement>>('stage');
  readonly delDlg = viewChild.required<ConfirmDialogComponent>('delDialog');

  readonly frame = FRAME_MS;
  readonly tabs = TABS;
  readonly fmt = formatMs;
  readonly bytes = formatBytes;

  readonly clip = signal<Clip | null>(null);
  readonly source = signal<SourceDetail | null>(null);
  readonly project = signal<Project | null>(null);
  readonly templates = signal<CaptionTemplate[]>([]);
  readonly words = signal<ClipWord[]>([]);
  readonly preview = signal<PreviewStyle | null>(null);
  readonly peaks = signal<number[][]>([]);
  readonly cuts = signal<number[]>([]);
  readonly draft = signal<Draft>({
    title: '',
    start_ms: 0,
    end_ms: 1000,
    aspect: '9:16',
    crop: { mode: 'fill', zoom: 1, keyframes: [] },
    background: { type: 'blur', color: '#000000' },
    audio: { gain_db: 0, mute: false },
    caption_template_id: null,
    caption_overrides: {},
  });
  readonly tab = signal<Tab>('shape');
  readonly timeMs = signal(0);
  readonly playing = signal(false);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly loadError = signal('');
  readonly conflict = signal(false);
  readonly pickedWord = signal<ClipWord | null>(null);
  readonly stageW = signal(600);

  private stopAtMs: number | null = null;
  private previewTimer: ReturnType<typeof setTimeout> | null = null;
  private pollTimer: ReturnType<typeof setTimeout> | null = null;
  private ro: ResizeObserver | null = null;

  readonly canWrite = computed(() => {
    const p = this.project();
    return !!p && p.my_role !== 'viewer' && this.auth.user()?.role !== 'viewer';
  });
  readonly hasVideo = computed(() => this.source()?.has_video ?? true);
  readonly srcW = computed(() => this.source()?.width ?? 1920);
  readonly srcH = computed(() => this.source()?.height ?? 1080);
  readonly durationMs = computed(() => Math.round((this.source()?.duration_s ?? 0) * 1000));
  readonly stageRatio = computed(() =>
    this.hasVideo() ? `${this.srcW()} / ${this.srcH()}` : '16 / 9',
  );
  readonly dirty = computed(() => {
    const c = this.clip();
    return !!c && JSON.stringify(this.draft()) !== JSON.stringify(fromClip(c));
  });
  readonly win = computed(() => {
    const d = this.draft();
    if (!this.hasVideo()) return null;
    return windowAt(
      d.crop.keyframes,
      this.srcW(),
      this.srcH(),
      d.aspect,
      d.crop.zoom,
      Math.max(0, this.timeMs() - d.start_ms),
    );
  });
  readonly style = computed(() => this.preview()?.style ?? this.clip()?.style ?? null);
  readonly fonts = computed(() => this.preview()?.fonts ?? ['Liberation Sans']);
  readonly capsOn = computed(() => this.draft().caption_overrides['enabled'] !== false);
  readonly activeCue = computed(() => {
    const p = this.preview();
    if (!p) return null;
    const rel = this.timeMs() - this.draft().start_ms;
    return p.cues.find((c) => rel >= c.start_ms && rel <= c.end_ms) ?? null;
  });

  constructor() {
    // Keep the caption preview in step with every unsaved change, using the server's own compiler.
    effect(() => {
      const d = this.draft();
      // reading these is what makes the effect re-run when any of them changes
      void [d.caption_template_id, d.caption_overrides, d.start_ms, d.end_ms, d.aspect];
      untracked(() => this.schedulePreview());
    });
    effect(() => {
      const el = this.stageEl()?.nativeElement;
      if (el && typeof ResizeObserver !== 'undefined' && !this.ro) {
        this.ro = new ResizeObserver(() => this.stageW.set(el.clientWidth || 600));
        this.ro.observe(el);
        this.stageW.set(el.clientWidth || 600);
      }
    });
    this.destroyRef.onDestroy(() => {
      this.ro?.disconnect();
      if (this.previewTimer) clearTimeout(this.previewTimer);
      if (this.pollTimer) clearTimeout(this.pollTimer);
    });
  }

  async ngOnInit(): Promise<void> {
    try {
      const clip = await this.api.get(this.id());
      this.adopt(clip);
      const [src, project, templates, words] = await Promise.all([
        this.media.getSource(clip.source_id),
        this.media.getProject(clip.project_id),
        this.api.templates(),
        this.api.words(clip.id).catch(() => [] as ClipWord[]),
      ]);
      this.source.set(src);
      this.project.set(project);
      this.templates.set(templates);
      this.words.set(words);
      void this.loadSideData(src);
      if (clip.renders.some((r) => r.state === 'running' || r.state === 'pending')) this.pollSoon();
    } catch (e) {
      this.loadError.set(errorMessage(e));
    }
  }

  private async loadSideData(src: SourceDetail): Promise<void> {
    const get = async (url: string | null): Promise<unknown> => {
      if (!url) return null;
      try {
        return await (await fetch(url)).json();
      } catch {
        return null; // markers are a convenience; the editor works without them
      }
    };
    const wf = (await get(src.waveform_url)) as { peaks?: number[][] } | null;
    this.peaks.set(wf?.peaks ?? []);
    const sc = (await get(src.scenes_url)) as { cuts_ms?: number[] } | null;
    this.cuts.set(sc?.cuts_ms ?? []);
  }

  private adopt(clip: Clip): void {
    this.clip.set(clip);
    this.draft.set(fromClip(clip));
  }

  // ------------------------------------------------------------------ draft changes
  patch(p: Partial<Draft>): void {
    this.draft.update((d) => ({ ...d, ...p }));
  }

  setCrop(p: Partial<CropSpec>): void {
    this.draft.update((d) => ({ ...d, crop: { ...d.crop, ...p } }));
  }

  setTemplate(id: string): void {
    this.patch({
      caption_template_id: id,
      caption_overrides: this.capsOn() ? {} : { enabled: false },
    });
  }

  setOverride(key: string, value: unknown): void {
    this.patch({ caption_overrides: { ...this.draft().caption_overrides, [key]: value } });
  }

  audioBg(): string {
    return this.draft().background.type === 'solid' ? this.draft().background.color : '#1B1B2F';
  }

  setStart(ms: number): void {
    const d = this.draft();
    this.patch({ start_ms: snapToFrame(Math.max(0, Math.min(ms, d.end_ms - 1000))) });
  }

  setEnd(ms: number): void {
    const d = this.draft();
    const max = this.durationMs() || Number.MAX_SAFE_INTEGER;
    this.patch({ end_ms: snapToFrame(Math.min(max, Math.max(ms, d.start_ms + 1000))) });
  }

  typeTime(e: Event, which: 'start' | 'end'): void {
    const input = e.target as HTMLInputElement;
    const ms = parseTime(input.value);
    if (ms === null) {
      input.value = formatMs(which === 'start' ? this.draft().start_ms : this.draft().end_ms);
      this.error.set('Type a time like 1:23.4 or 83.4');
      return;
    }
    this.error.set('');
    if (which === 'start') this.setStart(ms);
    else this.setEnd(ms);
    input.value = formatMs(which === 'start' ? this.draft().start_ms : this.draft().end_ms);
  }

  discard(): void {
    const c = this.clip();
    if (c) this.draft.set(fromClip(c));
    this.error.set('');
    this.conflict.set(false);
  }

  inside(w: ClipWord): boolean {
    return w.start_ms >= this.draft().start_ms - 40 && w.end_ms <= this.draft().end_ms + 40;
  }

  pickWord(w: ClipWord): void {
    this.pickedWord.set(w);
    this.seekTo(w.start_ms);
  }

  removeKeyframe(k: CropKeyframe): void {
    this.setCrop({ keyframes: this.draft().crop.keyframes.filter((x) => x.t_ms !== k.t_ms) });
  }

  // ------------------------------------------------------------------ captions on the preview
  private schedulePreview(): void {
    if (!this.clip()) return;
    if (this.previewTimer) clearTimeout(this.previewTimer);
    this.previewTimer = setTimeout(() => void this.refreshPreview(), 250);
  }

  private async refreshPreview(): Promise<void> {
    const c = this.clip();
    if (!c) return;
    const d = this.draft();
    const [w, h] = ASPECT_SIZES[d.aspect];
    try {
      this.preview.set(
        await this.api.previewStyle(c.id, {
          width: w,
          height: h,
          start_ms: d.start_ms,
          end_ms: d.end_ms,
          caption_template_id: d.caption_template_id,
          caption_overrides: d.caption_overrides,
        }),
      );
    } catch {
      /* an invalid value is shown by the control that caused it; the last good preview stays */
    }
  }

  captionBoxStyle(w: { w: number }): Record<string, string> {
    const p = this.preview();
    const outW = p?.output_width ?? ASPECT_SIZES[this.draft().aspect][0];
    const outH = p?.output_height ?? ASPECT_SIZES[this.draft().aspect][1];
    const rectPx = (w.w / this.srcW()) * this.stageW();
    const scale = rectPx / outW;
    return { width: `${outW}px`, height: `${outH}px`, transform: `scale(${scale})` };
  }

  containerStyle(): Record<string, string> {
    return this.preview()?.css.container ?? {};
  }

  textStyle(): Record<string, string> {
    return this.preview()?.css.text ?? {};
  }

  wordColor(word: { text: string; start_ms: number; end_ms: number }): string | null {
    const p = this.preview();
    if (!p) return null;
    const rel = this.timeMs() - this.draft().start_ms;
    const a = p.css.animation;
    if (a === 'karaoke') return word.end_ms <= rel ? p.css.active_color : null;
    if ((a === 'highlight' || a === 'pop') && rel >= word.start_ms && rel < word.end_ms)
      return p.css.active_color;
    const key = word.text.toLowerCase().replace(/\W+/g, '');
    return p.css.keywords.includes(key) ? p.css.keyword_color : null;
  }

  // ------------------------------------------------------------------ playback
  private m(): HTMLMediaElement | null {
    return this.mediaEl()?.nativeElement ?? null;
  }

  onTime(): void {
    const el = this.m();
    if (!el) return;
    this.timeMs.set(Math.round(el.currentTime * 1000));
    this.playing.set(!el.paused);
    if (this.stopAtMs !== null && el.currentTime * 1000 >= this.stopAtMs) {
      el.pause();
      this.playing.set(false);
      this.stopAtMs = null;
    }
  }

  async onMediaError(): Promise<void> {
    // signed links expire: fetch fresh ones and carry on from the same place
    const at = this.m()?.currentTime ?? 0;
    const c = this.clip();
    if (!c) return;
    try {
      this.source.set(await this.media.getSource(c.source_id));
      queueMicrotask(() => {
        const el = this.m();
        if (el) el.currentTime = at;
      });
    } catch {
      /* leave the player as it is */
    }
  }

  seekTo(ms: number): void {
    const el = this.m();
    this.timeMs.set(ms);
    if (el) el.currentTime = ms / 1000;
  }

  playClip(): void {
    const el = this.m();
    this.seekTo(this.draft().start_ms);
    this.stopAtMs = this.draft().end_ms;
    void el?.play().catch(() => undefined);
  }

  togglePlay(): void {
    const el = this.m();
    if (!el) return;
    this.stopAtMs = null;
    if (el.paused) void el.play().catch(() => undefined);
    else el.pause();
    this.playing.set(!el.paused);
  }

  // ------------------------------------------------------------------ dragging the crop window
  beginPan(e: PointerEvent): void {
    if (!this.canWrite()) return;
    e.preventDefault();
    const stage = this.stageEl()?.nativeElement;
    if (!stage) return;
    const move = (ev: PointerEvent): void => {
      const r = stage.getBoundingClientRect();
      const d = this.draft();
      const [cx, cy] = centreFor(
        (ev.clientX - r.left) / r.width,
        (ev.clientY - r.top) / r.height,
        this.srcW(),
        this.srcH(),
        d.aspect,
        d.crop.zoom,
      );
      const rel = Math.max(0, Math.min(d.end_ms - d.start_ms, this.timeMs() - d.start_ms));
      const near = d.crop.keyframes.find((k) => Math.abs(k.t_ms - rel) < 300);
      this.setCrop({
        keyframes: upsertKeyframe(d.crop.keyframes, {
          t_ms: near?.t_ms ?? snapToFrame(rel),
          cx,
          cy,
        }),
      });
    };
    const up = (): void => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }

  @HostListener('document:keydown', ['$event'])
  onKey(e: KeyboardEvent): void {
    const t = e.target as HTMLElement | null;
    if (
      t &&
      (t.closest('input, textarea, select, [contenteditable="true"]') ||
        t.getAttribute('role') === 'slider')
    )
      return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const el = this.m();
    switch (e.key) {
      case ' ':
        this.togglePlay();
        break;
      case 'i':
      case 'I':
        if (this.canWrite()) this.setStart(this.timeMs());
        break;
      case 'o':
      case 'O':
        if (this.canWrite()) this.setEnd(this.timeMs());
        break;
      case 'ArrowLeft':
        this.seekTo(Math.max(0, this.timeMs() - (e.shiftKey ? 1000 : FRAME_MS)));
        break;
      case 'ArrowRight':
        this.seekTo(this.timeMs() + (e.shiftKey ? 1000 : FRAME_MS));
        break;
      case 'j':
      case 'J':
        this.seekTo(Math.max(0, this.timeMs() - 5000));
        break;
      case 'k':
      case 'K':
        el?.pause();
        break;
      case 'l':
      case 'L':
        this.seekTo(this.timeMs() + 5000);
        break;
      default:
        return;
    }
    e.preventDefault();
  }

  // ------------------------------------------------------------------ saving and rendering
  private async act(fn: () => Promise<void>): Promise<boolean> {
    this.busy.set(true);
    this.error.set('');
    this.conflict.set(false);
    try {
      await fn();
      return true;
    } catch (e) {
      this.conflict.set(apiError(e)?.code === 'version_conflict');
      this.error.set(errorMessage(e));
      return false;
    } finally {
      this.busy.set(false);
    }
  }

  async save(): Promise<boolean> {
    const c = this.clip();
    if (!c) return false;
    return this.act(async () => {
      const d = this.draft();
      const saved = await this.api.saveVersion(c.id, {
        base_version: c.current_version,
        title: d.title,
        start_ms: d.start_ms,
        end_ms: d.end_ms,
        aspect: d.aspect,
        crop: d.crop,
        background: d.background,
        audio: d.audio,
        caption_template_id: d.caption_template_id,
        caption_overrides: d.caption_overrides,
      });
      this.adopt(saved);
      this.words.set(await this.api.words(saved.id).catch(() => []));
    });
  }

  async reload(): Promise<void> {
    this.conflict.set(false);
    this.error.set('');
    try {
      this.adopt(await this.api.get(this.id()));
    } catch (e) {
      this.error.set(errorMessage(e));
    }
  }

  async renderNow(kind: 'preview' | 'final'): Promise<void> {
    if (this.dirty() && !(await this.save())) return;
    const c = this.clip();
    if (!c) return;
    const ok = await this.act(async () => {
      await this.api.render(c.id, kind, c.current_version);
      this.adopt(await this.api.get(c.id));
    });
    if (ok) {
      this.tab.set('renders');
      this.pollSoon();
    }
  }

  private pollSoon(): void {
    if (this.pollTimer) clearTimeout(this.pollTimer);
    this.pollTimer = setTimeout(() => void this.pollRenders(), 1500);
  }

  private async pollRenders(): Promise<void> {
    const c = this.clip();
    if (!c) return;
    try {
      const fresh = await this.api.get(c.id);
      // keep the person's unsaved edits; only the renders move on by themselves
      this.clip.set({ ...c, renders: fresh.renders });
      if (fresh.renders.some((r) => r.state === 'running' || r.state === 'pending'))
        this.pollSoon();
    } catch {
      this.pollSoon();
    }
  }

  async downloadCaptions(format: 'srt' | 'vtt'): Promise<void> {
    const c = this.clip();
    if (c) await this.act(() => this.api.captionsFile(c.id, format));
  }

  async remove(): Promise<void> {
    const c = this.clip();
    if (!c) return;
    const ok = await this.act(async () => {
      await this.api.remove(c.id);
      this.delDlg().close();
      await this.router.navigate(['/sources', c.source_id]);
    });
    if (!ok) return;
  }

  badge(r: ClipRender): string {
    return r.state === 'done'
      ? 'succeeded'
      : r.state === 'running'
        ? 'running'
        : r.state === 'pending'
          ? 'pending'
          : r.state;
  }

  qcLabel(s: ClipRender['qc_status']): string {
    return s === 'pass'
      ? 'all passed'
      : s === 'warn'
        ? 'passed with warnings'
        : s === 'fail'
          ? 'failed'
          : 'not checked';
  }

  stageLabel(stage: string): string {
    return (
      (
        {
          clip_subtitles: 'Writing the captions',
          clip_loudness: 'Measuring the loudness',
          clip_render: 'Rendering',
          clip_thumbnail: 'Making a thumbnail',
          clip_qc: 'Checking the result',
        } as Record<string, string>
      )[stage] ?? stage
    );
  }
}
