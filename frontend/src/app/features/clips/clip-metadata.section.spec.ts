import { HttpErrorResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ClipsApi } from '../../core/clips-api.service';
import { ClipMetadata, Platform, PlatformMetadata } from '../../core/models';
import { ClipMetadataSection } from './clip-metadata.section';

const plat = (p: Platform, over: Partial<PlatformMetadata> = {}): PlatformMetadata => ({
  platform: p,
  title: '',
  hook: '',
  description: '',
  short_description: '',
  hashtags: [],
  tags: [],
  thumbnail_text: '',
  category: '',
  audience: '',
  edited: false,
  generated_by: null,
  last_error: null,
  errors: [],
  warnings: [],
  ...over,
});

const data = (
  over: Partial<Record<Platform, Partial<PlatformMetadata>>> = {},
  job = null,
): ClipMetadata => ({
  platforms: {
    generic: plat('generic', over.generic),
    youtube: plat('youtube', over.youtube),
    instagram: plat('instagram', over.instagram),
  },
  limits: {
    generic: { title: 150, description: 2000, short_description: 300, thumbnail_text: 40 },
    youtube: {
      title: 100,
      description: 5000,
      short_description: 300,
      thumbnail_text: 40,
      tags_total: 500,
    },
    instagram: { title: 150, description: 2200, short_description: 300, thumbnail_text: 40 },
  },
  soft_hashtag_limits: { generic: 30, youtube: 15, instagram: 30 },
  job,
});

