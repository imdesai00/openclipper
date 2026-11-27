import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Route, Router, UrlTree, provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it } from 'vitest';

import { User } from './api';
import { AuthService } from './auth.service';
import { adminGuard, authGuard, loginGuard, setupGuard } from './guards';

const mk = (role: User['role']): User => ({
  id: '1',
  email: 'a@example.com',
  display_name: 'A',
  role,
  is_active: true,
  created_at: '',
  last_login_at: null,
});

class FakeAuth {
  user = signal<User | null>(null);
  isAdmin = () => this.user()?.role === 'admin';
  needSetup = false;
  setupRequired = async () => this.needSetup;
}

describe('route guards', () => {
  let fake: FakeAuth;
  let router: Router;

  const run = async (guard: unknown): Promise<boolean | string> => {
    const r = await TestBed.runInInjectionContext(() =>
      (guard as (r: Route, s: unknown) => unknown)({} as Route, {}),
    );
    return r instanceof UrlTree ? router.serializeUrl(r) : (r as boolean);
  };

  beforeEach(() => {
    fake = new FakeAuth();
    TestBed.configureTestingModule({
      providers: [provideRouter([]), { provide: AuthService, useValue: fake }],
    });
    router = TestBed.inject(Router);
  });

  it('authGuard: signed out → /login, or /setup on a fresh install', async () => {
    expect(await run(authGuard)).toBe('/login');
    fake.needSetup = true;
    expect(await run(authGuard)).toBe('/setup');
  });

  it('authGuard: signed in passes', async () => {
    fake.user.set(mk('viewer'));
    expect(await run(authGuard)).toBe(true);
  });

  it('loginGuard: fresh install redirects to setup; signed-in goes home', async () => {
    fake.needSetup = true;
    expect(await run(loginGuard)).toBe('/setup');
    fake.needSetup = false;
    expect(await run(loginGuard)).toBe(true);
    fake.user.set(mk('editor'));
    expect(await run(loginGuard)).toBe('/');
  });

  it('setupGuard: closed once an admin exists', async () => {
    fake.needSetup = false;
    expect(await run(setupGuard)).toBe('/login');
    fake.needSetup = true;
    expect(await run(setupGuard)).toBe(true);
  });

  it('adminGuard: only admins', async () => {
    fake.user.set(mk('editor'));
    expect(await run(adminGuard)).toBe('/');
    fake.user.set(mk('admin'));
    expect(await run(adminGuard)).toBe(true);
  });
});
