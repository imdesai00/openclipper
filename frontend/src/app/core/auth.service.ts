import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { User } from './api';

interface AuthResponse {
  access_token: string;
  expires_in: number;
  user: User;
}

const CLIENT_HEADER = { 'X-OpenClipper-Client': 'web' };
const API = '/api/v1';

@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly http = inject(HttpClient);

  /** Access token lives in memory only; the refresh token is an httpOnly cookie. */
  private token: string | null = null;
  private inflightRefresh: Promise<boolean> | null = null;
  private setupCache: boolean | null = null;
  private expiresAt = 0;

  readonly user = signal<User | null>(null);
  readonly isAdmin = computed(() => this.user()?.role === 'admin');

  accessToken(): string | null {
    return this.token;
  }

  /** Milliseconds until the in-memory access token expires (0 when signed out). */
  expiresInMs(): number {
    return this.token ? Math.max(0, this.expiresAt - Date.now()) : 0;
  }

  /** Called once at app start: restores the session if a valid refresh cookie exists. */
  async bootstrap(): Promise<void> {
    await this.refresh();
  }

  async setupRequired(): Promise<boolean> {
    if (this.setupCache === false) return false; // setup can never become required again
    const r = await firstValueFrom(
      this.http.get<{ setup_required: boolean }>(`${API}/setup/status`),
    );
    this.setupCache = r.setup_required;
    return r.setup_required;
  }

  async setupAdmin(input: {
    email: string;
    display_name: string;
    password: string;
  }): Promise<void> {
    this.apply(await firstValueFrom(this.http.post<AuthResponse>(`${API}/setup/admin`, input)));
    this.setupCache = false;
  }

  async login(email: string, password: string): Promise<void> {
    this.apply(
      await firstValueFrom(this.http.post<AuthResponse>(`${API}/auth/login`, { email, password })),
    );
  }

  async acceptInvite(input: {
    token: string;
    display_name: string;
    password: string;
  }): Promise<void> {
    this.apply(
      await firstValueFrom(this.http.post<AuthResponse>(`${API}/auth/accept-invite`, input)),
    );
  }

  /** Single-flight: concurrent callers share one refresh (rotation would reject the second). */
  refresh(): Promise<boolean> {
    this.inflightRefresh ??= this.doRefresh().finally(() => {
      this.inflightRefresh = null;
    });
    return this.inflightRefresh;
  }

  private async doRefresh(): Promise<boolean> {
    try {
      const r = await firstValueFrom(
        this.http.post<AuthResponse>(`${API}/auth/refresh`, null, { headers: CLIENT_HEADER }),
      );
      this.apply(r);
      return true;
    } catch {
      this.clear();
      return false;
    }
  }

  async logout(): Promise<void> {
    try {
      await firstValueFrom(
        this.http.post<void>(`${API}/auth/logout`, null, { headers: CLIENT_HEADER }),
      );
    } finally {
      this.clear();
    }
  }

  clear(): void {
    this.token = null;
    this.expiresAt = 0;
    this.user.set(null);
  }

  private apply(r: AuthResponse): void {
    this.token = r.access_token;
    this.expiresAt = Date.now() + r.expires_in * 1000;
    this.user.set(r.user);
  }
}
