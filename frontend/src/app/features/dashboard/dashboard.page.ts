import { HttpClient } from '@angular/common/http';
import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { formatBytes } from '../../core/format';
import { MediaApi } from '../../core/media-api.service';
import { DashboardTotals } from '../../core/models';

interface Ready {
  status: string;
  checks: Record<string, string>;
}

@Component({
  selector: 'app-dashboard-page',
  imports: [RouterLink],
  template: `
    <h1 class="text-2xl font-semibold tracking-tight">Dashboard</h1>

    @if (totals(); as t) {
      @if (t.projects === 0) {
        <section class="card mt-6">
          <h2 class="text-base font-semibold">Nothing here yet</h2>
          <p class="mt-1 max-w-prose text-sm text-zinc-600 dark:text-zinc-400">
            Numbers appear here once you have projects and videos.
            <a routerLink="/projects" class="underline">Create your first project</a>.
          </p>
        </section>
      } @else {
        <dl class="mt-6 grid grid-cols-2 gap-3 lg:grid-cols-5" aria-label="Totals">
          @for (c of cards(t); track c.label) {
            <div class="card p-4">
              <dt class="text-sm text-zinc-600 dark:text-zinc-400">{{ c.label }}</dt>
              <dd class="mt-1 text-2xl font-semibold">{{ c.value }}</dd>
            </div>
          }
        </dl>
      }
    } @else if (totalsError()) {
      <p class="alert-error mt-6" role="alert">Could not load totals.</p>
    }

    <section class="card mt-4" aria-labelledby="sys">
      <h2 id="sys" class="text-base font-semibold">System status</h2>
      @if (loading()) {
        <p class="mt-2 text-sm text-zinc-600 dark:text-zinc-400">Checking…</p>
      } @else if (failed()) {
        <p class="alert-error mt-2" role="alert">Could not read system status.</p>
      } @else {
        <ul class="mt-2 divide-y divide-zinc-200 text-sm dark:divide-zinc-800">
          @for (c of checks(); track c[0]) {
            <li class="flex items-center justify-between py-2">
              <span class="capitalize">{{ c[0] }}</span>
              <span [class]="c[1] === 'ok' ? 'badge' : 'badge border-red-600 text-red-700'">
                {{ c[1] === 'ok' ? 'OK' : c[1] }}
              </span>
            </li>
          }
        </ul>
      }
    </section>
  `,
})
export class DashboardPage implements OnInit {
  private readonly http = inject(HttpClient);
  private readonly api = inject(MediaApi);
  readonly loading = signal(true);
  readonly failed = signal(false);
  readonly checks = signal<[string, string][]>([]);
  readonly totals = signal<DashboardTotals | null>(null);
  readonly totalsError = signal(false);

  cards(t: DashboardTotals): { label: string; value: string }[] {
    return [
      { label: 'Projects', value: String(t.projects) },
      { label: 'Videos', value: String(t.sources) },
      { label: 'Ready', value: String(t.sources_ready) },
      { label: 'Processing now', value: String(t.jobs_running) },
      { label: 'Storage used', value: formatBytes(t.storage_bytes) },
    ];
  }

  async ngOnInit(): Promise<void> {
    this.api.dashboard().then(
      (t) => this.totals.set(t),
      () => this.totalsError.set(true),
    );
    try {
      // The endpoint answers 503 with the same body when a check fails; surface both cases.
      const r = await firstValueFrom(this.http.get<Ready>('/api/v1/health/ready'));
      this.checks.set(Object.entries(r.checks));
    } catch (e: unknown) {
      const body = (e as { error?: Ready }).error;
      if (body?.checks) this.checks.set(Object.entries(body.checks));
      else this.failed.set(true);
    } finally {
      this.loading.set(false);
    }
  }
}
