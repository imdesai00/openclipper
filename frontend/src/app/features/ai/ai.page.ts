import { Component, signal } from '@angular/core';

import { ProvidersSection } from './providers.section';
import { RoutingSection } from './routing.section';
import { UsageSection } from './usage.section';

type Tab = 'providers' | 'routing' | 'usage';

@Component({
  selector: 'app-ai-page',
  imports: [ProvidersSection, RoutingSection, UsageSection],
  template: `
    <h1 class="text-2xl font-semibold tracking-tight">AI</h1>
    <p class="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
      Choose which models do the thinking: on this machine, online, or both.
    </p>

    <div
      role="tablist"
      aria-label="AI settings"
      class="mt-6 flex gap-1 border-b border-zinc-200 dark:border-zinc-800"
    >
      @for (t of tabs; track t.id) {
        <button
          type="button"
          role="tab"
          [id]="'tab-' + t.id"
          [attr.aria-selected]="tab() === t.id"
          [attr.aria-controls]="'panel-' + t.id"
          [tabindex]="tab() === t.id ? 0 : -1"
          class="-mb-px border-b-2 px-4 py-2 text-sm font-medium"
          [class]="
            tab() === t.id
              ? 'border-accent text-zinc-900 dark:border-accent-fg dark:text-zinc-100'
              : 'border-transparent text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100'
          "
          (click)="tab.set(t.id)"
          (keydown.arrowright)="move(1)"
          (keydown.arrowleft)="move(-1)"
        >
          {{ t.label }}
        </button>
      }
    </div>

    <div
      class="mt-6"
      role="tabpanel"
      [id]="'panel-' + tab()"
      [attr.aria-labelledby]="'tab-' + tab()"
    >
      @switch (tab()) {
        @case ('providers') {
          <app-providers-section />
        }
        @case ('routing') {
          <app-routing-section />
        }
        @case ('usage') {
          <app-usage-section />
        }
      }
    </div>
  `,
})
export class AiPage {
  readonly tabs: { id: Tab; label: string }[] = [
    { id: 'providers', label: 'Providers and models' },
    { id: 'routing', label: 'Routing and test' },
    { id: 'usage', label: 'Usage' },
  ];
  readonly tab = signal<Tab>('providers');

  move(delta: number): void {
    const i = this.tabs.findIndex((t) => t.id === this.tab());
    this.tab.set(this.tabs[(i + delta + this.tabs.length) % this.tabs.length].id);
    setTimeout(() => document.getElementById('tab-' + this.tab())?.focus());
  }
}
