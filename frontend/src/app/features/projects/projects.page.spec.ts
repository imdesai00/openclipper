import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { AuthService } from '../../core/auth.service';
import { MediaApi } from '../../core/media-api.service';
import { Project } from '../../core/models';
import { ProjectsPage } from './projects.page';

const proj = (id: string, name: string, n = 0): Project => ({
  id,
  name,
  description: null,
  owner_id: 'u',
  created_at: '',
  my_role: 'owner',
  source_count: n,
});

describe('ProjectsPage', () => {
  const api = { listProjects: vi.fn(), createProject: vi.fn() };
  const role = signal<'editor' | 'viewer'>('editor');

  beforeEach(() => {
    api.listProjects.mockReset();
    api.createProject.mockReset();
    role.set('editor');
    TestBed.configureTestingModule({
      providers: [
        provideRouter([]),
        { provide: MediaApi, useValue: api },
        { provide: AuthService, useValue: { user: () => ({ role: role() }) } },
      ],
    });
  });

  async function render() {
    const f = TestBed.createComponent(ProjectsPage);
    f.detectChanges();
    await f.whenStable();
    f.detectChanges();
    return f.nativeElement as HTMLElement;
  }

  it('shows an honest empty state instead of fake numbers', async () => {
    api.listProjects.mockResolvedValue([]);
    const el = await render();
    expect(el.textContent).toContain('No projects yet');
  });

  it('lists projects with counts and pluralises correctly', async () => {
    api.listProjects.mockResolvedValue([proj('1', 'Alpha', 1), proj('2', 'Beta', 3)]);
    const el = await render();
    expect(el.textContent).toContain('1 video ·');
    expect(el.textContent).toContain('3 videos ·');
    expect(el.querySelectorAll('a[href="/projects/2"]')).toHaveLength(1);
  });

  it('hides project creation from viewers', async () => {
    role.set('viewer');
    api.listProjects.mockResolvedValue([]);
    const el = await render();
    expect(el.querySelector('#pname')).toBeNull();
    expect(el.textContent).toContain('Ask an editor or admin');
  });

  it('creates a project and opens it', async () => {
    api.listProjects.mockResolvedValue([]);
    api.createProject.mockResolvedValue(proj('9', 'New'));
    const nav = vi.spyOn(TestBed.inject(Router), 'navigate').mockResolvedValue(true);
    const el = await render();
    const input = el.querySelector<HTMLInputElement>('#pname')!;
    input.value = '  New  ';
    input.dispatchEvent(new Event('input'));
    el.querySelector('form')!.dispatchEvent(new Event('submit'));
    await vi.waitFor(() => expect(nav).toHaveBeenCalledWith(['/projects', '9']));
    expect(api.createProject).toHaveBeenCalledWith('New', null);
  });

  it('shows the server error when loading fails', async () => {
    api.listProjects.mockRejectedValue(new Error('boom'));
    const el = await render();
    expect(el.querySelector('[role=alert]')?.textContent).toContain('Something went wrong');
  });
});
