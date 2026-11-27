import { Component, OnDestroy, OnInit, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import {
  LucideFolderOpen,
  LucideLayoutDashboard,
  LucideLogOut,
  LucideMoon,
  LucideSettings,
  LucideSun,
  LucideUsers,
} from '@lucide/angular';

import { AuthService } from '../../core/auth.service';
import { RealtimeService } from '../../core/realtime.service';
import { ThemeService } from '../../core/theme.service';

@Component({
  selector: 'app-shell',
  imports: [
    RouterOutlet,
    RouterLink,
    RouterLinkActive,
    LucideLayoutDashboard,
    LucideFolderOpen,
    LucideSettings,
    LucideUsers,
    LucideLogOut,
    LucideSun,
    LucideMoon,
  ],
  template: `
    <div class="min-h-screen md:flex">
      <aside
        class="border-b border-zinc-200 bg-white md:w-56 md:shrink-0 md:border-r md:border-b-0 dark:border-zinc-800 dark:bg-zinc-900"
      >
        <div class="flex items-center justify-between px-4 py-3 md:block">
          <span class="text-base font-semibold tracking-tight">OpenClipper</span>
        </div>
        <nav aria-label="Main" class="flex gap-1 px-2 pb-2 md:block md:space-y-1 md:pb-0">
          <a
            routerLink="/"
            [routerLinkActiveOptions]="{ exact: true }"
            routerLinkActive="bg-zinc-100 dark:bg-zinc-800"
            class="flex items-center gap-2 rounded-md px-3 py-2 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
          >
            <svg lucideLayoutDashboard class="size-4" aria-hidden="true"></svg> Dashboard
          </a>
          <a
            routerLink="/projects"
            routerLinkActive="bg-zinc-100 dark:bg-zinc-800"
            class="flex items-center gap-2 rounded-md px-3 py-2 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
          >
            <svg lucideFolderOpen class="size-4" aria-hidden="true"></svg> Projects
          </a>
          @if (auth.isAdmin()) {
            <a
              routerLink="/settings"
              routerLinkActive="bg-zinc-100 dark:bg-zinc-800"
              class="flex items-center gap-2 rounded-md px-3 py-2 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
            >
              <svg lucideSettings class="size-4" aria-hidden="true"></svg> Settings
            </a>
            <a
              routerLink="/users"
              routerLinkActive="bg-zinc-100 dark:bg-zinc-800"
              class="flex items-center gap-2 rounded-md px-3 py-2 text-sm hover:bg-zinc-100 dark:hover:bg-zinc-800"
            >
              <svg lucideUsers class="size-4" aria-hidden="true"></svg> Users
            </a>
          }
        </nav>
      </aside>

      <div class="flex min-w-0 flex-1 flex-col">
        <header
          class="flex items-center justify-end gap-3 border-b border-zinc-200 bg-white px-4 py-2 dark:border-zinc-800 dark:bg-zinc-900"
        >
          <span class="text-sm text-zinc-600 dark:text-zinc-400">
            {{ auth.user()?.display_name }}
            <span class="badge ml-1">{{ auth.user()?.role }}</span>
          </span>
          <button
            type="button"
            class="btn btn-secondary px-2.5"
            (click)="theme.toggle()"
            [attr.aria-label]="theme.dark() ? 'Switch to light theme' : 'Switch to dark theme'"
          >
            @if (theme.dark()) {
              <svg lucideSun class="size-4" aria-hidden="true"></svg>
            } @else {
              <svg lucideMoon class="size-4" aria-hidden="true"></svg>
            }
          </button>
          <button type="button" class="btn btn-secondary" (click)="logout()">
            <svg lucideLogOut class="size-4" aria-hidden="true"></svg> Sign out
          </button>
        </header>
        <main class="flex-1 p-4 md:p-8"><router-outlet /></main>
      </div>
    </div>
  `,
})
export class ShellComponent implements OnInit, OnDestroy {
  readonly auth = inject(AuthService);
  readonly theme = inject(ThemeService);
  private readonly router = inject(Router);
  private readonly realtime = inject(RealtimeService);

  ngOnInit(): void {
    this.realtime.start();
  }

  ngOnDestroy(): void {
    this.realtime.stop();
  }

  async logout(): Promise<void> {
    try {
      await this.auth.logout();
    } finally {
      await this.router.navigateByUrl('/login');
    }
  }
}
