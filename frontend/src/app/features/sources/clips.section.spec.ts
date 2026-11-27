import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ClipsApi } from '../../core/clips-api.service';
import { ClipSummary, SourceDetail } from '../../core/models';
import { ClipsSection } from './clips.section';

const clip = (over: Partial<ClipSummary> = {}): ClipSummary => ({
  id: 'c1',
  source_id: 's1',
  title: 'Sleep beats diet',
  current_version: 3,
  start_ms: 65_000,
  end_ms: 95_000,
  aspect: '9:16',
  created_at: '',
  candidate_id: null,
  best_render_state: 'pass',
  thumbnail_url: null,
  ...over,
});

const source = (over: Partial<SourceDetail> = {}): SourceDetail =>
  ({ id: 's1', status: 'ready', ...over }) as SourceDetail;

describe('ClipsSection', () => {
  const api = { list: vi.fn(), create: vi.fn() };

  beforeEach(() => {
    api.list.mockReset().mockResolvedValue([]);
    api.create.mockReset();
    TestBed.configureTestingModule({
      providers: [provideRouter([]), { provide: ClipsApi, useValue: api }],
    });
  });

  async function render(src: SourceDetail, canWrite = true) {
    const f = TestBed.createComponent(ClipsSection);
    f.componentRef.setInput('source', src);
    f.componentRef.setInput('canWrite', canWrite);
    f.detectChanges();
    await vi.waitFor(() => expect(api.list).toHaveBeenCalled());
    await f.whenStable();
    f.detectChanges();
    return f;
  }
  const text = (f: { nativeElement: HTMLElement }) => f.nativeElement.textContent ?? '';

  it('lists clips with their range, shape, version and an honest render state', async () => {
    api.list.mockResolvedValue([
      clip(),
      clip({ id: 'c2', title: 'Bad one', best_render_state: 'fail' }),
      clip({ id: 'c3', title: 'New', best_render_state: null }),
    ]);
    const f = await render(source());
    const t = text(f);
    expect(t).toContain('Sleep beats diet');
    expect(t).toContain('1:05.00–1:35.00');
    expect(t).toContain('9:16');
    expect(t).toContain('version 3');
    expect(t).toContain('Rendered');
    expect(t).toContain('failed the quality checks');
    expect(t).toContain('Not rendered yet');
    expect(f.nativeElement.querySelector('a[href="/clips/c1"]')).not.toBeNull();
  });

  it('says there are no clips yet instead of staying blank', async () => {
    expect(text(await render(source()))).toContain('No clips yet.');
  });

  it('makes a clip from typed times and opens the editor', async () => {
    api.create.mockResolvedValue({ id: 'new-1' });
    const nav = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    const f = await render(source());
    f.componentInstance.start = '1:23.4';
    f.componentInstance.end = '2:00';
    await f.componentInstance.create();
    expect(api.create).toHaveBeenCalledWith('s1', 83_400, 120_000);
    expect(nav).toHaveBeenCalledWith(['/clips', 'new-1']);
  });

  it('refuses times that make no sense, with a reason, and creates nothing', async () => {
    const f = await render(source());
    for (const [a, b] of [
      ['abc', '0:30'],
      ['0:30', '0:10'],
    ]) {
      f.componentInstance.start = a;
      f.componentInstance.end = b;
      await f.componentInstance.create();
      f.detectChanges();
      expect(text(f)).toContain('Type the start and the end');
    }
    expect(api.create).not.toHaveBeenCalled();
  });

  it('gives viewers the list but no way to make clips, and nothing before the video is ready', async () => {
    api.list.mockResolvedValue([clip()]);
    const v = await render(source(), false);
    expect(v.nativeElement.querySelector('form')).toBeNull();
    expect(text(v)).toContain('Sleep beats diet');
    const notReady = await render(source({ status: 'processing' }));
    expect(notReady.nativeElement.querySelector('form')).toBeNull();
  });
});
