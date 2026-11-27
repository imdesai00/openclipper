import { Component, inject, signal } from '@angular/core';
import { AbstractControl, FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';

import { errorMessage } from '../../core/api';
import { AuthService } from '../../core/auth.service';
import { AuthCardComponent } from './auth-card.component';

function matches(a: string, b: string) {
  return (group: AbstractControl) =>
    group.get(a)?.value === group.get(b)?.value ? null : { mismatch: true };
}

@Component({
  selector: 'app-setup-page',
  imports: [ReactiveFormsModule, AuthCardComponent],
  template: `
    <app-auth-card
      heading="Create the admin account"
      subtitle="This is a fresh install. The first account becomes the administrator; everyone else joins by invitation."
    >
      <form [formGroup]="form" (ngSubmit)="submit()" class="space-y-4" novalidate>
        @if (error()) {
          <p class="alert-error" role="alert">{{ error() }}</p>
        }
        <div>
          <label class="label" for="name">Your name</label>
          <input
            id="name"
            class="input"
            formControlName="display_name"
            autocomplete="name"
            [attr.aria-invalid]="invalid('display_name')"
          />
          @if (invalid('display_name')) {
            <p class="field-error">Enter your name.</p>
          }
        </div>
        <div>
          <label class="label" for="email">Email</label>
          <input
            id="email"
            class="input"
            type="email"
            formControlName="email"
            autocomplete="email"
            [attr.aria-invalid]="invalid('email')"
          />
          @if (invalid('email')) {
            <p class="field-error">Enter a valid email address.</p>
          }
        </div>
        <div>
          <label class="label" for="password">Password</label>
          <input
            id="password"
            class="input"
            type="password"
            formControlName="password"
            autocomplete="new-password"
            aria-describedby="pw-help"
            [attr.aria-invalid]="invalid('password')"
          />
          <p id="pw-help" class="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
            At least 12 characters. A passphrase works well.
          </p>
          @if (invalid('password')) {
            <p class="field-error">Password must be 12 to 128 characters.</p>
          }
        </div>
        <div>
          <label class="label" for="confirm">Confirm password</label>
          <input
            id="confirm"
            class="input"
            type="password"
            formControlName="confirm"
            autocomplete="new-password"
          />
          @if (submitted() && form.hasError('mismatch')) {
            <p class="field-error">Passwords do not match.</p>
          }
        </div>
        <button class="btn btn-primary w-full" type="submit" [disabled]="busy()">
          {{ busy() ? 'Creating…' : 'Create admin account' }}
        </button>
      </form>
    </app-auth-card>
  `,
})
export class SetupPage {
  private readonly fb = inject(FormBuilder).nonNullable;
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  readonly busy = signal(false);
  readonly submitted = signal(false);
  readonly error = signal('');

  readonly form = this.fb.group(
    {
      display_name: ['', [Validators.required, Validators.maxLength(200)]],
      email: ['', [Validators.required, Validators.email]],
      password: ['', [Validators.required, Validators.minLength(12), Validators.maxLength(128)]],
      confirm: ['', Validators.required],
    },
    { validators: matches('password', 'confirm') },
  );

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
      const { display_name, email, password } = this.form.getRawValue();
      await this.auth.setupAdmin({ display_name, email, password });
      await this.router.navigateByUrl('/');
    } catch (e) {
      this.error.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }
}
