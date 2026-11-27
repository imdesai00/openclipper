import { HttpErrorResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { SourceDetail, TranscriptMeta, TranscriptPage } from '../../core/models';
import { TranscriptApi } from '../../core/transcript-api.service';
import { TranscriptSection } from './transcript.section';

const meta = (over: Partial<TranscriptMeta> = {}): TranscriptMeta => ({
  id: 't1',
  source_id: 's1',
  version: 1,
  kind: 'asr',
  is_current: true,
  engine: 'faster-whisper',
  model: 'tiny',
  language: 'en',
  language_probability: 0.93,
  requested_language: null,
  duration_s: 20,
  segment_count: 2,
  word_count: 6,
  based_on_version: null,
  created_at: '2026-11-27T10:00:00Z',
  ...over,
});

const page = (): TranscriptPage => ({
  transcript: meta(),
  offset: 0,
  segments: [
    {
      id: 'a',
      idx: 0,
      start_ms: 0,
      end_ms: 2000,
      text: ' Hello there world.',
      edited: false,
      words: [],
    },
    {
      id: 'b',
      idx: 1,
      start_ms: 5000,
      end_ms: 7000,
      text: ' Second segment.',
      edited: true,
      words: [],
    },
  ],
});

const source = (over: Partial<SourceDetail> = {}): SourceDetail =>
  ({
    id: 's1',
    project_id: 'p1',
    status: 'ready',
    has_audio: true,
    has_video: true,
    transcription_job: null,
    transcript: null,
    ...over,
  }) as SourceDetail;

const asr = (over = {}) => ({
  available: true,
  device: 'cpu',
  compute_type: 'int8',
  default_model: 'small',
  default_language: null,
  models: [
    { name: 'tiny', mb: 75, multilingual: true, note: '', downloaded: true },
    { name: 'small', mb: 465, multilingual: true, note: '', downloaded: false },
  ],
  languages: ['en', 'es'],
  ...over,
});

describe('TranscriptSection', () => {
  const api = {
    asrStatus: vi.fn(),
    get: vi.fn(),
    transcribe: vi.fn(),
    edit: vi.fn(),
    versions: vi.fn(),
    restore: vi.fn(),
    exportFile: vi.fn(),
  };

  beforeEach(() => {
    Object.values(api).forEach((m) => m.mockReset());
    api.asrStatus.mockResolvedValue(asr());
    api.get.mockResolvedValue(page());
    TestBed.configureTestingModule({ providers: [{ provide: TranscriptApi, useValue: api }] });
  });

  async function render(src: SourceDetail, canWrite = true, currentS = 0) {
    const f = TestBed.createComponent(TranscriptSection);
    f.componentRef.setInput('source', src);
    f.componentRef.setInput('canWrite', canWrite);
    f.componentRef.setInput('currentS', currentS);
    f.detectChanges();
    await f.whenStable();
    f.detectChanges();
    return f;
  }
  const text = (f: { nativeElement: HTMLElement }) => f.nativeElement.textContent ?? '';
  const button = (f: { nativeElement: HTMLElement }, label: string) =>
    Array.from(f.nativeElement.querySelectorAll('button')).find((b) =>
      (b.textContent ?? '').trim().startsWith(label),
    ) as HTMLButtonElement | undefined;

  it('offers to transcribe with the default model, and says which models are not downloaded yet', async () => {
    const f = await render(source());
    expect(button(f, 'Transcribe')).toBeTruthy();
    expect(text(f)).toContain('small (465 MB, downloads on first use)');
    expect(text(f)).toContain('tiny (75 MB)');
  });

  it('starts a job with the chosen model and language, then tells the page to reload', async () => {
    api.transcribe.mockResolvedValue({ reused: false, reason: '', transcript: null, job: {} });
    const f = await render(source());
    const changed = vi.fn();
    f.componentInstance.changed.subscribe(changed);
    f.componentInstance.model = 'tiny';
    f.componentInstance.lang = 'es';
    await f.componentInstance.start(false);
    expect(api.transcribe).toHaveBeenCalledWith('s1', {
      language: 'es',
      model: 'tiny',
      force: false,
    });
    expect(changed).toHaveBeenCalled();
  });

  it('tells the user when the transcription service is not running, and blocks the button', async () => {
    api.asrStatus.mockResolvedValue(asr({ available: false }));
    const f = await render(source());
    expect(text(f)).toContain('transcription service is not running');
    expect(button(f, 'Transcribe')?.disabled).toBe(true);
  });

  it('explains a file without sound instead of showing a form', async () => {
    const f = await render(source({ has_audio: false }));
    expect(text(f)).toContain('nothing to transcribe');
    expect(button(f, 'Transcribe')).toBeUndefined();
  });

  it('shows progress while the job runs, and the real failure message when it fails', async () => {
    const stage = (stage: string, status: string, progress: number) => ({
      stage,
      position: 0,
      status,
      progress,
      message: null,
      attempts: 1,
      error_code: null,
      error_message: null,
    });
    const job = (status: string, extra = {}) =>
      ({
        id: 'j',
        stages: [stage('extract_audio', 'succeeded', 1), stage('transcribe', 'running', 0.4)],
        status,
        ...extra,
      }) as never;
    const running = await render(source({ transcription_job: job('running') }));
    expect(
      running.nativeElement.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow'),
    ).toBe('40');
    expect(text(running)).toContain('Recognise the speech');

    const failed = await render(
      source({
        transcription_job: job('failed', {
          error_message: 'No speech was found in this recording.',
        }),
      }),
    );
    expect(failed.nativeElement.querySelector('[role=alert]')?.textContent).toContain(
      'No speech was found',
    );
    expect(button(failed, 'Transcribe')).toBeTruthy(); // and it can be tried again
  });

  it('shows the transcript, highlights the segment being played, and seeks on click', async () => {
    const f = await render(source({ transcript: { version: 1 } as never }), true, 5.5);
    expect(text(f)).toContain('Hello there world.');
    expect(text(f)).toContain('English');
    const items = f.nativeElement.querySelectorAll('ol[aria-label="Transcript segments"] > li');
    expect(items[0].getAttribute('aria-current')).toBeNull();
    expect(items[1].getAttribute('aria-current')).toBe('true');
    const seek = vi.fn();
    f.componentInstance.seek.subscribe(seek);
    (items[1].querySelector('button') as HTMLButtonElement).click();
    expect(seek).toHaveBeenCalledWith(5);
  });

  it('edits one segment against the version it opened, then asks the page to reload', async () => {
    api.edit.mockResolvedValue(meta({ version: 2, kind: 'edit' }));
    const f = await render(source({ transcript: { version: 1 } as never }));
    const changed = vi.fn();
    f.componentInstance.changed.subscribe(changed);
    button(f, 'Edit text')!.click();
    f.detectChanges();
    (
      f.nativeElement.querySelector('button[aria-label^="Edit segment"]') as HTMLButtonElement
    ).click();
    f.detectChanges();
    f.componentInstance.draft.set('Hello there everyone.');
    await f.componentInstance.saveEdit(f.componentInstance.segments()[0]);
    expect(api.edit).toHaveBeenCalledWith('s1', 1, [
      { segment_idx: 0, text: 'Hello there everyone.' },
    ]);
    expect(changed).toHaveBeenCalled();
    expect(f.componentInstance.editingIdx()).toBeNull();
  });

  it('keeps the text and says so when someone else saved first', async () => {
    api.edit.mockRejectedValue(
      new HttpErrorResponse({
        status: 409,
        error: {
          code: 'version_conflict',
          message: 'The transcript changed since you opened it. Reload and try again.',
        },
      }),
    );
    const f = await render(source({ transcript: { version: 1 } as never }));
    f.componentInstance.startEdit(f.componentInstance.segments()[0]);
    f.componentInstance.draft.set('mine');
    await f.componentInstance.saveEdit(f.componentInstance.segments()[0]);
    f.detectChanges();
    expect(f.componentInstance.conflict()).toBe(true);
    expect(f.componentInstance.editingIdx()).toBe(0); // the user's draft is still there
    expect(text(f)).toContain('changed since you opened it');
    expect(button(f, 'Show the latest')).toBeTruthy();
  });

  it('gives viewers the text and exports but no way to change it', async () => {
    const f = await render(source({ transcript: { version: 1 } as never }), false);
    expect(text(f)).toContain('Hello there world.');
    expect(button(f, 'Edit text')).toBeUndefined();
    expect(button(f, 'Transcribe')).toBeUndefined();
    expect(button(f, 'Export SRT')).toBeTruthy();
  });

  it('lists versions and restores an older one', async () => {
    api.versions.mockResolvedValue([
      meta({ id: 't2', version: 2, kind: 'edit' }),
      meta({ is_current: false }),
    ]);
    api.restore.mockResolvedValue(meta({ version: 3, kind: 'restore' }));
    const f = await render(source({ transcript: { version: 2 } as never }));
    api.get.mockClear();
    button(f, 'History')!.click();
    await f.whenStable();
    f.detectChanges();
    expect(text(f)).toContain('Version 2');
    expect(text(f)).toContain('(current)');
    button(f, 'Restore this version')!.click();
    await f.whenStable();
    expect(api.restore).toHaveBeenCalledWith('s1', 1);
  });
});
