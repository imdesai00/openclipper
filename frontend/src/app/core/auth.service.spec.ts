import { HttpClient, provideHttpClient, withInterceptors } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { beforeEach, describe, expect, it } from 'vitest';

import { User } from './api';
import { authInterceptor } from './auth.interceptor';
import { AuthService } from './auth.service';

const user: User = {
  id: '1',
  email: 'a@example.com',
  display_name: 'A',
  role: 'admin',
  is_active: true,
  created_at: '2026-01-01T00:00:00Z',
  last_login_at: null,
};
const auth = (token = 't1') => ({ access_token: token, expires_in: 900, user });

describe('AuthService + interceptor', () => {
  let svc: AuthService;
  let http: HttpTestingController;
  let client: HttpClient;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        provideHttpClient(withInterceptors([authInterceptor])),
        provideHttpClientTesting(),
      ],
    });
    svc = TestBed.inject(AuthService);
    http = TestBed.inject(HttpTestingController);
    client = TestBed.inject(HttpClient);
  });

  it('login keeps the token in memory and exposes the user', async () => {
    const p = svc.login('a@example.com', 'pw');
    http.expectOne('/api/v1/auth/login').flush(auth());
    await p;
    expect(svc.accessToken()).toBe('t1');
    expect(svc.user()?.email).toBe('a@example.com');
    expect(svc.isAdmin()).toBe(true);
    expect(JSON.stringify(localStorage)).not.toContain('t1');
  });

  it('bootstrap with no valid cookie leaves the user signed out', async () => {
    const p = svc.bootstrap();
    const req = http.expectOne('/api/v1/auth/refresh');
    expect(req.request.headers.get('X-OpenClipper-Client')).toBe('web');
    req.flush({ code: 'no_session' }, { status: 401, statusText: 'Unauthorized' });
    await p;
    expect(svc.user()).toBeNull();
  });

  it('shares one refresh between concurrent callers', async () => {
    const a = svc.refresh();
    const b = svc.refresh();
    http.expectOne('/api/v1/auth/refresh').flush(auth('t2'));
    expect(await a).toBe(true);
    expect(await b).toBe(true);
  });

  it('adds the bearer token to API calls but not to public ones', async () => {
    const p = svc.login('a@example.com', 'pw');
    http.expectOne('/api/v1/auth/login').flush(auth());
    await p;
    const call = firstValueFrom(client.get('/api/v1/users'));
    const req = http.expectOne('/api/v1/users');
    expect(req.request.headers.get('Authorization')).toBe('Bearer t1');
    req.flush({});
    await call;
    const pub = firstValueFrom(client.get('/api/v1/setup/status'));
    const pubReq = http.expectOne('/api/v1/setup/status');
    expect(pubReq.request.headers.has('Authorization')).toBe(false);
    pubReq.flush({ setup_required: false });
    await pub;
  });

  it('refreshes once and retries when the access token expired', async () => {
    const p = svc.login('a@example.com', 'pw');
    http.expectOne('/api/v1/auth/login').flush(auth('old'));
    await p;

    const call = firstValueFrom(client.get<{ ok: boolean }>('/api/v1/users'));
    http
      .expectOne('/api/v1/users')
      .flush({ code: 'token_expired' }, { status: 401, statusText: 'Unauthorized' });
    // Let the interceptor's refresh start, then answer it.
    await Promise.resolve();
    http.expectOne('/api/v1/auth/refresh').flush(auth('new'));
    await new Promise((r) => setTimeout(r));
    const retry = http.expectOne('/api/v1/users');
    expect(retry.request.headers.get('Authorization')).toBe('Bearer new');
    retry.flush({ ok: true });
    expect(await call).toEqual({ ok: true });
  });

  it('logout clears state even if the request fails', async () => {
    const p = svc.login('a@example.com', 'pw');
    http.expectOne('/api/v1/auth/login').flush(auth());
    await p;
    const out = svc.logout().catch(() => undefined);
    http.expectOne('/api/v1/auth/logout').flush('x', { status: 500, statusText: 'err' });
    await out;
    expect(svc.user()).toBeNull();
    expect(svc.accessToken()).toBeNull();
  });
});
