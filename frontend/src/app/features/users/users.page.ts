import { DatePipe } from '@angular/common';
import { Component, OnInit, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';

import { Role, User, errorMessage } from '../../core/api';
import { AuthService } from '../../core/auth.service';
import { Invite, UsersService } from './users.service';

@Component({
  selector: 'app-users-page',
  imports: [ReactiveFormsModule, DatePipe],
  template: `
    <h1 class="text-2xl font-semibold tracking-tight">Users</h1>
    <p class="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
      Registration is closed. Invite people and send them the link yourself.
    </p>

    <section class="card mt-6" aria-labelledby="inv">
      <h2 id="inv" class="text-base font-semibold">Invite someone</h2>
      <form
        [formGroup]="inviteForm"
        (ngSubmit)="invite()"
        class="mt-3 flex flex-wrap items-end gap-3"
        novalidate
      >
        <div class="min-w-64 flex-1">
          <label class="label" for="inv-email">Email</label>
          <input id="inv-email" class="input" type="email" formControlName="email" />
        </div>
        <div>
          <label class="label" for="inv-role">Role</label>
          <select id="inv-role" class="input" formControlName="role">
            <option value="admin">Admin</option>
            <option value="editor">Editor</option>
            <option value="viewer">Viewer</option>
          </select>
        </div>
        <button class="btn btn-primary" type="submit" [disabled]="inviting() || inviteForm.invalid">
          Create invitation
        </button>
      </form>
      @if (inviteError()) {
        <p class="alert-error mt-3" role="alert">{{ inviteError() }}</p>
      }
      @if (lastInvite(); as inv) {
        <div
          class="mt-4 rounded-md border border-zinc-200 p-3 text-sm dark:border-zinc-800"
          role="status"
        >
          <p>
            Invitation for <strong>{{ inv.email }}</strong> ({{ inv.role }}), valid until
            {{ inv.expires_at | date: 'medium' }}. This link is shown only once.
          </p>
          <div class="mt-2 flex gap-2">
            <input
              class="input font-mono"
              readonly
              [value]="inviteLink(inv)"
              aria-label="Invitation link"
            />
            <button type="button" class="btn btn-secondary" (click)="copy(inv)">
              {{ copied() ? 'Copied' : 'Copy' }}
            </button>
          </div>
        </div>
      }
    </section>

    <section class="card mt-4 overflow-x-auto p-0" aria-labelledby="all">
      <h2 id="all" class="sr-only">All users</h2>
      @if (loading()) {
        <p class="p-6 text-sm text-zinc-600 dark:text-zinc-400">Loading…</p>
      } @else if (loadError()) {
        <p class="alert-error m-4" role="alert">{{ loadError() }}</p>
      } @else {
        @if (actionError()) {
          <p class="alert-error m-4" role="alert">{{ actionError() }}</p>
        }
        <table class="w-full text-left text-sm">
          <thead
            class="border-b border-zinc-200 text-zinc-600 dark:border-zinc-800 dark:text-zinc-400"
          >
            <tr>
              <th scope="col" class="px-4 py-2 font-medium">Name</th>
              <th scope="col" class="px-4 py-2 font-medium">Role</th>
              <th scope="col" class="px-4 py-2 font-medium">Last sign-in</th>
              <th scope="col" class="px-4 py-2 font-medium">
                <span class="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody class="divide-y divide-zinc-200 dark:divide-zinc-800">
            @for (u of users(); track u.id) {
              <tr [class.opacity-60]="!u.is_active">
                <td class="px-4 py-2">
                  <div class="font-medium">{{ u.display_name }}</div>
                  <div class="text-zinc-600 dark:text-zinc-400">{{ u.email }}</div>
                </td>
                <td class="px-4 py-2">
                  <label class="sr-only" [for]="'role-' + u.id"
                    >Role for {{ u.display_name }}</label
                  >
                  <select
                    [id]="'role-' + u.id"
                    class="input w-auto py-1"
                    [value]="u.role"
                    [disabled]="u.id === me()?.id"
                    (change)="setRole(u, $any($event.target).value)"
                  >
                    <option value="admin">Admin</option>
                    <option value="editor">Editor</option>
                    <option value="viewer">Viewer</option>
                  </select>
                </td>
                <td class="px-4 py-2 text-zinc-600 dark:text-zinc-400">
                  {{ u.last_login_at ? (u.last_login_at | date: 'medium') : 'Never' }}
                </td>
                <td class="px-4 py-2 text-right">
                  @if (u.id !== me()?.id) {
                    @if (!u.is_active) {
                      <span class="badge mr-2">Deactivated</span>
                      <button
                        type="button"
                        class="btn btn-secondary py-1"
                        (click)="setActive(u, true)"
                      >
                        Reactivate
                      </button>
                    } @else if (confirming() === u.id) {
                      <button
                        type="button"
                        class="btn btn-danger py-1"
                        (click)="setActive(u, false)"
                      >
                        Confirm deactivate
                      </button>
                      <button
                        type="button"
                        class="btn btn-secondary ml-1 py-1"
                        (click)="confirming.set(null)"
                      >
                        Cancel
                      </button>
                    } @else {
                      <button
                        type="button"
                        class="btn btn-secondary py-1"
                        (click)="confirming.set(u.id)"
                      >
                        Deactivate
                      </button>
                    }
                  } @else {
                    <span class="badge">You</span>
                  }
                </td>
              </tr>
            }
          </tbody>
        </table>
      }
    </section>
  `,
})
export class UsersPage implements OnInit {
  private readonly api = inject(UsersService);
  private readonly fb = inject(FormBuilder).nonNullable;
  readonly me = inject(AuthService).user;

  readonly users = signal<User[]>([]);
  readonly loading = signal(true);
  readonly loadError = signal('');
  readonly actionError = signal('');
  readonly inviteError = signal('');
  readonly inviting = signal(false);
  readonly lastInvite = signal<Invite | null>(null);
  readonly copied = signal(false);
  readonly confirming = signal<string | null>(null);

  readonly inviteForm = this.fb.group({
    email: ['', [Validators.required, Validators.email]],
    role: ['editor' as Role],
  });

  ngOnInit(): void {
    void this.load();
  }

  async load(): Promise<void> {
    this.loading.set(true);
    this.loadError.set('');
    try {
      this.users.set(await this.api.listAll());
    } catch (e) {
      this.loadError.set(errorMessage(e));
    } finally {
      this.loading.set(false);
    }
  }

  async invite(): Promise<void> {
    if (this.inviteForm.invalid) return;
    this.inviting.set(true);
    this.inviteError.set('');
    this.copied.set(false);
    try {
      const { email, role } = this.inviteForm.getRawValue();
      this.lastInvite.set(await this.api.invite(email, role));
      this.inviteForm.controls.email.reset('');
    } catch (e) {
      this.inviteError.set(errorMessage(e));
    } finally {
      this.inviting.set(false);
    }
  }

  inviteLink(inv: Invite): string {
    return `${window.location.origin}/accept-invite?token=${encodeURIComponent(inv.invite_token)}`;
  }

  async copy(inv: Invite): Promise<void> {
    try {
      await navigator.clipboard.writeText(this.inviteLink(inv));
      this.copied.set(true);
    } catch {
      this.inviteError.set('Copy failed. Select the link and copy it manually.');
    }
  }

  async setRole(u: User, role: Role): Promise<void> {
    await this.mutate(u, { role });
  }

  async setActive(u: User, is_active: boolean): Promise<void> {
    this.confirming.set(null);
    await this.mutate(u, { is_active });
  }

  private async mutate(u: User, patch: { role?: Role; is_active?: boolean }): Promise<void> {
    this.actionError.set('');
    try {
      const updated = await this.api.update(u.id, patch);
      this.users.update((list) => list.map((x) => (x.id === updated.id ? updated : x)));
    } catch (e) {
      this.actionError.set(errorMessage(e));
      await this.load(); // resync the UI with the server (e.g. a reverted select)
    }
  }
}
