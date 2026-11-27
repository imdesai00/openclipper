import { Component, OnInit, computed, inject, output, signal, viewChild } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';

import { AiApi, AiModel, NewProvider, Preset, Provider } from '../../core/ai-api.service';
import { errorMessage } from '../../core/api';
import { ConfirmDialogComponent } from '../../shared/confirm-dialog.component';

interface Pull {
  providerId: string;
  model: string;
  status: string;
  percent: number | null;
  error: string;
  done: boolean;
}

@Component({
  selector: 'app-providers-section',
  imports: [ReactiveFormsModule, ConfirmDialogComponent],
  template: `
    <section class="card" aria-labelledby="add-h">
      <h2 id="add-h" class="text-base font-semibold">Add a provider</h2>
      <p class="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
        Start with Ollama to run everything on this machine for free. Add a hosted service only if
        you want to; keys are encrypted and never shown again.
      </p>
      <form [formGroup]="form" (ngSubmit)="add()" class="mt-4 grid gap-3 sm:grid-cols-2" novalidate>
        <div class="sm:col-span-2">
          <label class="label" for="preset">Type</label>
          <select id="preset" class="input" (change)="pickPreset($any($event.target).value)">
            @for (p of presets(); track p.kind + p.name) {
              <option [value]="$index">{{ p.name }}</option>
            }
          </select>
          @if (preset(); as pr) {
            <p class="mt-1 text-sm text-zinc-600 dark:text-zinc-400">{{ pr.note }}</p>
          }
        </div>
        <div>
          <label class="label" for="pname">Name</label>
          <input id="pname" class="input" formControlName="name" autocomplete="off" />
        </div>
        @if (showUrl()) {
          <div>
            <label class="label" for="purl">Address</label>
            <input
              id="purl"
              class="input"
              formControlName="base_url"
              autocomplete="off"
              placeholder="http://…"
            />
          </div>
        }
        @if (preset()?.needs_key || preset()?.kind === 'openai_compat') {
          <div class="sm:col-span-2">
            <label class="label" for="pkey"
              >API key{{ preset()?.needs_key ? '' : ' (optional)' }}</label
            >
            <input
              id="pkey"
              class="input"
              type="password"
              formControlName="api_key"
              autocomplete="new-password"
              spellcheck="false"
              aria-describedby="pkey-h"
            />
            <p id="pkey-h" class="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
              Stored encrypted on this server. After saving you will only see the last four
              characters.
            </p>
          </div>
        }
        @if (preset()?.kind === 'openai_compat') {
          <label class="flex items-center gap-2 text-sm sm:col-span-2">
            <input type="checkbox" class="size-4" formControlName="is_local" />
            This server runs on my own machine or network (counts as local)
          </label>
        }
        @if (error()) {
          <p class="alert-error sm:col-span-2" role="alert">{{ error() }}</p>
        }
        <div class="sm:col-span-2">
          <button class="btn btn-primary" type="submit" [disabled]="busy() || form.invalid">
            Add provider
          </button>
        </div>
      </form>
    </section>

    @if (loadError()) {
      <p class="alert-error mt-4" role="alert">{{ loadError() }}</p>
    }

    <ul class="mt-4 space-y-4" aria-label="Providers">
      @for (p of providers(); track p.id) {
        <li class="card" [attr.aria-label]="p.name">
          <div class="flex flex-wrap items-center justify-between gap-2">
            <div class="flex flex-wrap items-center gap-2">
              <h3 class="text-base font-semibold">{{ p.name }}</h3>
              <span class="badge">{{ p.is_local ? 'Local' : 'Remote' }}</span>
              <span class="badge">{{ p.kind }}</span>
              @if (!p.enabled) {
                <span class="badge">Switched off</span>
              }
            </div>
            <label class="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                class="size-4"
                [checked]="p.enabled"
                (change)="toggle(p, $any($event.target).checked)"
              />
              Enabled
            </label>
          </div>

          <p class="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
            @if (p.has_credential) {
              API key saved: <code class="font-mono">{{ p.credential_mask }}</code>
            } @else if (p.kind !== 'ollama') {
              No API key saved.
            }
            @if (p.base_url) {
              <span class="ml-2 break-all">{{ p.base_url }}</span>
            }
          </p>
          @if (p.last_test_at) {
            <p
              class="mt-1 text-sm"
              role="status"
              [class]="
                p.last_test_ok
                  ? 'text-green-800 dark:text-green-300'
                  : 'text-red-800 dark:text-red-300'
              "
            >
              {{ p.last_test_ok ? 'Connected' : 'Test failed' }}: {{ p.last_test_message }}
            </p>
          }

          <div class="mt-3 flex flex-wrap gap-2">
            <button
              type="button"
              class="btn btn-secondary py-1"
              [disabled]="busyId() === p.id"
              (click)="test(p)"
            >
              Test connection
            </button>
            <button
              type="button"
              class="btn btn-secondary py-1"
              [disabled]="busyId() === p.id"
              (click)="discover(p)"
            >
              Find models
            </button>
            @if (p.kind !== 'ollama') {
              <button
                type="button"
                class="btn btn-secondary py-1"
                (click)="keyFor.set(keyFor() === p.id ? null : p.id)"
              >
                {{ p.has_credential ? 'Replace key' : 'Add key' }}
              </button>
            }
            @if (p.has_credential) {
              <button type="button" class="btn btn-secondary py-1" (click)="removeKey(p)">
                Remove key
              </button>
            }
            <button type="button" class="btn btn-secondary py-1" (click)="askRemove(p)">
              Remove provider
            </button>
          </div>

          @if (keyFor() === p.id) {
            <form
              class="mt-3 flex gap-2"
              (submit)="$event.preventDefault(); saveKey(p, newKey.value); newKey.value = ''"
            >
              <label class="sr-only" [for]="'k-' + p.id">New API key for {{ p.name }}</label>
              <input
                #newKey
                [id]="'k-' + p.id"
                class="input"
                type="password"
                autocomplete="new-password"
                spellcheck="false"
              />
              <button class="btn btn-primary" type="submit">Save key</button>
            </form>
          }
          @if (messages()[p.id]; as m) {
            <p class="mt-2 text-sm" role="status">{{ m }}</p>
          }

          @if (p.kind === 'ollama') {
            <form
              class="mt-4 flex flex-wrap items-end gap-2"
              (submit)="$event.preventDefault(); pull(p, modelName.value)"
            >
              <div>
                <label class="label" [for]="'pull-' + p.id">Download a model</label>
                <input
                  #modelName
                  [id]="'pull-' + p.id"
                  class="input"
                  placeholder="e.g. qwen2.5:3b"
                  autocomplete="off"
                />
              </div>
              <button
                class="btn btn-secondary"
                type="submit"
                [disabled]="pulling()?.done === false"
              >
                Download
              </button>
              <a
                class="text-sm underline"
                href="https://ollama.com/library"
                target="_blank"
                rel="noopener noreferrer"
                >Browse models</a
              >
            </form>
            @if (pulling(); as pu) {
              @if (pu.providerId === p.id) {
                <div class="mt-2 text-sm" role="status">
                  <p>{{ pu.model }}: {{ pu.error || pu.status }}</p>
                  @if (!pu.done && !pu.error) {
                    <div
                      class="mt-1 h-2 max-w-md overflow-hidden rounded bg-zinc-200 dark:bg-zinc-800"
                      role="progressbar"
                      [attr.aria-valuenow]="pu.percent ?? 0"
                      aria-valuemin="0"
                      aria-valuemax="100"
                      [attr.aria-label]="'Downloading ' + pu.model"
                    >
                      <div class="h-full bg-accent" [style.width.%]="pu.percent ?? 5"></div>
                    </div>
                  }
                </div>
              }
            }
          }

          @if (modelsOf(p.id).length) {
            <h4 class="mt-4 text-sm font-semibold">Models ({{ modelsOf(p.id).length }})</h4>
            <div
              class="mt-1 max-h-96 overflow-auto rounded-md border border-zinc-200 dark:border-zinc-800"
            >
              <table class="w-full text-left text-sm">
                <thead
                  class="sticky top-0 bg-white text-zinc-600 dark:bg-zinc-900 dark:text-zinc-400"
                >
                  <tr>
                    <th scope="col" class="px-3 py-2 font-medium">Model</th>
                    <th scope="col" class="px-3 py-2 font-medium">Context</th>
                    <th scope="col" class="px-3 py-2 font-medium">Price per million tokens</th>
                    <th scope="col" class="px-3 py-2 font-medium">On</th>
                  </tr>
                </thead>
                <tbody class="divide-y divide-zinc-200 dark:divide-zinc-800">
                  @for (m of modelsOf(p.id); track m.id) {
                    <tr>
                      <td class="px-3 py-2">
                        <span class="break-all font-medium">{{ m.model_id }}</span>
                        @if (m.is_free && !m.is_local) {
                          <span
                            class="badge ml-2"
                            title="Free models are rate-limited and their availability is not guaranteed"
                          >
                            Free: rate-limited, availability not guaranteed
                          </span>
                        }
                      </td>
                      <td class="px-3 py-2">
                        {{ m.context_length ? m.context_length.toLocaleString() : 'unknown' }}
                      </td>
                      <td class="px-3 py-2">
                        @switch (m.price_source) {
                          @case ('local') {
                            Free (runs here)
                          }
                          @case ('free') {
                            Free
                          }
                          @case ('unknown') {
                            Unknown: costs will not be estimated
                          }
                          @default {
                            in {{ m.input_per_mtok }} / out {{ m.output_per_mtok }} USD ({{
                              m.price_source === 'user' ? 'your price' : 'from provider'
                            }})
                          }
                        }
                        @if (!m.is_local) {
                          <button
                            type="button"
                            class="ml-2 text-xs underline"
                            (click)="editPrice(m)"
                          >
                            Set price
                          </button>
                        }
                      </td>
                      <td class="px-3 py-2">
                        <input
                          type="checkbox"
                          class="size-4"
                          [checked]="m.enabled"
                          [attr.aria-label]="'Use ' + m.model_id"
                          (change)="toggleModel(m, $any($event.target).checked)"
                        />
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            </div>
          }
        </li>
      } @empty {
        @if (!loading()) {
          <li class="card">
            <p class="text-sm text-zinc-600 dark:text-zinc-400">
              No providers yet. Add Ollama above to get started without any account.
            </p>
          </li>
        }
      }
    </ul>

    <app-confirm-dialog
      #rmDialog
      heading="Remove this provider?"
      message="Its saved key, model list and any routes that use it are deleted. Past usage stays in the report."
      actionLabel="Remove provider"
      [busy]="busyId() !== null"
      [error]="removeError()"
      (confirmed)="remove()"
    />
  `,
})
export class ProvidersSection implements OnInit {
  private readonly api = inject(AiApi);
  private readonly fb = inject(FormBuilder).nonNullable;
  readonly changed = output<void>();
  readonly rmDialog = viewChild.required<ConfirmDialogComponent>('rmDialog');

