import { Component, input } from '@angular/core';

@Component({
  selector: 'app-auth-card',
  template: `
    <main class="flex min-h-screen items-center justify-center px-4 py-10">
      <div class="w-full max-w-md">
        <p class="mb-6 text-center text-lg font-semibold tracking-tight">OpenClipper</p>
        <section class="card">
          <h1 class="text-xl font-semibold">{{ heading() }}</h1>
          @if (subtitle()) {
            <p class="mt-1 text-sm text-zinc-600 dark:text-zinc-400">{{ subtitle() }}</p>
          }
          <div class="mt-5"><ng-content /></div>
        </section>
      </div>
    </main>
  `,
})
export class AuthCardComponent {
  readonly heading = input.required<string>();
  readonly subtitle = input<string>('');
}
