import { DatePipe } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';

import { AiApi, UsageReport } from '../../core/ai-api.service';
import { errorMessage } from '../../core/api';

@Component({
  selector: 'app-usage-section',
  imports: [DatePipe],
  template: `
    <div class="flex items-end gap-3">
      <div>
        <label class="label" for="days">Period</label>
        <select id="days" class="input w-auto" (change)="load(+$any($event.target).value)">
          <option value="7">Last 7 days</option>
          <option value="30" selected>Last 30 days</option>
          <option value="90">Last 90 days</option>
        </select>
      </div>
    </div>
    @if (error()) {
      <p class="alert-error mt-3" role="alert">{{ error() }}</p>
    }
    @if (report(); as r) {
      @if (r.calls === 0) {
        <div class="card mt-4">
          <p class="text-sm text-zinc-600 dark:text-zinc-400">No AI calls in this period yet.</p>
        </div>
      } @else {
        <dl class="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-5">
          <div class="card p-4">
            <dt class="text-sm text-zinc-600 dark:text-zinc-400">Calls</dt>
            <dd class="mt-1 text-2xl font-semibold">{{ r.calls }}</dd>
          </div>
          <div class="card p-4">
            <dt class="text-sm text-zinc-600 dark:text-zinc-400">Failed</dt>
            <dd class="mt-1 text-2xl font-semibold">{{ r.errors }}</dd>
          </div>
          <div class="card p-4">
            <dt class="text-sm text-zinc-600 dark:text-zinc-400">Tokens in</dt>
            <dd class="mt-1 text-2xl font-semibold">{{ r.tokens_in.toLocaleString() }}</dd>
          </div>
          <div class="card p-4">
            <dt class="text-sm text-zinc-600 dark:text-zinc-400">Tokens out</dt>
            <dd class="mt-1 text-2xl font-semibold">{{ r.tokens_out.toLocaleString() }}</dd>
          </div>
          <div class="card p-4">
            <dt class="text-sm text-zinc-600 dark:text-zinc-400">Estimated cost</dt>
            <dd class="mt-1 text-2xl font-semibold">{{ money(r.cost_est) }}</dd>
          </div>
        </dl>
        <p class="mt-2 text-sm text-zinc-600 dark:text-zinc-400">
          Costs are estimates from token counts and the prices shown under each model.
          @if (r.calls_with_unknown_cost) {
            {{ r.calls_with_unknown_cost }} calls used models with no known price and are not
            included.
          }
        </p>

        <h2 class="mt-6 text-base font-semibold">By model</h2>
        <div class="card mt-2 overflow-x-auto p-0">
          <table class="w-full text-left text-sm">
            <thead class="text-zinc-600 dark:text-zinc-400">
              <tr>
                <th scope="col" class="px-4 py-2 font-medium">Model</th>
                <th scope="col" class="px-4 py-2 font-medium">Calls</th>
                <th scope="col" class="px-4 py-2 font-medium">Failed</th>
                <th scope="col" class="px-4 py-2 font-medium">Tokens (in / out)</th>
                <th scope="col" class="px-4 py-2 font-medium">Est. cost</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-zinc-200 dark:divide-zinc-800">
              @for (m of r.by_model; track m.provider + m.model) {
                <tr>
                  <td class="px-4 py-2">{{ m.provider }} · {{ m.model }}</td>
                  <td class="px-4 py-2">{{ m.calls }}</td>
                  <td class="px-4 py-2">{{ m.errors }}</td>
                  <td class="px-4 py-2">
                    {{ m.tokens_in.toLocaleString() }} / {{ m.tokens_out.toLocaleString() }}
                  </td>
                  <td class="px-4 py-2">{{ money(m.cost_est) }}</td>
                </tr>
              }
            </tbody>
          </table>
        </div>

        <h2 class="mt-6 text-base font-semibold">Recent calls</h2>
        <div class="card mt-2 overflow-x-auto p-0">
          <table class="w-full text-left text-sm">
            <thead class="text-zinc-600 dark:text-zinc-400">
              <tr>
                <th scope="col" class="px-4 py-2 font-medium">When</th>
                <th scope="col" class="px-4 py-2 font-medium">Model</th>
                <th scope="col" class="px-4 py-2 font-medium">Result</th>
                <th scope="col" class="px-4 py-2 font-medium">Time</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-zinc-200 dark:divide-zinc-800">
              @for (u of r.recent; track u.id) {
                <tr>
                  <td class="px-4 py-2">{{ u.created_at | date: 'medium' }}</td>
                  <td class="px-4 py-2">
                    {{ u.provider_name }} · {{ u.model_name
                    }}<span class="text-zinc-600 dark:text-zinc-400"> ({{ u.task }})</span>
                  </td>
                  <td class="px-4 py-2">
                    @if (u.status === 'ok') {
                      OK, {{ u.tokens_in }} in / {{ u.tokens_out }} out
                    } @else {
                      <span class="text-red-800 dark:text-red-300"
                        >Failed: {{ u.error_message }}</span
                      >
                    }
                  </td>
                  <td class="px-4 py-2">{{ u.latency_ms }} ms</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      }
    }
  `,
})
export class UsageSection implements OnInit {
  private readonly api = inject(AiApi);
  readonly report = signal<UsageReport | null>(null);
  readonly error = signal('');

  ngOnInit(): void {
    void this.load(30);
  }

  async load(days: number): Promise<void> {
    try {
      this.report.set(await this.api.usage(days));
      this.error.set('');
    } catch (e) {
      this.error.set(errorMessage(e));
    }
  }

  money(v: string): string {
    const n = Number(v);
    return n === 0 ? '$0' : `$${n < 0.01 ? n.toFixed(4) : n.toFixed(2)}`;
  }
}
