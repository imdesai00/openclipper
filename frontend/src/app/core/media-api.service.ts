import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { DashboardTotals, Features, Job, Page, Project, Source, SourceDetail } from './models';

const API = '/api/v1';

@Injectable({ providedIn: 'root' })
export class MediaApi {
  private readonly http = inject(HttpClient);

  dashboard(): Promise<DashboardTotals> {
    return firstValueFrom(this.http.get<DashboardTotals>(`${API}/dashboard`));
  }

  features(): Promise<Features> {
    return firstValueFrom(this.http.get<Features>(`${API}/features`));
  }

  setIngest(enabled: boolean, daily_limit: number): Promise<Features> {
    return firstValueFrom(
      this.http.put<Features>(`${API}/settings/ingest`, { enabled, daily_limit }),
    );
  }

  async listProjects(): Promise<Project[]> {
    const out: Project[] = [];
    let cursor: string | null = null;
    do {
      const params: Record<string, string> = { limit: '100' };
      if (cursor) params['cursor'] = cursor;
      const page: Page<Project> = await firstValueFrom(
        this.http.get<Page<Project>>(`${API}/projects`, { params }),
      );
      out.push(...page.items);
      cursor = page.next_cursor;
    } while (cursor);
    return out;
  }

  createProject(name: string, description: string | null): Promise<Project> {
    return firstValueFrom(this.http.post<Project>(`${API}/projects`, { name, description }));
  }

  getProject(id: string): Promise<Project> {
    return firstValueFrom(this.http.get<Project>(`${API}/projects/${id}`));
  }

  deleteProject(id: string, confirmName: string): Promise<void> {
    return firstValueFrom(
      this.http.request<void>('DELETE', `${API}/projects/${id}`, {
        body: { confirm_name: confirmName },
      }),
    );
  }

  async listSources(projectId: string): Promise<Source[]> {
    const out: Source[] = [];
    let cursor: string | null = null;
    do {
      const params: Record<string, string> = { limit: '100' };
      if (cursor) params['cursor'] = cursor;
      const page: Page<Source> = await firstValueFrom(
        this.http.get<Page<Source>>(`${API}/projects/${projectId}/sources`, { params }),
      );
      out.push(...page.items);
      cursor = page.next_cursor;
    } while (cursor);
    return out;
  }

  addLink(projectId: string, url: string, attested: boolean): Promise<Source> {
    return firstValueFrom(
      this.http.post<Source>(`${API}/projects/${projectId}/sources/link`, { url, attested }),
    );
  }

  getSource(id: string): Promise<SourceDetail> {
    return firstValueFrom(this.http.get<SourceDetail>(`${API}/sources/${id}`));
  }

  deleteSource(id: string): Promise<void> {
    return firstValueFrom(this.http.delete<void>(`${API}/sources/${id}`));
  }

  reprocess(id: string): Promise<Job> {
    return firstValueFrom(this.http.post<Job>(`${API}/sources/${id}/reprocess`, null));
  }

  retryJob(id: string): Promise<Job> {
    return firstValueFrom(this.http.post<Job>(`${API}/jobs/${id}/retry`, null));
  }

  cancelJob(id: string): Promise<Job> {
    return firstValueFrom(this.http.post<Job>(`${API}/jobs/${id}/cancel`, null));
  }
}
