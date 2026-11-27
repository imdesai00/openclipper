import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AiApi, AiModel, Preset, Provider } from '../../core/ai-api.service';
import { ProvidersSection } from './providers.section';

const SECRET = 'sk-or-v1-THIS-MUST-NEVER-STAY-ON-THE-PAGE-1234';
const presets: Preset[] = [
  {
    kind: 'openrouter',
    name: 'OpenRouter',
    base_url: null,
    is_local: false,
    needs_key: true,
    note: 'One key, many models.',
  },
  {
    kind: 'ollama',
    name: 'Ollama (included)',
    base_url: 'http://ollama:11434',
    is_local: true,
    needs_key: false,
    note: 'Runs here.',
  },
];
const prov = (o: Partial<Provider>): Provider => ({
  id: 'p1',
  kind: 'openrouter',
  name: 'OpenRouter',
  base_url: null,
  enabled: true,
  is_local: false,
  rate_limit_rpm: null,
  timeout_s: 120,
  has_credential: true,
  credential_mask: 'sk-…1234',
  model_count: 1,
  last_test_at: null,
  last_test_ok: null,
  last_test_message: null,
  ...o,
});
const model = (o: Partial<AiModel>): AiModel => ({
  id: 'm1',
  provider_id: 'p1',
  provider_name: 'OpenRouter',
  model_id: 'a/b',
  display_name: null,
  context_length: 131072,
  supports_json_schema: false,
  is_free: false,
  enabled: true,
  is_local: false,
  input_per_mtok: '3',
  output_per_mtok: '15',
  price_source: 'provider',
  ...o,
});

describe('ProvidersSection', () => {
  const api = {
    presets: vi.fn(),
    providers: vi.fn(),
    models: vi.fn(),
    createProvider: vi.fn(),
    test: vi.fn(),
    discover: vi.fn(),
    patchProvider: vi.fn(),
    setKey: vi.fn(),
    clearKey: vi.fn(),
    deleteProvider: vi.fn(),
    setModelEnabled: vi.fn(),
    pull: vi.fn(),
    setPrice: vi.fn(),
    clearPrice: vi.fn(),
  };

  beforeEach(() => {
    Object.values(api).forEach((m) => m.mockReset());
    api.presets.mockResolvedValue(presets);
    api.providers.mockResolvedValue([]);
    api.models.mockResolvedValue([]);
    TestBed.configureTestingModule({ providers: [{ provide: AiApi, useValue: api }] });
  });

  async function render(): Promise<HTMLElement> {
    const f = TestBed.createComponent(ProvidersSection);
    f.detectChanges();
    for (let i = 0; i < 12; i++) {
      await new Promise((r) => setTimeout(r, 10)); // let the component's chain of async loads finish
      f.detectChanges();
    }
    return f.nativeElement as HTMLElement;
  }

  it('explains the empty state and offers Ollama first-class', async () => {
    const el = await render();
    expect(el.textContent).toContain('No providers yet');
    expect(el.textContent).toContain('without any account');
  });

  it('shows only the mask of a saved key, never the key', async () => {
    api.providers.mockResolvedValue([prov({})]);
    api.models.mockResolvedValue([model({})]);
    const el = await render();
    expect(el.textContent).toContain('sk-…1234');
    expect(el.innerHTML).not.toContain('THIS-MUST-NEVER');
  });

  it('clears the typed key from the form as soon as the provider is created', async () => {
    api.createProvider.mockResolvedValue(prov({}));
    api.providers.mockResolvedValue([prov({})]);
    const el = await render();
    const key = el.querySelector<HTMLInputElement>('#pkey')!;
    expect(key.type).toBe('password');
    expect(key.getAttribute('autocomplete')).toBe('new-password');
    key.value = SECRET;
    key.dispatchEvent(new Event('input'));
    el.querySelector('form')!.dispatchEvent(new Event('submit'));
    await vi.waitFor(() => expect(api.createProvider).toHaveBeenCalled());
    expect(api.createProvider.mock.calls[0][0]).toMatchObject({
      kind: 'openrouter',
      api_key: SECRET,
    });
    await vi.waitFor(() => expect(el.querySelector<HTMLInputElement>('#pkey')!.value).toBe(''));
    expect(el.innerHTML).not.toContain(SECRET);
  });

  it('labels remote free models with the rate-limit warning, and local models as free without it', async () => {
    api.providers.mockResolvedValue([prov({})]);
    api.models.mockResolvedValue([
      model({
        id: 'm1',
        model_id: 'x/free:free',
        is_free: true,
        price_source: 'free',
        input_per_mtok: '0',
        output_per_mtok: '0',
      }),
      model({ id: 'm2', model_id: 'qwen', is_free: true, is_local: true, price_source: 'local' }),
      model({
        id: 'm3',
        model_id: 'mystery',
        price_source: 'unknown',
        input_per_mtok: null,
        output_per_mtok: null,
      }),
    ]);
    const rows = Array.from((await render()).querySelectorAll('tbody tr'));
    expect(rows[0].textContent).toContain('rate-limited, availability not guaranteed');
    expect(rows[1].textContent).toContain('Free (runs here)');
    expect(rows[1].textContent).not.toContain('availability not guaranteed');
    expect(rows[2].textContent).toContain('costs will not be estimated');
  });

  it('shows Ollama model download only for Ollama providers', async () => {
    api.providers.mockResolvedValue([
      prov({}),
      prov({
        id: 'p2',
        kind: 'ollama',
        name: 'Ollama',
        is_local: true,
        has_credential: false,
        credential_mask: null,
      }),
    ]);
    const el = await render();
    expect(el.querySelectorAll('input[placeholder^="e.g. qwen"]')).toHaveLength(1);
  });

  it('reports a failed connection test in words', async () => {
    api.providers.mockResolvedValue([
      prov({
        last_test_at: '2026-01-01',
        last_test_ok: false,
        last_test_message: 'The provider rejected the credentials. Check the API key.',
      }),
    ]);
    const el = await render();
    expect(el.textContent).toContain('Test failed: The provider rejected the credentials');
  });
});
