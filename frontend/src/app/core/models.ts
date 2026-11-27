export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export type MemberRole = 'owner' | 'editor' | 'viewer';

export interface Project {
  id: string;
  name: string;
  description: string | null;
  owner_id: string;
  created_at: string;
  my_role: MemberRole;
  source_count: number;
}

export interface Stage {
  stage: string;
  position: number;
  status: 'pending' | 'running' | 'succeeded' | 'skipped' | 'failed' | 'cancelled';
  progress: number;
  message: string | null;
  attempts: number;
  error_code: string | null;
  error_message: string | null;
}

export interface Job {
  id: string;
  project_id: string;
  source_id: string | null;
  kind: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  stages: Stage[];
}

export type SourceStatus = 'pending' | 'processing' | 'ready' | 'failed';

export interface Source {
  id: string;
  project_id: string;
  kind: 'upload' | 'direct_url' | 'ytdlp';
  title: string;
  origin_url: string | null;
  original_filename: string | null;
  status: SourceStatus;
  error_code: string | null;
  error_message: string | null;
  size_bytes: number | null;
  duration_s: number | null;
  width: number | null;
  height: number | null;
  container: string | null;
  video_codec: string | null;
  audio_codec: string | null;
  has_audio: boolean | null;
  created_at: string;
  thumbnail_url: string | null;
}

export interface SourceDetail extends Source {
  proxy_url: string | null;
  waveform_url: string | null;
  media_hash: string | null;
  job: Job | null;
}

export interface Features {
  ingest_enabled: boolean;
  ingest_available: boolean;
  ingest_daily_limit: number;
  max_upload_bytes: number;
  allowed_extensions: string[];
}

export interface DashboardTotals {
  projects: number;
  sources: number;
  sources_ready: number;
  sources_failed: number;
  jobs_running: number;
  storage_bytes: number;
}

export interface RealtimeEvent {
  type: 'job.update' | 'job.progress' | 'pong';
  job_id?: string;
  project_id?: string;
  source_id?: string;
  stage?: string;
  stage_status?: Stage['status'];
  progress?: number;
  message?: string | null;
  job_status?: Job['status'];
  source_status?: SourceStatus;
  error_code?: string;
  error_message?: string;
}

export const STAGE_LABELS: Record<string, string> = {
  download: 'Download the video',
  validate: 'Check the file',
  proxy: 'Prepare the editing copy',
  waveform: 'Draw the waveform',
  thumbnail: 'Make a thumbnail',
};
