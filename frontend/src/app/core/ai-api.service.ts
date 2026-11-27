import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { AuthService } from './auth.service';

const API = '/api/v1';

export type ProviderKind =
  'ollama' | 'openai' | 'openrouter' | 'groq' | 'anthropic' | 'gemini' | 'openai_compat';
export type Task = 'highlight' | 'metadata' | 'translation' | 'captions_cleanup';
export type Mode = 'LOCAL' | 'REMOTE' | 'HYBRID';
export const TASKS: { id: Task; label: string; help: string }[] = [
  {
    id: 'highlight',
    label: 'Find highlights',
    help: 'Reads transcripts to pick the best moments.',
  },
  {
    id: 'metadata',
    label: 'Titles and descriptions',
    help: 'Writes titles, descriptions and hashtags.',
  },
  { id: 'translation', label: 'Translate captions', help: 'Machine translation of captions.' },
  {
    id: 'captions_cleanup',
    label: 'Clean up captions',
    help: 'Fixes punctuation and obvious mistakes.',
  },
];

export interface Provider {
  id: string;
  kind: ProviderKind;
  name: string;
  base_url: string | null;
  enabled: boolean;
  is_local: boolean;
  rate_limit_rpm: number | null;
  timeout_s: number;
  has_credential: boolean;
  credential_mask: string | null;
  model_count: number;
  last_test_at: string | null;
  last_test_ok: boolean | null;
  last_test_message: string | null;
}

export interface Preset {
  kind: ProviderKind;
  name: string;
  base_url: string | null;
  is_local: boolean;
  needs_key: boolean;
  note: string;
}

export interface AiModel {
  id: string;
  provider_id: string;
  provider_name: string;
  model_id: string;
  display_name: string | null;
  context_length: number | null;
  supports_json_schema: boolean;
  is_free: boolean;
  enabled: boolean;
  is_local: boolean;
  input_per_mtok: string | null;
  output_per_mtok: string | null;
  price_source: 'local' | 'user' | 'provider' | 'free' | 'unknown';
}

export interface RouteEntry {
  model_id: string;
  model_name: string;
  provider_id: string;
  provider_name: string;
  is_local: boolean;
  usable: boolean;
  unusable_reason: string | null;
}

export interface PromptResult {
  text: string;
  provider: string;
  model: string;
  tokens_in: number;
  tokens_out: number;
  cost_est: string | null;
  latency_ms: number;
  repairs: number;
  failed_attempts: { model: string; provider: string; code: string; message: string }[];
}

export interface UsageRow {
  id: string;
  created_at: string;
  provider_name: string;
  model_name: string;
  task: string;
  tokens_in: number;
  tokens_out: number;
  cost_est: string | null;
  latency_ms: number;
  status: 'ok' | 'error';
  error_code: string | null;
  error_message: string | null;
  repairs: number;
  attempt: number;
}

export interface UsageReport {
  days: number;
  calls: number;
  errors: number;
  tokens_in: number;
  tokens_out: number;
  cost_est: string;
  calls_with_unknown_cost: number;
  by_model: {
    provider: string;
    model: string;
    calls: number;
    errors: number;
    tokens_in: number;
    tokens_out: number;
    cost_est: string;
    calls_with_unknown_cost: number;
  }[];
  recent: UsageRow[];
}

export interface NewProvider {
  kind: ProviderKind;
  name: string;
  base_url?: string | null;
  is_local?: boolean | null;
  rate_limit_rpm?: number | null;
  timeout_s?: number;
  api_key?: string | null;
}

@Injectable({ providedIn: 'root' })
export class AiApi {
  private readonly http = inject(HttpClient);
  private readonly auth = inject(AuthService);

  providers() {
    return firstValueFrom(this.http.get<Provider[]>(`${API}/providers`));
  }
  presets() {
    return firstValueFrom(this.http.get<Preset[]>(`${API}/providers/presets`));
  }
  createProvider(body: NewProvider) {
    return firstValueFrom(this.http.post<Provider>(`${API}/providers`, body));
  }
  patchProvider(id: string, body: Partial<Pick<Provider, 'enabled' | 'name' | 'base_url'>>) {
    return firstValueFrom(this.http.patch<Provider>(`${API}/providers/${id}`, body));
  }
  deleteProvider(id: string) {
    return firstValueFrom(this.http.delete<void>(`${API}/providers/${id}`));
  }
  setKey(id: string, api_key: string) {
    return firstValueFrom(
      this.http.put<Provider>(`${API}/providers/${id}/credential`, { api_key }),
    );
  }
  clearKey(id: string) {
    return firstValueFrom(this.http.delete<Provider>(`${API}/providers/${id}/credential`));
  }
  test(id: string) {
    return firstValueFrom(
      this.http.post<{ ok: boolean; message: string; latency_ms: number }>(
        `${API}/providers/${id}/test`,
        null,
      ),
    );
  }
  discover(id: string) {
    return firstValueFrom(
      this.http.post<{ added: number; updated: number }>(`${API}/providers/${id}/discover`, null),
    );
  }
  models(providerId?: string) {
    const params: Record<string, string> = providerId ? { provider_id: providerId } : {};
    return firstValueFrom(this.http.get<AiModel[]>(`${API}/models`, { params }));
  }
  setModelEnabled(id: string, enabled: boolean) {
    return firstValueFrom(this.http.patch<AiModel>(`${API}/models/${id}`, { enabled }));
  }
  setPrice(id: string, input_per_mtok: number, output_per_mtok: number) {
    return firstValueFrom(
      this.http.put<AiModel>(`${API}/models/${id}/price`, { input_per_mtok, output_per_mtok }),
    );
  }
  clearPrice(id: string) {
    return firstValueFrom(this.http.delete<AiModel>(`${API}/models/${id}/price`));
  }
  mode() {
    return firstValueFrom(this.http.get<{ mode: Mode }>(`${API}/ai/mode`));
  }
  setMode(mode: Mode) {
    return firstValueFrom(this.http.put<{ mode: Mode }>(`${API}/ai/mode`, { mode }));
  }
  routes() {
    return firstValueFrom(
      this.http.get<{ mode: Mode; routes: Record<Task, RouteEntry[]> }>(`${API}/ai/routes`),
    );
  }
  saveRoutes(routes: Record<string, string[]>) {
    return firstValueFrom(
      this.http.put<{ mode: Mode; routes: Record<Task, RouteEntry[]> }>(`${API}/ai/routes`, {
        routes,
      }),
    );
  }
  testPrompt(body: { prompt: string; task: Task; model_id?: string | null; json_mode?: boolean }) {
    return firstValueFrom(this.http.post<PromptResult>(`${API}/ai/test-prompt`, body));
  }
  usage(days: number) {
    return firstValueFrom(this.http.get<UsageReport>(`${API}/usage`, { params: { days } }));
  }

  /** Download an Ollama model; `onEvent` receives each progress line. Plain fetch because HttpClient cannot stream. */
  async pull(
    providerId: string,
    model: string,
    onEvent: (e: { status?: string; total?: number; completed?: number; error?: string }) => void,
  ): Promise<void> {
    if (this.auth.expiresInMs() < 60_000) await this.auth.refresh();
    const resp = await fetch(`${API}/providers/${providerId}/pull`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${this.auth.accessToken()}`,
      },
      body: JSON.stringify({ model }),
    });
    if (!resp.ok || !resp.body) {
      const j = await resp.json().catch(() => ({ message: 'Could not start the download.' }));
      throw new Error(j.message ?? 'Could not start the download.');
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let nl: number;
      while ((nl = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (line) onEvent(JSON.parse(line));
      }
    }
  }
}
