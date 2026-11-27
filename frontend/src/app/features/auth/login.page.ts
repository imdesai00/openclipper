import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';

import { errorMessage } from '../../core/api';
import { AuthService } from '../../core/auth.service';
import { AuthCardComponent } from './auth-card.component';

@Component({
  selector: 'app-login-page',
  imports: [ReactiveFormsModule, AuthCardComponent],
  template: `
    <app-auth-card heading="Sign in">
      <form [formGroup]="form" (ngSubmit)="submit()" class="space-y-4" novalidate>
        @if (error()) {
          <p class="alert-error" role="alert">{{ error() }}</p>
        }
        <div>
          <label class="label" for="email">Email</label>
          <input
            id="email"
            class="input"
            type="email"
            formControlName="email"
            autocomplete="username"
            [attr.aria-invalid]="invalid('email')"
          />
          @if (invalid('email')) {
            <p class="field-error">Enter your email address.</p>
          }
        </div>
        <div>
          <label class="label" for="password">Password</label>
          <input
            id="password"
            class="input"
            type="password"
            formControlName="password"
            autocomplete="current-password"
            [attr.aria-invalid]="invalid('password')"
          />
          @if (invalid('password')) {
            <p class="field-error">Enter your password.</p>
          }
        </div>
        <button class="btn btn-primary w-full" type="submit" [disabled]="busy()">
          {{ busy() ? 'Signing in…' : 'Sign in' }}
        </button>
        <p class="text-center text-sm text-zinc-600 dark:text-zinc-400">
          Accounts are created by an administrator. Ask for an invitation link.
        </p>
      </form>
    </app-auth-card>
  `,
})
export class LoginPage {
  private readonly fb = inject(FormBuilder).nonNullable;
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  readonly busy = signal(false);
  readonly error = signal('');
  private readonly submitted = signal(false);

  readonly form = this.fb.group({
    email: ['', [Validators.required, Validators.email]],
    password: ['', Validators.required],
  });

  invalid(name: string): boolean {
    const c = this.form.get(name);
    return !!c && c.invalid && (c.touched || this.submitted());
  }

  async submit(): Promise<void> {
    this.submitted.set(true);
    this.error.set('');
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      return;
    }
    this.busy.set(true);
    try {
      const { email, password } = this.form.getRawValue();
      await this.auth.login(email, password);
      await this.router.navigateByUrl('/');
    } catch (e) {
      this.error.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }
}
