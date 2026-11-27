import { Component, OnInit, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { errorMessage } from '../../core/api';
import { AuthService } from '../../core/auth.service';
import { MediaApi } from '../../core/media-api.service';
import { Project } from '../../core/models';

@Component({
  selector: 'app-projects-page',
  imports: [ReactiveFormsModule, RouterLink],
  template: `
    <h1 class="text-2xl font-semibold tracking-tight">Projects</h1>
    <p class="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
      A project holds the videos for one show, channel or topic.
    </p>

    @if (canCreate()) {
      <form
        [formGroup]="form"
        (ngSubmit)="create()"
        class="card mt-6 flex flex-wrap items-end gap-3"
        novalidate
      >
        <div class="min-w-64 flex-1">
          <label class="label" for="pname">New project name</label>
          <input
            id="pname"
            class="input"
            formControlName="name"
            maxlength="200"
            autocomplete="off"
          />
        </div>
        <button class="btn btn-primary" type="submit" [disabled]="busy() || form.invalid">
          Create project
        </button>
        @if (error()) {
          <p class="alert-error w-full" role="alert">{{ error() }}</p>
        }
      </form>
    }

    <section class="mt-6" aria-label="Your projects">
      @if (loading()) {
        <p class="text-sm text-zinc-600 dark:text-zinc-400">Loading…</p>
      } @else if (loadError()) {
        <p class="alert-error" role="alert">{{ loadError() }}</p>
      } @else if (projects().length === 0) {
        <div class="card">
          <h2 class="text-base font-semibold">No projects yet</h2>
          <p class="mt-1 text-sm text-zinc-600 dark:text-zinc-400">
            @if (canCreate()) {
              Create your first project above, then add a video to it.
            } @else {
              You have not been added to any project. Ask an editor or admin to add you.
            }
          </p>
        </div>
      } @else {
        <ul class="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          @for (p of projects(); track p.id) {
            <li class="card p-0">
              <a
                [routerLink]="['/projects', p.id]"
                class="block rounded-lg p-5 hover:bg-zinc-50 dark:hover:bg-zinc-800"
              >
                <span class="text-base font-semibold">{{ p.name }}</span>
                <span class="mt-1 block text-sm text-zinc-600 dark:text-zinc-400">
                  {{ p.source_count }} {{ p.source_count === 1 ? 'video' : 'videos' }} ·
                  {{ p.my_role }}
                </span>
              </a>
            </li>
          }
        </ul>
      }
    </section>
  `,
})
export class ProjectsPage implements OnInit {
  private readonly api = inject(MediaApi);
  private readonly router = inject(Router);
  private readonly fb = inject(FormBuilder).nonNullable;
  private readonly auth = inject(AuthService);

  readonly projects = signal<Project[]>([]);
  readonly loading = signal(true);
  readonly loadError = signal('');
  readonly busy = signal(false);
  readonly error = signal('');
  readonly form = this.fb.group({ name: ['', [Validators.required, Validators.maxLength(200)]] });

  canCreate(): boolean {
    return this.auth.user()?.role !== 'viewer';
  }

  async ngOnInit(): Promise<void> {
    try {
      this.projects.set(await this.api.listProjects());
    } catch (e) {
      this.loadError.set(errorMessage(e));
    } finally {
      this.loading.set(false);
    }
  }

  async create(): Promise<void> {
    if (this.form.invalid) return;
    this.busy.set(true);
    this.error.set('');
    try {
      const p = await this.api.createProject(this.form.getRawValue().name.trim(), null);
      await this.router.navigate(['/projects', p.id]);
    } catch (e) {
      this.error.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }
}