describe('ClipMetadataSection', () => {
  const api = { metadata: vi.fn(), saveMetadata: vi.fn(), generateMetadata: vi.fn() };

  beforeEach(() => {
    Object.values(api).forEach((m) => m.mockReset());
    api.metadata.mockResolvedValue(data());
    TestBed.configureTestingModule({ providers: [{ provide: ClipsApi, useValue: api }] });
  });

  async function render(canWrite = true) {
    const f = TestBed.createComponent(ClipMetadataSection);
    f.componentRef.setInput('clipId', 'c1');
    f.componentRef.setInput('canWrite', canWrite);
    f.detectChanges();
    await vi.waitFor(() => expect(api.metadata).toHaveBeenCalled());
    await f.whenStable();
    f.detectChanges();
    return f;
  }
  const text = (f: { nativeElement: HTMLElement }) => f.nativeElement.textContent ?? '';
  const btn = (f: { nativeElement: HTMLElement }, label: string) =>
    Array.from(f.nativeElement.querySelectorAll('button')).find((b) =>
      (b.textContent ?? '').trim().startsWith(label),
    ) as HTMLButtonElement | undefined;

  it('shows each platform separately, with the generated-by note and an edited badge', async () => {
    api.metadata.mockResolvedValue(
      data({ youtube: { title: 'Why sleep', generated_by: 'Ollama / qwen', edited: true } }),
    );
    const f = await render();
    btn(f, 'YouTube')!.click();
    // ngModel writes the value into the input a moment after the view updates
    await vi.waitFor(() => {
      f.detectChanges();
      const input = f.nativeElement.querySelector('#meta-title') as HTMLInputElement;
      expect(input.value).toBe('Why sleep');
    });
    expect(text(f)).toContain('Written by Ollama / qwen');
    expect(text(f)).toContain('edited by you');
    expect(btn(f, 'YouTube')!.getAttribute('aria-selected')).toBe('true');
  });

  it('counts characters against the platform limit and blocks saving when over it', async () => {
    const f = await render();
    btn(f, 'YouTube')!.click();
    f.componentInstance.setField('title', 'x'.repeat(101));
    f.detectChanges();
    expect(text(f)).toContain('(101 / 100)');
    expect(text(f)).toContain('Over a platform limit');
    expect(btn(f, 'Save YouTube text')!.disabled).toBe(true);
    f.componentInstance.setField('title', 'x'.repeat(100));
    f.detectChanges();
    expect(btn(f, 'Save YouTube text')!.disabled).toBe(false);
  });

  it('counts an Instagram caption together with its hashtags', async () => {
    const f = await render();
    btn(f, 'Instagram')!.click();
    f.componentInstance.setField('description', 'a'.repeat(10));
    f.componentInstance.setField('hashtags', '#one #two');
    expect(f.componentInstance.count('description')).toBe(10 + 1 + '#one #two'.length);
    btn(f, 'YouTube')!.click();
    f.componentInstance.setField('description', 'a'.repeat(10));
    f.componentInstance.setField('hashtags', '#one #two');
    expect(f.componentInstance.count('description')).toBe(10); // YouTube counts them separately
  });

  it('saves with hashtags and tags split into lists', async () => {
    api.saveMetadata.mockResolvedValue(plat('generic'));
    const f = await render();
    f.componentInstance.setField('title', 'Mine');
    f.componentInstance.setField('hashtags', '#a, b  #c');
    f.componentInstance.setField('tags', 'deep sleep, health');
    await f.componentInstance.save();
    expect(api.saveMetadata).toHaveBeenCalledWith(
      'c1',
      'generic',
      expect.objectContaining({
        title: 'Mine',
        hashtags: ['#a', 'b', '#c'],
        tags: ['deep sleep', 'health'],
      }),
    );
    f.detectChanges();
    expect(text(f)).toContain('Saved.');
  });

  it('asks before replacing text a person edited, and only then overwrites', async () => {
    api.generateMetadata
      .mockRejectedValueOnce(
        new HttpErrorResponse({
          status: 409,
          error: { code: 'edited_metadata', message: 'x', details: ['youtube'] },
        }),
      )
      .mockResolvedValueOnce({});
    const f = await render();
    await f.componentInstance.generate(['youtube']);
    f.detectChanges();
    expect(text(f)).toContain('Writing it again replaces your edits');
    expect(api.generateMetadata).toHaveBeenLastCalledWith('c1', ['youtube'], false);
    await f.componentInstance.confirmOverwrite();
    expect(api.generateMetadata).toHaveBeenLastCalledWith('c1', ['youtube'], true);
  });

  it('keeps the person’s text when they decline to replace it', async () => {
    api.generateMetadata.mockRejectedValue(
      new HttpErrorResponse({
        status: 409,
        error: { code: 'edited_metadata', message: 'x', details: ['generic'] },
      }),
    );
    const f = await render();
    await f.componentInstance.generate(['generic']);
    f.detectChanges();
    btn(f, 'Keep my text')!.click();
    f.detectChanges();
    expect(text(f)).not.toContain('Writing it again replaces');
    expect(api.generateMetadata).toHaveBeenCalledTimes(1);
  });

  it('shows why a platform failed, a failed job, and live progress', async () => {
    api.metadata.mockResolvedValue(data({ youtube: { last_error: 'No model could answer.' } }));
    const f = await render();
    btn(f, 'YouTube')!.click();
    f.detectChanges();
    expect(f.nativeElement.querySelector('[role=alert]')?.textContent).toContain(
      'No model could answer.',
    );

    api.metadata.mockResolvedValue(
      data({}, { status: 'failed', error_message: 'Nothing could be written.' } as never),
    );
    const failed = await render();
    expect(text(failed)).toContain('Writing failed: Nothing could be written.');

    api.metadata.mockResolvedValue(data({}, { status: 'running' } as never));
    const running = await render();
    expect(running.nativeElement.querySelector('[role=status]')?.textContent).toContain('Writing');
    expect(btn(running, 'Write all three')!.disabled).toBe(true); // no second job while one runs
  });

  it('shows validation messages from the server and lets viewers read but not change', async () => {
    api.metadata.mockResolvedValue(
      data({
        generic: {
          errors: ['Title is 200 characters; generic allows 150.'],
          warnings: ['More than 30 hashtags'],
        },
      }),
    );
    const f = await render();
    expect(text(f)).toContain('Title is 200 characters; generic allows 150.');
    expect(text(f)).toContain('More than 30 hashtags');
    const v = await render(false);
    expect(btn(v, 'Save')).toBeUndefined();
    expect(btn(v, 'Write')).toBeUndefined();
    expect((v.nativeElement.querySelector('#meta-title') as HTMLInputElement).disabled).toBe(true);
  });
});
