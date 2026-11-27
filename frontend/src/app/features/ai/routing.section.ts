import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import {
  AiApi,
  AiModel,
  Mode,
  PromptResult,
  RouteEntry,
  TASKS,
  Task,
} from '../../core/ai-api.service';
import { errorMessage } from '../../core/api';

const MODES: { id: Mode; label: string; help: string }[] = [
  {
    id: 'LOCAL',
    label: 'Local only',
    help: 'Only models running on your own machine or network. Nothing is sent to an online service.',
  },
  {
    id: 'REMOTE',
    label: 'Online only',
    help: 'Only hosted services. Your transcripts are sent to them.',
  },
  {
    id: 'HYBRID',
    label: 'Mixed',
    help: 'Use whichever you choose for each task, local and online together.',
  },
];
const SLOTS = ['First choice', 'If that fails, try', 'Then try'];

@Component({
  selector: 'app-routing-section',
  imports: [FormsModule],
  template: `
    <section class="card" aria-labelledby="mode-h">
      <h2 id="mode-h" class="text-base font-semibold">Where may AI run?</h2>
      <fieldset class="mt-3">
        <legend class="sr-only">AI mode</legend>
        <div class="space-y-2">
          @for (m of modes; track m.id) {
            <label class="flex items-start gap-2 text-sm">
              <input
                type="radio"
                name="mode"
                class="mt-0.5"
                [value]="m.id"
                [checked]="mode() === m.id"
                (change)="setMode(m.id)"
              />
              <span
                ><strong>{{ m.label }}.</strong> {{ m.help }}</span
              >
            </label>
          }
        </div>
      </fieldset>
      @if (modeError()) {
        <p class="alert-error mt-2" role="alert">{{ modeError() }}</p>
      }
    </section>

    <section class="card mt-4" aria-labelledby="routes-h">
      <h2 id="routes-h" class="text-base font-semibold">Which model does each job use?</h2>
      <p class="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
        If the first choice fails, the next one is tried automatically.
      </p>
      <div class="mt-4 space-y-5">
        @for (t of tasks; track t.id) {
          <fieldset>
            <legend class="text-sm font-medium">
              {{ t.label }}
              <span class="font-normal text-zinc-600 dark:text-zinc-400">· {{ t.help }}</span>
            </legend>
            <div class="mt-2 grid gap-2 md:grid-cols-3">
              @for (label of slotLabels; track $index) {
                <div>
                  <label class="label" [for]="t.id + '-' + $index">{{ label }}</label>
                  <select
                    [id]="t.id + '-' + $index"
                    class="input"
                    [value]="slot(t.id, $index)"
                    (change)="setSlot(t.id, $index, $any($event.target).value)"
                  >
                    <option value="">{{ $index === 0 ? 'Not set' : 'None' }}</option>
                    @for (m of enabledModels(); track m.id) {
                      <option [value]="m.id">
                        {{ m.provider_name }} · {{ m.model_id }}{{ m.is_local ? '' : ' (online)' }}
                      </option>
                    }
                  </select>
                </div>
              }
            </div>
            @for (r of saved()[t.id] ?? []; track r.model_id) {
              @if (!r.usable) {
                <p class="mt-1 text-sm text-amber-800 dark:text-amber-300" role="status">
                  {{ r.model_name }} is saved but cannot be used now: {{ r.unusable_reason }}
                </p>
              }
            }
          </fieldset>
        }
      </div>
      @if (routeError()) {
        <p class="alert-error mt-3" role="alert">{{ routeError() }}</p>
      }
      @if (routeSaved()) {
        <p class="mt-3 text-sm text-green-800 dark:text-green-300" role="status">Saved.</p>
      }
      <button type="button" class="btn btn-primary mt-4" [disabled]="busy()" (click)="save()">
        Save choices
      </button>
    </section>

    <section class="card mt-4" aria-labelledby="try-h">
      <h2 id="try-h" class="text-base font-semibold">Try a prompt</h2>
      <p class="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
        Runs the real route, so you see exactly what a job would get, including cost.
      </p>
      <div class="mt-3 grid gap-3 md:grid-cols-2">
        <div>
          <label class="label" for="try-task">Job</label>
          <select id="try-task" class="input" [(ngModel)]="tryTask">
            @for (t of tasks; track t.id) {
              <option [value]="t.id">{{ t.label }}</option>
            }
          </select>
        </div>
        <div>
          <label class="label" for="try-model">Or one specific model</label>
          <select id="try-model" class="input" [(ngModel)]="tryModel">
            <option value="">Use the job's choices</option>
            @for (m of enabledModels(); track m.id) {
              <option [value]="m.id">{{ m.provider_name }} · {{ m.model_id }}</option>
            }
          </select>
        </div>
        <div class="md:col-span-2">
          <label class="label" for="try-prompt">Prompt</label>
          <textarea id="try-prompt" class="input" rows="3" [(ngModel)]="tryPrompt"></textarea>
        </div>
        <label class="flex items-center gap-2 text-sm md:col-span-2">
          <input type="checkbox" class="size-4" [(ngModel)]="tryJson" /> Ask for JSON (tests
          structured output)
        </label>
      </div>
      <button
        type="button"
        class="btn btn-primary mt-3"
        [disabled]="trying() || !tryPrompt.trim()"
        (click)="run()"
      >
        {{ trying() ? 'Waiting for the model…' : 'Run' }}
      </button>
      @if (tryError()) {
        <div class="alert-error mt-3" role="alert">
          <p>{{ tryError() }}</p>
          @for (a of tryDetails(); track $index) {
            <p class="mt-1 text-xs">{{ a }}</p>
          }
        </div>
      }
      @if (result(); as r) {
        <div
          class="mt-3 rounded-md border border-zinc-200 p-3 text-sm dark:border-zinc-800"
          role="status"
        >
          <pre class="whitespace-pre-wrap break-words font-sans">{{ r.text }}</pre>
          <p class="mt-2 text-zinc-600 dark:text-zinc-400">
            {{ r.provider }} · {{ r.model }} · {{ r.tokens_in }} in / {{ r.tokens_out }} out ·
            {{ r.latency_ms }} ms ·
            @if (r.cost_est === null) {
              cost unknown (no price for this model)
            } @else {
              about {{ r.cost_est }} USD (estimate)
            }
            @if (r.repairs) {
              · fixed {{ r.repairs }}×
            }
          </p>
          @for (a of r.failed_attempts; track $index) {
            <p class="mt-1 text-amber-800 dark:text-amber-300">
              Skipped {{ a.provider }} · {{ a.model }}: {{ a.message }}
            </p>
          }
        </div>
      }
    </section>
  `,
})
export class RoutingSection implements OnInit {
  private readonly api = inject(AiApi);
  readonly modes = MODES;
  readonly tasks = TASKS;
  readonly slotLabels = SLOTS;

