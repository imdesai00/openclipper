import { HttpErrorResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { HighlightsApi } from '../../core/highlights-api.service';
import { HighlightCandidate, HighlightRun, SourceDetail } from '../../core/models';
import { HighlightsSection } from './highlights.section';

const cand = (over: Partial<HighlightCandidate> = {}): HighlightCandidate => ({
  id: 'c1',
  rank: 1,
  start_ms: 65_000,
  end_ms: 95_000,
  duration_ms: 30_000,
  text: 'Make it so small that it feels silly not to do it.',
  category: 'education',
  hook: 'The trick nobody tells you',
  title: 'Start absurdly small',
  explanation: 'A clear, self-contained piece of advice.',
  llm_score: 90,
  standalone_score: 80,
  confidence: 70,
  final_score: 84.4,
  score_parts: {
    llm: { value: 90, weight: 0.6 },
    energy: { value: 50, weight: 0.4 },
    unavailable: ['scene'],
  },
  provider_name: 'Ollama',
  model_name: 'qwen2.5:0.5b',
  status: 'new',
  ...over,
});

const run = (
  cands: HighlightCandidate[] = [cand()],
  stats: Record<string, unknown> = {},
): HighlightRun => ({
  run: {
    id: 'r1',
    source_id: 's1',
    transcript_id: 't1',
    created_at: '2026-11-27T10:00:00Z',
    candidate_count: cands.length,
    request: {},
    stats: {
      windows: 1,
      windows_failed: [],
      raw_candidates: cands.length,
      dropped: {},
      kept: cands.length,
      models: {},
      scene_cuts: 0,
      cost_est: 0,
      tokens_in: 0,
      tokens_out: 0,
      ...stats,
    } as HighlightRun['run']['stats'],
  },
  candidates: cands,
  score_note: 'AI relevance score, not an engagement prediction.',
});

const source = (over: Partial<SourceDetail> = {}): SourceDetail =>
  ({
    id: 's1',
    project_id: 'p1',
    status: 'ready',
    transcript: { version: 1 },
    highlights_job: null,
    highlights: null,
    ...over,
  }) as SourceDetail;

describe('HighlightsSection', () => {
  const api = { find: vi.fn(), run: vi.fn(), review: vi.fn() };

  beforeEach(() => {
    Object.values(api).forEach((m) => m.mockReset());
    TestBed.configureTestingModule({ providers: [{ provide: HighlightsApi, useValue: api }] });
  });

  async function render(src: SourceDetail, canWrite = true) {
    const f = TestBed.createComponent(HighlightsSection);
    f.componentRef.setInput('source', src);
    f.componentRef.setInput('canWrite', canWrite);
    f.detectChanges();
    await f.whenStable();
    f.detectChanges();
    return f;
  }
  const text = (f: { nativeElement: HTMLElement }) => f.nativeElement.textContent ?? '';
  const button = (f: { nativeElement: HTMLElement }, label: string) =>
    f.nativeElement.querySelector(`button[aria-label="${label}"]`) as HTMLButtonElement | null;

  it('asks for a transcript first instead of showing a form that cannot work', async () => {
    const f = await render(source({ transcript: null }));
    expect(text(f)).toContain('Transcribe this video first');
    expect(f.nativeElement.querySelector('form')).toBeNull();
  });

  it('starts a search with what the person typed and tells the page to reload', async () => {
    api.find.mockResolvedValue({ reused: false, run: null, job: {} });
    const f = await render(source());
    const changed = vi.fn();
    f.componentInstance.changed.subscribe(changed);
    f.componentInstance.prompt = ' the funniest moments ';
    f.componentInstance.count = 5;
    f.componentInstance.minS = 30;
    f.componentInstance.maxS = 60;
    f.componentInstance.toggle('humour');
    await f.componentInstance.start();
    expect(api.find).toHaveBeenCalledWith('s1', {
      prompt: 'the funniest moments',
      categories: ['humour'],
      max_candidates: 5,
      min_seconds: 30,
      max_seconds: 60,
    });
    expect(changed).toHaveBeenCalled();
  });

  it('shows the earlier run when the same request was already answered', async () => {
    api.find.mockResolvedValue({ reused: true, run: run().run, job: null });
    api.run.mockResolvedValue(run());
    const f = await render(source());
    await f.componentInstance.start();
    f.detectChanges();
    expect(api.run).toHaveBeenCalledWith('s1', 'r1');
    expect(text(f)).toContain('Start absurdly small');
  });

  it('shows live progress while the job runs, and the real reason when it fails', async () => {
    const stage = (
      stage: string,
      status: string,
      progress: number,
      message: string | null = null,
    ) => ({
      stage,
      position: 0,
      status,
      progress,
      message,
      attempts: 1,
      error_code: null,
      error_message: null,
    });
    const job = (status: string, extra = {}) =>
      ({
        id: 'j',
        status,
        stages: [
          stage('detect_scenes', 'succeeded', 1),
          stage('highlights', 'running', 0.5, 'Reading part 2 of 4'),
        ],
        ...extra,
      }) as never;
    const running = await render(source({ highlights_job: job('running') }));
    expect(
      running.nativeElement.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow'),
    ).toBe('50');
    expect(text(running)).toContain('Reading part 2 of 4');
    expect(running.nativeElement.querySelector('form')).toBeNull(); // no second job while one runs

    const failed = await render(
      source({
        highlights_job: job('failed', {
          error_message: 'No part of the transcript could be analysed.',
        }),
      }),
    );
    expect(failed.nativeElement.querySelector('[role=alert]')?.textContent).toContain(
      'could be analysed',
    );
    expect(failed.nativeElement.querySelector('form')).not.toBeNull(); // and it can be tried again
  });

  it('shows each clip with its real transcript text, its time range and an honest score label', async () => {
    api.run.mockResolvedValue(run());
    const f = await render(source({ highlights: { run_id: 'r1', created_at: '', count: 1 } }));
    const t = text(f);
    expect(t).toContain('Make it so small that it feels silly not to do it.');
    expect(t).toContain('1:05–1:35');
    expect(t).toContain('30 s');
    expect(t).toContain('AI relevance score');
    expect(t).toContain('not an engagement prediction');
    expect(t).toContain('Education');
    expect(t).toContain('Model’s own score: 90 (weight 60%)');
    expect(t).toContain('rules of thumb, not measured');
  });

  it('says plainly when parts could not be analysed and what was left out', async () => {
    api.run.mockResolvedValue(
      run([cand()], {
        windows: 4,
        windows_failed: [{ window: 1, code: 'llm_failed', message: 'x' }],
        dropped: { unknown_segment: 3, overlaps_stronger: 1, reversed: 0 },
      }),
    );
    const f = await render(source({ highlights: { run_id: 'r1', created_at: '', count: 1 } }));
    expect(text(f)).toContain('1 of 4 parts of the video could not be analysed');
    expect(text(f)).toContain('3 named a part of the video it was never shown');
    expect(text(f)).toContain('1 overlapped a stronger clip');
    expect(text(f)).not.toContain('ended before it started');
  });

  it('asks the page to play the clip when Preview is pressed', async () => {
    api.run.mockResolvedValue(run());
    const f = await render(source({ highlights: { run_id: 'r1', created_at: '', count: 1 } }));
    const preview = vi.fn();
    f.componentInstance.preview.subscribe(preview);
    button(f, 'Preview clip 1')!.click();
    expect(preview).toHaveBeenCalledWith({ start: 65, end: 95 });
  });

  it('accepts and un-accepts a clip, keeping the button state in sync', async () => {
    api.run.mockResolvedValue(run());
    api.review
      .mockResolvedValueOnce(cand({ status: 'accepted' }))
      .mockResolvedValueOnce(cand({ status: 'new' }));
    const f = await render(source({ highlights: { run_id: 'r1', created_at: '', count: 1 } }));
    button(f, 'Accept clip 1')!.click();
    await f.whenStable();
    f.detectChanges();
    expect(api.review).toHaveBeenCalledWith('c1', 'accepted');
    expect(button(f, 'Accept clip 1')!.getAttribute('aria-pressed')).toBe('true');
    expect(text(f)).toContain('Accepted');
    button(f, 'Accept clip 1')!.click();
    await f.whenStable();
    expect(api.review).toHaveBeenLastCalledWith('c1', 'new');
  });

  it('shows a failed review as an error and leaves the clip as it was', async () => {
    api.run.mockResolvedValue(run());
    api.review.mockRejectedValue(
      new HttpErrorResponse({
        status: 403,
        error: { code: 'forbidden', message: 'You cannot do that.' },
      }),
    );
    const f = await render(source({ highlights: { run_id: 'r1', created_at: '', count: 1 } }));
    button(f, 'Reject clip 1')!.click();
    await f.whenStable();
    f.detectChanges();
    expect(f.nativeElement.querySelector('[role=alert]')?.textContent).toContain(
      'You cannot do that.',
    );
    expect(button(f, 'Reject clip 1')!.getAttribute('aria-pressed')).toBe('false');
  });

  it('lets viewers look and preview but not start searches or review', async () => {
    api.run.mockResolvedValue(run());
    const f = await render(
      source({ highlights: { run_id: 'r1', created_at: '', count: 1 } }),
      false,
    );
    expect(f.nativeElement.querySelector('form')).toBeNull();
    expect(button(f, 'Accept clip 1')).toBeNull();
    expect(button(f, 'Reject clip 1')).toBeNull();
    expect(button(f, 'Preview clip 1')).not.toBeNull();
  });

  it('says so when the model returned nothing usable', async () => {
    api.run.mockResolvedValue(run([]));
    const f = await render(source({ highlights: { run_id: 'r1', created_at: '', count: 0 } }));
    expect(text(f)).toContain('Nothing usable came back');
  });
});