  readonly providers = signal<Provider[]>([]);
  readonly models = signal<AiModel[]>([]);
  readonly presets = signal<Preset[]>([]);
  readonly preset = signal<Preset | null>(null);
  readonly loading = signal(true);
  readonly loadError = signal('');
  readonly error = signal('');
  readonly busy = signal(false);
  readonly busyId = signal<string | null>(null);
  readonly keyFor = signal<string | null>(null);
  readonly messages = signal<Record<string, string>>({});
  readonly pulling = signal<Pull | null>(null);
  readonly removing = signal<Provider | null>(null);
  readonly removeError = signal('');

  readonly showUrl = computed(() => {
    const k = this.preset()?.kind;
    return k === 'ollama' || k === 'openai_compat';
  });

  readonly form = this.fb.group({
    name: ['', [Validators.required, Validators.maxLength(100)]],
    base_url: [''],
    api_key: [''],
    is_local: [false],
  });

  modelsOf(id: string): AiModel[] {
    return this.models().filter((m) => m.provider_id === id);
  }

  async ngOnInit(): Promise<void> {
    try {
      const presets = await this.api.presets();
      this.presets.set(presets);
      this.applyPreset(presets[0]);
      await this.reload();
    } catch (e) {
      this.loadError.set(errorMessage(e));
    } finally {
      this.loading.set(false);
    }
  }

