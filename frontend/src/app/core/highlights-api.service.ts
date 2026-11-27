import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import { FindHighlightsResult, HighlightCandidate, HighlightRun } from './models';

const API = '/api/v1';

export interface FindHighlightsBody {
  prompt: string;
  categories: string[];
  max_candidates: number;
  min_seconds: number;
  max_seconds: number;
  force?: boolean;
}

@Injectable({ providedIn: 'root' })
export class HighlightsApi {
  private readonly http = inject(HttpClient);

  find(sourceId: string, body: FindHighlightsBody): Promise<FindHighlightsResult> {
    return firstValueFrom(
      this.http.post<FindHighlightsResult>(`${API}/sources/${sourceId}/highlights`, body),
    );
  }

  run(sourceId: string, runId: string): Promise<HighlightRun> {
    return firstValueFrom(
      this.http.get<HighlightRun>(`${API}/sources/${sourceId}/highlights/${runId}`),
    );
  }

  review(id: string, status: 'new' | 'accepted' | 'rejected'): Promise<HighlightCandidate> {
    return firstValueFrom(
      this.http.patch<HighlightCandidate>(`${API}/highlights/${id}`, { status }),
    );
  }
}
