import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { MediaApi } from '../../core/media-api.service';
import { TranscriptApi } from '../../core/transcript-api.service';
import { SettingsPage } from './settings.page';

const status = (over = {}) => ({
  available: true,
  device: 'cpu',
  compute_type: 'int8',
  default_model: 'small',
  default_language: null,
  models: [
    { name: 'tiny', mb: 75, multilingual: true, note: 'Fastest', downloaded: true },
    { name: 'small.en', mb: 465, multilingual: false, note: 'English only.', downloaded: false },
  ],
  languages: ['en'],
  ...over,
});

describe('SettingsPage speech card', () => {
  const media = { features: vi.fn(), setIngest: vi.fn() };
  const speech = {
    asrStatus: vi.fn(),
    saveAsrSettings: vi.fn(),
    downloadModel: vi.fn(),
    modelProgress: vi.fn(),
  };

  beforeEach(() => {
    [...Object.values(media), ...Object.values(speech)].forEach((m) => m.mockReset());
    media.features.mockResolvedValue({
      ingest_enabled: false,
      ingest_available: false,
      ingest_daily_limit: 10,
      max_upload_bytes: 1,
      allowed_extensions: [],
    });
    speech.asrStatus.mockResolvedValue(status());
    TestBed.configureTestingModule({
      providers: [
        { provide: MediaApi, useValue: media },
        { provide: TranscriptApi, useValue: speech },
      ],
    });
  });

  async function render() {
    const f = TestBed.createComponent(SettingsPage);
    f.detectChanges();
    await vi.waitFor(() => expect(speech.asrStatus).toHaveBeenCalled());
    await f.whenStable();
    f.detectChanges();
    return f;
  }

  it('shows whether the service runs and which models are on this server', async () => {
    const f = await render();
    const t = f.nativeElement.textContent as string;
    expect(t).toContain('Transcription service:');
    expect(t).toContain('running');
    expect(t).toContain('Downloaded');
    expect(t).toContain('English only');
  });

  it('says how to start the service when it is down, and disables downloads', async () => {
    speech.asrStatus.mockResolvedValue(status({ available: false }));
    const f = await render();
    expect(f.nativeElement.textContent).toContain('docker compose up -d worker-asr');
    expect(
      (
        f.nativeElement.querySelector(
          'button[aria-label="Download model small.en"]',
        ) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });

  it('downloads a model, follows its progress, and shows a failure instead of pretending', async () => {
    speech.downloadModel.mockResolvedValue({});
    speech.modelProgress
      .mockResolvedValueOnce({ status: 'Downloading', percent: 40, done: false, error: null })
      .mockResolvedValueOnce({
        status: 'Failed',
        percent: 0,
        done: true,
        error: 'Could not download the speech model.',
      });
    const f = await render();
    await f.componentInstance.download('small.en');
    f.detectChanges();
    expect(speech.downloadModel).toHaveBeenCalledWith('small.en');
    expect(speech.modelProgress).toHaveBeenCalledTimes(2);
    expect(f.nativeElement.textContent).toContain('Could not download the speech model.');
  });

  it('saves the default model and language', async () => {
    speech.saveAsrSettings.mockResolvedValue({});
    const f = await render();
    f.componentInstance.speechModel = 'tiny';
    f.componentInstance.speechLang = 'en';
    await f.componentInstance.saveSpeech();
    expect(speech.saveAsrSettings).toHaveBeenCalledWith('tiny', 'en');
    expect(f.componentInstance.speechSaved()).toBe(true);
  });
});
