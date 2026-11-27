import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import {
  AsrStatus,
  ModelProgress,
  TranscribeResult,
  TranscriptMeta,
  TranscriptPage,
} from './models';

const API = '/api/v1';

@Injectable({ providedIn: 'root' })
export class TranscriptApi {
  private readonly http = inject(HttpClient);

  asrStatus(): Promise<AsrStatus> {
    return firstValueFrom(this.http.get<AsrStatus>(`${API}/asr/status`));
  }

  saveAsrSettings(model: string, language: string | null): Promise<unknown> {
    return firstValueFrom(this.http.put(`${API}/asr/settings`, { model, language }));
  }

  downloadModel(name: string): Promise<unknown> {
    return firstValueFrom(
      this.http.post(`${API}/asr/models/${encodeURIComponent(name)}/download`, null),
    );
  }

  modelProgress(name: string): Promise<ModelProgress> {
    return firstValueFrom(
      this.http.get<ModelProgress>(`${API}/asr/models/${encodeURIComponent(name)}/progress`),
    );
  }

  transcribe(
    sourceId: string,
    body: { language: string | null; model: string | null; force: boolean },
  ): Promise<TranscribeResult> {
    return firstValueFrom(
      this.http.post<TranscribeResult>(`${API}/sources/${sourceId}/transcribe`, body),
    );
  }

  get(sourceId: string, offset = 0, version?: number): Promise<TranscriptPage> {
    const params: Record<string, string> = { offset: String(offset), limit: '200' };
    if (version !== undefined) params['version'] = String(version);
    return firstValueFrom(
      this.http.get<TranscriptPage>(`${API}/sources/${sourceId}/transcript`, { params }),
    );
  }

  versions(sourceId: string): Promise<TranscriptMeta[]> {
    return firstValueFrom(
      this.http.get<TranscriptMeta[]>(`${API}/sources/${sourceId}/transcript/versions`),
    );
  }

  edit(
    sourceId: string,
    baseVersion: number,
    edits: { segment_idx: number; text: string }[],
  ): Promise<TranscriptMeta> {
    return firstValueFrom(
      this.http.patch<TranscriptMeta>(`${API}/sources/${sourceId}/transcript`, {
        base_version: baseVersion,
        edits,
      }),
    );
  }

  restore(sourceId: string, version: number): Promise<TranscriptMeta> {
    return firstValueFrom(
      this.http.post<TranscriptMeta>(`${API}/sources/${sourceId}/transcript/restore`, { version }),
    );
  }

  /** Exports need the sign-in header, so they are fetched as a file and saved from memory. */
  async exportFile(sourceId: string, format: 'txt' | 'srt' | 'vtt'): Promise<void> {
    const blob = await firstValueFrom(
      this.http.get(`${API}/sources/${sourceId}/transcript/export`, {
        params: { format },
        responseType: 'blob',
      }),
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `transcript.${format}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }
}
