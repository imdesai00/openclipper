import { Component, computed, input } from '@angular/core';

const LABELS: Record<string, string> = {
  pending: 'Waiting',
  queued: 'Waiting',
  processing: 'Processing',
  running: 'Running',
  ready: 'Ready',
  succeeded: 'Done',
  skipped: 'Reused',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

const TONE: Record<string, string> = {
  ready: 'border-green-700 text-green-800 dark:border-green-500 dark:text-green-300',
  succeeded: 'border-green-700 text-green-800 dark:border-green-500 dark:text-green-300',
  skipped: 'border-green-700 text-green-800 dark:border-green-500 dark:text-green-300',
  failed: 'border-red-700 text-red-800 dark:border-red-500 dark:text-red-300',
  cancelled: 'border-zinc-400 text-zinc-700 dark:border-zinc-600 dark:text-zinc-300',
  processing: 'border-indigo-700 text-indigo-800 dark:border-indigo-400 dark:text-indigo-300',
  running: 'border-indigo-700 text-indigo-800 dark:border-indigo-400 dark:text-indigo-300',
};

/** Status is always conveyed by text, never by colour alone. */
@Component({
  selector: 'app-status-badge',
  template: `<span class="badge" [class]="tone()">{{ label() }}</span>`,
})
export class StatusBadgeComponent {
  readonly status = input.required<string>();
  readonly label = computed(() => LABELS[this.status()] ?? this.status());
  readonly tone = computed(() => TONE[this.status()] ?? '');
}
