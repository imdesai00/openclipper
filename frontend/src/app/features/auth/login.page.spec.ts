import { HttpErrorResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthService } from '../../core/auth.service';
import { LoginPage } from './login.page';

describe('LoginPage', () => {
  const login = vi.fn();

  beforeEach(() => {
    login.mockReset();
    TestBed.configureTestingModule({
      providers: [provideRouter([]), { provide: AuthService, useValue: { login } }],
    });
  });

  function setup() {
    const fixture = TestBed.createComponent(LoginPage);
    fixture.detectChanges();
    const el: HTMLElement = fixture.nativeElement;
    const type = (id: string, value: string) => {
      const input = el.querySelector<HTMLInputElement>(`#${id}`)!;
      input.value = value;
      input.dispatchEvent(new Event('input'));
    };
    const submit = async () => {
      el.querySelector('form')!.dispatchEvent(new Event('submit'));
      await fixture.whenStable();
      fixture.detectChanges();
    };
    return { el, type, submit };
  }

  it('shows validation errors and does not call the API when empty', async () => {
    const { el, submit } = setup();
    await submit();
    expect(login).not.toHaveBeenCalled();
    expect(el.textContent).toContain('Enter your email address');
    expect(el.textContent).toContain('Enter your password');
  });

  it('shows the server message on bad credentials', async () => {
    login.mockRejectedValue(
      new HttpErrorResponse({
        status: 401,
        error: {
          code: 'invalid_credentials',
          message: 'Invalid email or password',
          details: null,
          request_id: 'r',
        },
      }),
    );
    const { el, type, submit } = setup();
    type('email', 'a@example.com');
    type('password', 'wrong');
    await submit();
    expect(el.querySelector('[role=alert]')?.textContent).toContain('Invalid email or password');
  });

  it('navigates home on success', async () => {
    login.mockResolvedValue(undefined);
    const nav = vi.spyOn(TestBed.inject(Router), 'navigateByUrl').mockResolvedValue(true);
    const { type, submit } = setup();
    type('email', 'a@example.com');
    type('password', 'right');
    await submit();
    expect(login).toHaveBeenCalledWith('a@example.com', 'right');
    expect(nav).toHaveBeenCalledWith('/');
  });

  it('shows a friendly message when the server is unreachable', async () => {
    login.mockRejectedValue(new HttpErrorResponse({ status: 0 }));
    const { el, type, submit } = setup();
    type('email', 'a@example.com');
    type('password', 'x');
    await submit();
    expect(el.querySelector('[role=alert]')?.textContent).toContain('Cannot reach the server');
  });
});
