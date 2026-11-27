import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';

import { errorMessage } from '../../core/api';
import { AuthService } from '../../core/auth.service';
import { AuthCardComponent } from './auth-card.component';

@Component({
  selector: 'app-accept-invite-page',
  imports: [ReactiveFormsModule, AuthCardComponent],
  template: `
    <app-auth-card heading="Accept your invitation" subtitle="Choose a name and password to join.">
      @if (!token) {
        <p class="alert-error" role="alert">
          This invitation link is incomplete. Ask your administrator for a new one.
        </p>
      } @else {
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
            <label class="label" for="password">Password</label>
            <input
              id="password"
              class="input"
              type="password"
              formControlName="password"
              autocomplete="new-password"
              [attr.aria-invalid]="invalid('password')"
            />
            @if (invalid('password')) {
              <p class="field-error">Password must be 12 to 128 characters.</p>
            }
          </div>
          <button class="btn btn-primary w-full" type="submit" [disabled]="busy()">
            {{ busy() ? 'Joining…' : 'Join' }}
          </button>
        </form>
      }
    </app-auth-card>
  `,
})
export class AcceptInvitePage {
  private readonly fb = inject(FormBuilder).nonNullable;
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  readonly token = inject(ActivatedRoute).snapshot.queryParamMap.get('token') ?? '';

  readonly busy = signal(false);
  readonly error = signal('');
  private readonly submitted = signal(false);

  readonly form = this.fb.group({
    display_name: ['', [Validators.required, Validators.maxLength(200)]],
    password: ['', [Validators.required, Validators.minLength(12), Validators.maxLength(128)]],
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
      await this.auth.acceptInvite({ token: this.token, ...this.form.getRawValue() });
      await this.router.navigateByUrl('/');
    } catch (e) {
      this.error.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }
}
