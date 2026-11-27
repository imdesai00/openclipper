import { HttpClient } from '@angular/common/http';
import { Component, OnInit, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

interface Ready {
  status: string;
  checks: Record<string, string>;
}

@Component({
  selector: 'app-dashboard-page',
  template: `
    <h1 class="text-2xl font-semibold tracking-tight">Dashboard</h1>

    <section class="card mt-6">
      <h2 class="text-base font-semibold">Nothing here yet</h2>
      <p class="mt-1 max-w-prose text-sm text-zinc-600 dark:text-zinc-400">
        Projects, video sources and clips are not available in this version yet, so there are no
        numbers to show. Totals will appear here once they can be counted from real data.
      </p>
    </section>

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
  readonly loading = signal(true);
  readonly failed = signal(false);
  readonly checks = signal<[string, string][]>([]);

  async ngOnInit(): Promise<void> {
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
