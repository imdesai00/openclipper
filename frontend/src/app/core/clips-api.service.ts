import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';

import {
  Aspect,
  CaptionTemplate,
  Clip,
  ClipMetadata,
  ClipRender,
  ClipSummary,
  ClipWord,
  CropSpec,
  PlatformMetadata,
  Platform,
  PreviewStyle,
} from './models';

const API = '/api/v1';

export interface VersionBody {
  base_version: number;
  start_ms?: number;
  end_ms?: number;
  aspect?: Aspect;
  crop?: CropSpec;
  background?: { type: 'blur' | 'solid'; color: string };
  audio?: { gain_db: number; mute: boolean };
  caption_template_id?: string | null;
  caption_overrides?: Record<string, unknown>;
  title?: string;
  refresh_transcript?: boolean;
}

export interface PreviewDraft {
  width: number;
  height: number;
  start_ms?: number;
  end_ms?: number;
  caption_template_id?: string | null;
  caption_overrides?: Record<string, unknown>;
}

@Injectable({ providedIn: 'root' })
export class ClipsApi {
  private readonly http = inject(HttpClient);

  create(sourceId: string, start_ms: number, end_ms: number, title?: string): Promise<Clip> {
    return firstValueFrom(
      this.http.post<Clip>(`${API}/sources/${sourceId}/clips`, { start_ms, end_ms, title }),
    );
  }

  fromHighlight(candidateId: string): Promise<Clip> {
    return firstValueFrom(this.http.post<Clip>(`${API}/highlights/${candidateId}/clip`, null));
  }

  list(sourceId: string): Promise<ClipSummary[]> {
    return firstValueFrom(this.http.get<ClipSummary[]>(`${API}/sources/${sourceId}/clips`));
  }

  get(id: string): Promise<Clip> {
    return firstValueFrom(this.http.get<Clip>(`${API}/clips/${id}`));
  }

  remove(id: string): Promise<void> {
    return firstValueFrom(this.http.delete<void>(`${API}/clips/${id}`));
  }

  saveVersion(id: string, body: VersionBody): Promise<Clip> {
    return firstValueFrom(this.http.post<Clip>(`${API}/clips/${id}/versions`, body));
  }

  render(
    id: string,
    kind: 'preview' | 'final',
    version?: number,
    force = false,
  ): Promise<{ reused: boolean; render: ClipRender }> {
    return firstValueFrom(
      this.http.post<{ reused: boolean; render: ClipRender }>(`${API}/clips/${id}/render`, {
        kind,
        version,
        force,
      }),
    );
  }

  words(id: string): Promise<ClipWord[]> {
    return firstValueFrom(this.http.get<ClipWord[]>(`${API}/clips/${id}/words`));
  }

  previewStyle(id: string, draft: PreviewDraft): Promise<PreviewStyle> {
    return firstValueFrom(this.http.post<PreviewStyle>(`${API}/clips/${id}/preview-style`, draft));
  }

  templates(): Promise<CaptionTemplate[]> {
    return firstValueFrom(this.http.get<CaptionTemplate[]>(`${API}/templates`));
  }

  async captionsFile(id: string, format: 'srt' | 'vtt'): Promise<void> {
    const blob = await firstValueFrom(
      this.http.get(`${API}/clips/${id}/captions`, { params: { format }, responseType: 'blob' }),
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `captions.${format}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  metadata(id: string): Promise<ClipMetadata> {
    return firstValueFrom(this.http.get<ClipMetadata>(`${API}/clips/${id}/metadata`));
  }

  saveMetadata(
    id: string,
    platform: Platform,
    fields: Partial<PlatformMetadata>,
  ): Promise<PlatformMetadata> {
    return firstValueFrom(
      this.http.put<PlatformMetadata>(`${API}/clips/${id}/metadata/${platform}`, fields),
    );
  }

  generateMetadata(id: string, platforms: Platform[], overwrite: boolean): Promise<unknown> {
    return firstValueFrom(
      this.http.post(`${API}/clips/${id}/metadata/generate`, { platforms, overwrite }),
    );
  }
}
