import { TestBed } from '@angular/core/testing';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AiApi, AiModel, RouteEntry } from '../../core/ai-api.service';
import { RoutingSection } from './routing.section';

const m = (id: string, name: string, local: boolean): AiModel => ({
  id,
  provider_id: 'p-' + id,
  provider_name: local ? 'Ollama' : 'Cloud',
  model_id: name,
  display_name: null,
  context_length: 8192,
  supports_json_schema: true,
  is_free: local,
  enabled: true,
  is_local: local,
  input_per_mtok: '0',
  output_per_mtok: '0',
  price_source: 'local',
});
const entry = (model: AiModel, usable = true, reason: string | null = null): RouteEntry => ({
  model_id: model.id,
  model_name: model.model_id,
  provider_id: model.provider_id,
  provider_name: model.provider_name,
  is_local: model.is_local,
  usable,
  unusable_reason: reason,
});
const empty = { highlight: [], metadata: [], translation: [], captions_cleanup: [] };

describe('RoutingSection', () => {
  const api = {
    models: vi.fn(),
    routes: vi.fn(),
    setMode: vi.fn(),
    saveRoutes: vi.fn(),
    testPrompt: vi.fn(),
  };
  const local = m('m1', 'qwen', true);
  const cloud = m('m2', 'big', false);

  beforeEach(() => {
    Object.values(api).forEach((x) => x.mockReset());
    api.models.mockResolvedValue([local, cloud]);
    api.routes.mockResolvedValue({ mode: 'LOCAL', routes: empty });
    TestBed.configureTestingModule({ providers: [{ provide: AiApi, useValue: api }] });
  });

  async function render(): Promise<HTMLElement> {
    const f = TestBed.createComponent(RoutingSection);
    f.detectChanges();
    for (let i = 0; i < 12; i++) {
      await new Promise((r) => setTimeout(r, 10)); // let the component's chain of async loads finish
      f.detectChanges();
    }
    return f.nativeElement as HTMLElement;
  }

  it('starts in Local mode and says what that means', async () => {
    const el = await render();
    expect(el.querySelector<HTMLInputElement>('input[type=radio][name=mode]:checked')?.value).toBe(
      'LOCAL',
    );
    expect(el.textContent).toContain('Nothing is sent to an online service');
  });

  it('warns when a saved choice cannot be used under the current mode', async () => {
    api.routes.mockResolvedValue({
      mode: 'LOCAL',
      routes: {
        ...empty,
        highlight: [entry(cloud, false, 'Local mode does not allow remote providers.')],
      },
    });
    const el = await render();
    expect(el.textContent).toContain(
      'big is saved but cannot be used now: Local mode does not allow remote providers.',
    );
  });

  it('saves only the filled slots, in order', async () => {
    api.saveRoutes.mockResolvedValue({ mode: 'LOCAL', routes: empty });
    const el = await render();
    const set = (id: string, value: string) => {
      const s = el.querySelector<HTMLSelectElement>('#' + id)!;
      s.value = value;
      s.dispatchEvent(new Event('change'));
    };
    set('highlight-0', 'm1');
    set('highlight-2', 'm2'); // slot 1 left empty on purpose
    el.querySelector<HTMLButtonElement>('button.btn-primary')!.click();
    await vi.waitFor(() => expect(api.saveRoutes).toHaveBeenCalled());
    expect(api.saveRoutes.mock.calls[0][0]).toMatchObject({
      highlight: ['m1', 'm2'],
      metadata: [],
    });
  });

  it('shows the real server error when the mode cannot be changed', async () => {
    api.setMode.mockRejectedValue(new Error('x'));
    const el = await render();
    el.querySelector<HTMLInputElement>('input[value=HYBRID]')!.dispatchEvent(new Event('change'));
    await vi.waitFor(() =>
      expect(el.querySelector('[role=alert]')?.textContent).toContain('Something went wrong'),
    );
  });

  it('reports an unknown cost honestly and lists skipped models after a test prompt', async () => {
    api.testPrompt.mockResolvedValue({
      text: 'pong',
      provider: 'Cloud',
      model: 'big',
      tokens_in: 10,
      tokens_out: 2,
      cost_est: null,
      latency_ms: 340,
      repairs: 0,
      failed_attempts: [
        {
          model: 'qwen',
          provider: 'Ollama',
          code: 'unreachable',
          message: 'Could not connect to the provider.',
        },
      ],
    });
    const el = await render();
    [...el.querySelectorAll<HTMLButtonElement>('button')]
      .find((b) => b.textContent?.includes('Run'))!
      .click();
    await vi.waitFor(() => expect(el.textContent).toContain('pong'));
    expect(el.textContent).toContain('cost unknown (no price for this model)');
    expect(el.textContent).not.toContain('$0');
    expect(el.textContent).toContain('Skipped Ollama · qwen: Could not connect to the provider.');
  });
});
