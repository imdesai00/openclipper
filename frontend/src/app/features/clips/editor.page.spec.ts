import { HttpErrorResponse } from '@angular/common/http';
import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthService } from '../../core/auth.service';
import { ClipsApi } from '../../core/clips-api.service';
import { MediaApi } from '../../core/media-api.service';
import { Clip, ClipRender, Project, SourceDetail } from '../../core/models';
import { ClipEditorPage } from './editor.page';

const render = (over: Partial<ClipRender> = {}): ClipRender => ({
  id: 'r1',
  kind: 'final',
  version: 1,
  state: 'done',
  error: null,
  created_at: '',
  width: 1080,
  height: 1920,
  duration_ms: 6000,
  size_bytes: 2_500_000,
  loudness_lufs: -14.1,
  qc_status: 'pass',
  qc_report: {
    status: 'pass',
    checks: [{ check: 'duration', severity: 'pass', message: '6.00 s, asked for 6.00 s.' }],
  },
  play_url: '/api/v1/media/x?t=y',
  download_url: '/api/v1/media/x?t=y&dl=1&name=clip.mp4',
  thumbnail_url: null,
  job: null,
  ...over,
});

const clip = (over: Partial<Clip> = {}): Clip => ({
  id: 'c1',
  project_id: 'p1',
  source_id: 's1',
  candidate_id: null,
  title: 'Sleep beats diet',
  current_version: 1,
  version: {
    id: 'v1',
    version: 1,
    start_ms: 2000,
    end_ms: 8000,
    aspect: '9:16',
    crop: { mode: 'fill', zoom: 1, keyframes: [] },
    background: { type: 'blur', color: '#000000' },
    audio: { gain_db: 0, mute: false },
    caption_template_id: 't1',
    caption_overrides: {},
    transcript_id: 'tr1',
    created_at: '',
  },
  style: {
    font_family: 'Liberation Sans',
    animation: 'highlight',
    font_size_pct: 4.6,
    position: 'bottom',
    color: '#FFFFFF',
    highlight_color: '#FFD400',
    uppercase: false,
  },
  template_name: 'Bold highlight',
  renders: [],
  created_at: '',
  ...over,
});

const source = (): SourceDetail =>
  ({
    id: 's1',
    project_id: 'p1',
    title: 'Episode 12',
    status: 'ready',
    has_video: true,
    width: 1920,
    height: 1080,
    duration_s: 60,
    proxy_url: null,
    waveform_url: null,
    scenes_url: null,
  }) as SourceDetail;