  readonly mode = signal<Mode>('LOCAL');
  readonly modeError = signal('');
  readonly models = signal<AiModel[]>([]);
  readonly enabledModels = computed(() => this.models().filter((m) => m.enabled));
  readonly saved = signal<Record<string, RouteEntry[]>>({});
  readonly slots = signal<Record<string, string[]>>({});
  readonly routeError = signal('');
  readonly routeSaved = signal(false);
  readonly busy = signal(false);

  tryTask: Task = 'highlight';
  tryModel = '';
  tryPrompt = 'Reply with the single word: pong';
  tryJson = false;
  readonly trying = signal(false);
  readonly tryError = signal('');
  readonly tryDetails = signal<string[]>([]);
  readonly result = signal<PromptResult | null>(null);

  async ngOnInit(): Promise<void> {
    await this.load();
  }

  async load(): Promise<void> {
    try {
      const [models, routes] = await Promise.all([this.api.models(), this.api.routes()]);
      this.models.set(models);
      this.mode.set(routes.mode);
      this.saved.set(routes.routes);
      const s: Record<string, string[]> = {};
      for (const t of TASKS) s[t.id] = (routes.routes[t.id] ?? []).map((r) => r.model_id);
      this.slots.set(s);
    } catch (e) {
      this.routeError.set(errorMessage(e));
    }
  }

  slot(task: string, i: number): string {
    return this.slots()[task]?.[i] ?? '';
  }

  setSlot(task: string, i: number, id: string): void {
    this.slots.update((s) => {
      const arr = [...(s[task] ?? [])];
      while (arr.length <= i) arr.push('');
      arr[i] = id;
      return { ...s, [task]: arr };
    });
    this.routeSaved.set(false);
  }

  async setMode(m: Mode): Promise<void> {
    this.modeError.set('');
    try {
      await this.api.setMode(m);
      this.mode.set(m);
      await this.load();
    } catch (e) {
      this.modeError.set(errorMessage(e));
    }
  }

  async save(): Promise<void> {
    this.busy.set(true);
    this.routeError.set('');
    try {
      const body: Record<string, string[]> = {};
      for (const t of TASKS) body[t.id] = (this.slots()[t.id] ?? []).filter(Boolean);
      const r = await this.api.saveRoutes(body);
      this.saved.set(r.routes);
      this.routeSaved.set(true);
    } catch (e) {
      this.routeError.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }

  async run(): Promise<void> {
    this.trying.set(true);
    this.tryError.set('');
    this.tryDetails.set([]);
    this.result.set(null);
    try {
      this.result.set(
        await this.api.testPrompt({
          prompt: this.tryPrompt,
          task: this.tryTask,
          model_id: this.tryModel || null,
          json_mode: this.tryJson,
        }),
      );
    } catch (e) {
      this.tryError.set(errorMessage(e));
      const d = (
        e as { error?: { details?: { provider: string; model: string; message: string }[] } }
      ).error?.details;
      if (Array.isArray(d))
        this.tryDetails.set(d.map((a) => `${a.provider} · ${a.model}: ${a.message}`));
    } finally {
      this.trying.set(false);
    }
  }
}
