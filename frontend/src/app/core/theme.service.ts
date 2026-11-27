import { Injectable, signal } from '@angular/core';

const KEY = 'oc-theme';

@Injectable({ providedIn: 'root' })
export class ThemeService {
  readonly dark = signal(document.documentElement.classList.contains('dark'));

  toggle(): void {
    const next = !this.dark();
    this.dark.set(next);
    document.documentElement.classList.toggle('dark', next);
    try {
      localStorage.setItem(KEY, next ? 'dark' : 'light');
    } catch {
      /* storage can be unavailable (private mode); the theme still applies for this session */
    }
  }
}