describe('ClipEditorPage', () => {
  const api = {
    get: vi.fn(),
    words: vi.fn(),
    templates: vi.fn(),
    previewStyle: vi.fn(),
    saveVersion: vi.fn(),
    render: vi.fn(),
    remove: vi.fn(),
    captionsFile: vi.fn(),
  };
  const media = { getSource: vi.fn(), getProject: vi.fn() };
  const role = signal<'editor' | 'viewer'>('editor');

  beforeEach(() => {
    Object.values(api).forEach((m) => m.mockReset());
    Object.values(media).forEach((m) => m.mockReset());
    role.set('editor');
    api.get.mockResolvedValue(clip());
    api.words.mockResolvedValue([
      { text: 'Hello', start_ms: 1500, end_ms: 1900, inside: false },
      { text: 'there', start_ms: 2100, end_ms: 2500, inside: true },
    ]);
    api.templates.mockResolvedValue([
      { id: 't1', name: 'Bold highlight', is_builtin: true, style: {} },
    ]);
    api.previewStyle.mockResolvedValue({
      style: clip().style,
      css: {
        container: {},
        text: {},
        active_color: '#FFD400',
        keyword_color: '#F00',
        animation: 'highlight',
        keywords: [],
        font_px: 88,
      },
      cues: [],
      output_width: 1080,
      output_height: 1920,
      fonts: ['Liberation Sans', 'DejaVu Sans'],
    });
    media.getSource.mockResolvedValue(source());
    media.getProject.mockResolvedValue({ id: 'p1', name: 'Show', my_role: 'owner' } as Project);
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        { provide: ClipsApi, useValue: api },
        { provide: MediaApi, useValue: media },
        { provide: AuthService, useValue: { user: () => ({ role: role() }) } },
      ],
    });
  });

  async function open() {
    const f = TestBed.createComponent(ClipEditorPage);
    f.componentRef.setInput('id', 'c1');
    f.detectChanges();
    await vi.waitFor(() => expect(media.getProject).toHaveBeenCalled());
    await f.whenStable();
    f.detectChanges();
    return f;
  }
  const text = (f: { nativeElement: HTMLElement }) => f.nativeElement.textContent ?? '';
  const btn = (f: { nativeElement: HTMLElement }, label: string) =>
    Array.from(f.nativeElement.querySelectorAll('button')).find((b) =>
      (b.textContent ?? '').trim().startsWith(label),
    ) as HTMLButtonElement | undefined;

  it('opens a clip with its name, version, range and no unsaved-changes badge', async () => {
    const f = await open();
    expect((f.nativeElement.querySelector('#clip-title') as HTMLInputElement).value).toBe(
      'Sleep beats diet',
    );
    expect(text(f)).toContain('version 1');
    expect(text(f)).not.toContain('unsaved changes');
    expect((f.nativeElement.querySelector('#t-start') as HTMLInputElement).value).toBe('0:02.00');
    expect((f.nativeElement.querySelector('#t-end') as HTMLInputElement).value).toBe('0:08.00');
    expect(text(f)).toContain('Episode 12');
  });

  it('notices unsaved changes, saves them as the next version, and says so', async () => {
    const f = await open();
    f.componentInstance.patch({ aspect: '1:1' });
    f.detectChanges();
    expect(text(f)).toContain('unsaved changes');
    api.saveVersion.mockResolvedValue(
      clip({ current_version: 2, version: { ...clip().version, version: 2, aspect: '1:1' } }),
    );
    await f.componentInstance.save();
    f.detectChanges();
    expect(api.saveVersion).toHaveBeenCalledWith(
      'c1',
      expect.objectContaining({
        base_version: 1,
        aspect: '1:1',
        start_ms: 2000,
        end_ms: 8000,
        title: 'Sleep beats diet',
      }),
    );
    expect(text(f)).toContain('version 2');
    expect(text(f)).not.toContain('unsaved changes');
  });

  it('refuses to overwrite when someone else saved first, and can show the latest', async () => {
    const f = await open();
    f.componentInstance.patch({ aspect: '16:9' });
    api.saveVersion.mockRejectedValue(
      new HttpErrorResponse({
        status: 409,
        error: {
          code: 'version_conflict',
          message: 'This clip was changed since you opened it. Reload and try again.',
        },
      }),
    );
    expect(await f.componentInstance.save()).toBe(false);
    f.detectChanges();
    expect(f.nativeElement.querySelector('[role=alert]')?.textContent).toContain(
      'changed since you opened it',
    );
    api.get.mockResolvedValue(clip({ current_version: 5 }));
    btn(f, 'Show the latest')!.click();
    await vi.waitFor(() => expect(f.componentInstance.clip()?.current_version).toBe(5));
    f.detectChanges();
    expect(text(f)).not.toContain('unsaved changes'); // the draft was replaced by the saved clip
  });

  it('keeps the range at least a second long, inside the video, and on a frame', async () => {
    const f = await open();
    const c = f.componentInstance;
    c.setStart(7900);
    expect(c.draft().start_ms).toBe(7000);
    c.setEnd(2100);
    expect(c.draft().end_ms).toBe(8000); // refused: the end cannot come within a second of the start
    c.setEnd(999_999);
    expect(c.draft().end_ms).toBe(60_000);
    c.setStart(-500);
    expect(c.draft().start_ms).toBe(0);
    c.setStart(1013);
    expect(c.draft().start_ms).toBe(1000);
  });

  it('turns typed times into the range, and rejects nonsense without changing it', async () => {
    const f = await open();
    const input = f.nativeElement.querySelector('#t-start') as HTMLInputElement;
    input.value = '0:03.5';
    f.componentInstance.typeTime({ target: input } as unknown as Event, 'start');
    expect(f.componentInstance.draft().start_ms).toBe(3520); // 3.5 s snapped to the nearest 40 ms frame
    input.value = 'soon';
    f.componentInstance.typeTime({ target: input } as unknown as Event, 'start');
    f.detectChanges();
    expect(f.componentInstance.draft().start_ms).toBe(3520);
    expect(input.value).toBe('0:03.52');
    expect(text(f)).toContain('Type a time like');
  });

  it('shows the words, lets a click pick one, and sets the start from it', async () => {
    const f = await open();
    const words = Array.from(
      (f.nativeElement as HTMLElement).querySelectorAll('[data-testid=words] button'),
    ) as HTMLButtonElement[];
    expect(words.map((w) => w.textContent?.trim())).toEqual(['Hello', 'there']);
    expect(words[0].classList.contains('opacity-50')).toBe(true); // outside the clip: dimmed
    words[1].click();
    f.detectChanges();
    btn(f, 'Start the clip at this word')!.click();
    expect(f.componentInstance.draft().start_ms).toBe(2120);
  });

  it('adds and removes frame positions, and the crop window follows them', async () => {
    const f = await open();
    const c = f.componentInstance;
    c.setCrop({
      keyframes: [
        { t_ms: 0, cx: 0.2, cy: 0.5 },
        { t_ms: 4000, cx: 0.8, cy: 0.5 },
      ],
    });
    f.detectChanges();
    c.timeMs.set(4000); // two seconds into a clip that starts at 2 s: halfway between the two positions
    const mid = c.win()!;
    expect(Math.abs(mid.x - (0.5 * 1920 - mid.w / 2))).toBeLessThanOrEqual(2);
    c.removeKeyframe({ t_ms: 4000, cx: 0.8, cy: 0.5 });
    expect(c.draft().crop.keyframes).toHaveLength(1);
    expect(c.dirty()).toBe(true);
  });

  it('saves unsaved changes first, then renders, and shows the result with its quality report', async () => {
    const calls: string[] = [];
    api.saveVersion.mockImplementation(async () => {
      calls.push('save');
      return clip({ current_version: 2 });
    });
    api.render.mockImplementation(async () => {
      calls.push('render');
      return { reused: false, render: render() };
    });
    api.get.mockResolvedValue(clip({ current_version: 2, renders: [render({ version: 2 })] }));
    const f = await open();
    f.componentInstance.patch({ audio: { gain_db: 3, mute: false } });
    await f.componentInstance.renderNow('final');
    f.detectChanges();
    expect(calls).toEqual(['save', 'render']); // never renders something other than what was saved
    expect(api.render).toHaveBeenCalledWith('c1', 'final', 2);
    const t = text(f);
    expect(t).toContain('Final · version 2');
    expect(t).toContain('1080×1920');
    expect(t).toContain('-14.1 LUFS');
    expect(t).toContain('Quality checks: all passed');
    expect(f.nativeElement.querySelector('a[href*="dl=1"]')).not.toBeNull();
  });

  it('puts a failed quality check and a failed render in front of the person, in words', async () => {
    api.get.mockResolvedValue(
      clip({
        renders: [
          render({
            qc_status: 'fail',
            qc_report: {
              status: 'fail',
              checks: [
                { check: 'black_frames', severity: 'fail', message: '100% of the clip is black.' },
              ],
            },
          }),
          render({
            id: 'r2',
            state: 'failed',
            error: 'Processing took too long and was stopped',
            play_url: null,
            download_url: null,
            qc_report: null,
            qc_status: null,
          }),
        ],
      }),
    );
    const f = await open();
    f.componentInstance.tab.set('renders');
    f.detectChanges();
    const t = text(f);
    expect(t).toContain('Quality checks: failed');
    expect(t).toContain('Problem: 100% of the clip is black.');
    expect(f.nativeElement.querySelector('[role=alert]')?.textContent).toContain('took too long');
  });

  it('draws the caption preview from the server using the unsaved caption settings', async () => {
    const f = await open();
    f.componentInstance.setOverride('color', '#FF0000');
    await vi.waitFor(() =>
      expect(api.previewStyle).toHaveBeenLastCalledWith(
        'c1',
        expect.objectContaining({
          width: 1080,
          height: 1920,
          caption_overrides: { color: '#FF0000' },
        }),
      ),
    );
  });

  it('responds to the keyboard, but never while typing in a field', async () => {
    const f = await open();
    const c = f.componentInstance;
    c.timeMs.set(5000);
    c.onKey(new KeyboardEvent('keydown', { key: 'o' }));
    expect(c.draft().end_ms).toBe(5000);
    c.onKey(new KeyboardEvent('keydown', { key: 'i' }));
    expect(c.draft().start_ms).toBe(4000); // clamped: a clip is at least a second long
    const input = document.createElement('input');
    const typing = new KeyboardEvent('keydown', { key: 'o' });
    Object.defineProperty(typing, 'target', { value: input });
    c.timeMs.set(6500);
    c.onKey(typing);
    expect(c.draft().end_ms).toBe(5000); // the letter went into the field, not the editor
  });

  it('gives viewers the clip and its renders, but nothing that changes it', async () => {
    role.set('viewer');
    api.get.mockResolvedValue(clip({ renders: [render()] }));
    const f = await open();
    expect(btn(f, 'Save as version')).toBeUndefined();
    expect(btn(f, 'Delete this clip')).toBeUndefined();
    expect((f.nativeElement.querySelector('#clip-title') as HTMLInputElement).disabled).toBe(true);
    expect((f.nativeElement.querySelector('#aspect') as HTMLSelectElement).disabled).toBe(true);
    f.componentInstance.tab.set('renders');
    f.detectChanges();
    expect(btn(f, 'Render the final clip')).toBeUndefined();
    expect(text(f)).toContain('Final · version 1');
  });

  it('deletes only after the confirmation dialog, then goes back to the video', async () => {
    api.remove.mockResolvedValue(undefined);
    const nav = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    const f = await open();
    vi.spyOn(f.componentInstance.delDlg(), 'close').mockImplementation(() => undefined); // jsdom has no <dialog>.close()
    await f.componentInstance.remove();
    expect(api.remove).toHaveBeenCalledWith('c1');
    expect(nav).toHaveBeenCalledWith(['/sources', 's1']);
  });

  it('shows a load failure instead of an empty editor', async () => {
    api.get.mockRejectedValue(
      new HttpErrorResponse({
        status: 404,
        error: { code: 'not_found', message: 'Clip not found' },
      }),
    );
    const f = TestBed.createComponent(ClipEditorPage);
    f.componentRef.setInput('id', 'nope');
    f.detectChanges();
    await vi.waitFor(() => {
      f.detectChanges();
      expect(text(f)).toContain('Clip not found');
    });
  });
});