  private async reload(): Promise<void> {
    const [p, m] = await Promise.all([this.api.providers(), this.api.models()]);
    this.providers.set(p);
    this.models.set(m);
    this.changed.emit();
  }

  pickPreset(index: string): void {
    this.applyPreset(this.presets()[Number(index)]);
  }

  private applyPreset(p: Preset | undefined): void {
    if (!p) return;
    this.preset.set(p);
    this.form.reset({
      name: p.name,
      base_url: p.base_url ?? '',
      api_key: '',
      is_local: p.is_local,
    });
  }

  async add(): Promise<void> {
    const pr = this.preset();
    if (!pr || this.form.invalid) return;
    const v = this.form.getRawValue();
    const body: NewProvider = { kind: pr.kind, name: v.name.trim() };
    if (this.showUrl()) body.base_url = v.base_url.trim() || null;
    if (pr.kind === 'openai_compat') body.is_local = v.is_local;
    if (v.api_key.trim()) body.api_key = v.api_key.trim();
    this.busy.set(true);
    this.error.set('');
    try {
      const created = await this.api.createProvider(body);
      this.form.patchValue({ api_key: '' }); // never keep a typed key around
      await this.reload();
      this.say(created.id, 'Added. Next: Test connection, then Find models.');
    } catch (e) {
      this.error.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }

  private say(id: string, text: string): void {
    this.messages.update((m) => ({ ...m, [id]: text }));
  }

  private async run(p: Provider, fn: () => Promise<void>): Promise<void> {
    this.busyId.set(p.id);
    try {
      await fn();
    } catch (e) {
      this.say(p.id, errorMessage(e));
    } finally {
      this.busyId.set(null);
    }
  }

  test(p: Provider): Promise<void> {
    return this.run(p, async () => {
      const r = await this.api.test(p.id);
      await this.reload();
      this.say(p.id, r.ok ? `Connected in ${r.latency_ms} ms.` : '');
    });
  }

  discover(p: Provider): Promise<void> {
    return this.run(p, async () => {
      const r = await this.api.discover(p.id);
      await this.reload();
      this.say(p.id, `Found ${r.added + r.updated} models (${r.added} new).`);
    });
  }

  async toggle(p: Provider, enabled: boolean): Promise<void> {
    await this.run(p, async () => {
      await this.api.patchProvider(p.id, { enabled });
      await this.reload();
    });
  }

  saveKey(p: Provider, key: string): Promise<void> {
    return this.run(p, async () => {
      if (key.trim().length < 8) throw new Error('That key looks too short.');
      await this.api.setKey(p.id, key.trim());
      this.keyFor.set(null);
      await this.reload();
      this.say(p.id, 'Key saved.');
    });
  }

  removeKey(p: Provider): Promise<void> {
    return this.run(p, async () => {
      await this.api.clearKey(p.id);
      await this.reload();
      this.say(p.id, 'Key removed.');
    });
  }

  askRemove(p: Provider): void {
    this.removing.set(p);
    this.removeError.set('');
    this.rmDialog().open();
  }

  async remove(): Promise<void> {
    const p = this.removing();
    if (!p) return;
    this.busyId.set(p.id);
    try {
      await this.api.deleteProvider(p.id);
      this.rmDialog().close();
      await this.reload();
    } catch (e) {
      this.removeError.set(errorMessage(e));
    } finally {
      this.busyId.set(null);
    }
  }

  async toggleModel(m: AiModel, enabled: boolean): Promise<void> {
    try {
      const u = await this.api.setModelEnabled(m.id, enabled);
      this.models.update((l) => l.map((x) => (x.id === u.id ? u : x)));
      this.changed.emit();
    } catch (e) {
      this.say(m.provider_id, errorMessage(e));
    }
  }

  async editPrice(m: AiModel): Promise<void> {
    const raw = window.prompt(
      `Price in USD per million tokens for ${m.model_id}.\nFormat: input,output (for example 3,15). Leave empty to use the provider's price.`,
    );
    if (raw === null) return;
    try {
      const u = raw.trim()
        ? await this.api.setPrice(
            m.id,
            ...(raw.split(',').map((x) => Number(x.trim())) as [number, number]),
          )
        : await this.api.clearPrice(m.id);
      this.models.update((l) => l.map((x) => (x.id === u.id ? u : x)));
    } catch (e) {
      this.say(m.provider_id, errorMessage(e));
    }
  }

  async pull(p: Provider, model: string): Promise<void> {
    const name = model.trim();
    if (!name) return;
    const state: Pull = {
      providerId: p.id,
      model: name,
      status: 'Starting…',
      percent: null,
      error: '',
      done: false,
    };
    this.pulling.set(state);
    try {
      await this.api.pull(p.id, name, (e) => {
        if (e.error) this.pulling.set({ ...state, error: e.error, done: true });
        else
          this.pulling.set({
            ...state,
            status: e.status ?? '',
            percent: e.total ? Math.round(((e.completed ?? 0) / e.total) * 100) : null,
            done: e.status === 'success',
          });
      });
      if (!this.pulling()?.error)
        this.pulling.set({ ...state, status: 'Downloaded', percent: 100, done: true });
      await this.discover(p);
    } catch (e) {
      this.pulling.set({ ...state, error: errorMessage(e), done: true });
    }
  }
}
