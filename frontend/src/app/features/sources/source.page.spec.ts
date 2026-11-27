import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthService } from '../../core/auth.service';
import { MediaApi } from '../../core/media-api.service';
import { Project, SourceDetail } from '../../core/models';
import { RealtimeService } from '../../core/realtime.service';
import { HighlightsApi } from '../../core/highlights-api.service';
import { TranscriptApi } from '../../core/transcript-api.service';
import { SourcePage } from './source.page';
import { Subject } from 'rxjs';

const stage = (stage: string, status: string, extra = {}) => ({
  stage,
  position: 0,
  status,
  progress: status === 'succeeded' ? 1 : 0,
  message: null,
  attempts: 1,
  error_code: null,
  error_message: null,
  ...extra,
});

const base = (over: Partial<SourceDetail> = {}): SourceDetail =>
  ({
    id: 's1',
    project_id: 'p1',
    kind: 'upload',
    title: 'Episode 12',
    origin_url: null,
    original_filename: 'ep12.mp4',
    status: 'ready',
    error_code: null,
    error_message: null,
    size_bytes: 1_500_000,
    duration_s: 3725,
    width: 1920,
    height: 1080,
    container: 'mov,mp4',
    video_codec: 'h264',
    audio_codec: 'aac',
    has_audio: true,
    has_video: true,
    created_at: '',
    thumbnail_url: null,
    proxy_url: '/api/v1/media/x?t=y',
    waveform_url: null,
    media_hash: 'abcdef0123456789abcdef',
    job: null,
    transcription_job: null,
    transcript: null,
    highlights_job: null,
    highlights: null,
    ...over,
  }) as SourceDetail;

describe('SourcePage', () => {
  const api = {
    getSource: vi.fn(),
    getProject: vi.fn(),
    retryJob: vi.fn(),
    cancelJob: vi.fn(),
    reprocess: vi.fn(),
    deleteSource: vi.fn(),
  };
  const transcriptApi = { asrStatus: vi.fn(), get: vi.fn() };
  const role = signal<'editor' | 'viewer'>('editor');
  const events = new Subject<unknown>();

  beforeEach(() => {
    Object.values(api).forEach((m) => m.mockReset());
    transcriptApi.asrStatus.mockReset().mockResolvedValue({
      available: true,
      device: 'cpu',
      compute_type: 'int8',
      default_model: 'small',
      default_language: null,
      models: [],
      languages: [],
    });
    role.set('editor');
    api.getProject.mockResolvedValue({ id: 'p1', name: 'Show', my_role: 'owner' } as Project);
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        { provide: MediaApi, useValue: api },
        { provide: TranscriptApi, useValue: transcriptApi },
        { provide: HighlightsApi, useValue: { find: vi.fn(), run: vi.fn(), review: vi.fn() } },
        { provide: AuthService, useValue: { user: () => ({ role: role() }) } },
        { provide: RealtimeService, useValue: { events } },
      ],
    });
  });

  async function render(src: SourceDetail) {
    api.getSource.mockResolvedValue(src);
    const f = TestBed.createComponent(SourcePage);
    f.componentRef.setInput('id', 's1');
    f.detectChanges();
    await f.whenStable();
    await vi.waitFor(() => expect(api.getProject).toHaveBeenCalled());
    await f.whenStable();
    f.detectChanges();
    return f.nativeElement as HTMLElement;
  }

  it('plays the proxy and shows real facts about the file', async () => {
    const el = await render(base());
    expect(el.querySelector('video')?.getAttribute('src')).toBe('/api/v1/media/x?t=y');
    expect(el.textContent).toContain('1:02:05');
    expect(el.textContent).toContain('1920 × 1080');
    expect(el.textContent).toContain('abcdef0123456789…');
  });

  it('explains a failure and offers Retry (not Cancel)', async () => {
    const el = await render(
      base({
        status: 'failed',
        proxy_url: null,
        error_message: 'This file is not a readable video',
        job: {
          id: 'j1',
          project_id: 'p1',
          source_id: 's1',
          kind: 'analyze_source',
          status: 'failed',
          error_code: 'not_media',
          error_message: 'x',
          started_at: null,
          finished_at: null,
          stages: [
            stage('validate', 'failed', { error_message: 'This file is not a readable video' }),
            stage('proxy', 'pending'),
          ],
        },
      } as Partial<SourceDetail>),
    );
    expect(el.querySelector('video')).toBeNull();
    expect(el.querySelector('[role=alert]')?.textContent).toContain('not a readable video');
    const labels = Array.from(el.querySelectorAll('aside button')).map((b) =>
      b.textContent?.trim(),
    );
    expect(labels).toContain('Retry');
    expect(labels).not.toContain('Cancel');
  });

  it('offers Cancel while a job is running and shows stage progress', async () => {
    const el = await render(
      base({
        status: 'processing',
        proxy_url: null,
        job: {
          id: 'j1',
          project_id: 'p1',
          source_id: 's1',
          kind: 'analyze_source',
          status: 'running',
          error_code: null,
          error_message: null,
          started_at: null,
          finished_at: null,
          stages: [stage('validate', 'succeeded'), stage('proxy', 'running', { progress: 0.42 })],
        },
      } as Partial<SourceDetail>),
    );
    const labels = Array.from(el.querySelectorAll('aside button')).map((b) =>
      b.textContent?.trim(),
    );
    expect(labels).toContain('Cancel');
    expect(el.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow')).toBe('42');
  });

  it('applies live progress events to the right stage only', async () => {
    const f = TestBed.createComponent(SourcePage);
    api.getSource.mockResolvedValue(
      base({
        status: 'processing',
        proxy_url: null,
        job: {
          id: 'j1',
          project_id: 'p1',
          source_id: 's1',
          kind: 'analyze_source',
          status: 'running',
          error_code: null,
          error_message: null,
          started_at: null,
          finished_at: null,
          stages: [stage('validate', 'succeeded'), stage('proxy', 'running')],
        },
      } as Partial<SourceDetail>),
    );
    f.componentRef.setInput('id', 's1');
    f.detectChanges();
    await f.whenStable();
    events.next({ type: 'job.progress', source_id: 'other', stage: 'proxy', progress: 0.9 });
    events.next({ type: 'job.progress', source_id: 's1', stage: 'proxy', progress: 0.75 });
    f.detectChanges();
    expect(f.nativeElement.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow')).toBe(
      '75',
    );
  });

  it('gives viewers no way to change anything', async () => {
    role.set('viewer');
    const el = await render(base());
    expect(el.querySelectorAll('button')).toHaveLength(0); // no actions and no hidden dialog either
  });
});
