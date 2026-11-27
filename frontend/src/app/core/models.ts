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
  has_video: boolean;
  created_at: string;
  thumbnail_url: string | null;
}

export interface SourceDetail extends Source {
  proxy_url: string | null;
  waveform_url: string | null;
  media_hash: string | null;
  job: Job | null;
  transcription_job: Job | null;
  transcript: TranscriptSummary | null;
  highlights_job: Job | null;
  highlights: { run_id: string; created_at: string; count: number } | null;
  scenes_url: string | null;
}

export interface TranscriptSummary {
  version: number;
  kind: 'asr' | 'edit' | 'restore';
  language: string | null;
  model: string;
  segment_count: number;
  word_count: number;
}

export interface Features {
  ingest_enabled: boolean;
  ingest_available: boolean;
  ingest_daily_limit: number;
  max_upload_bytes: number;
  allowed_extensions: string[];
}

export interface DashboardTotals {
  clips: number;
  clips_rendered: number;
  renders_failed_qc: number;
  jobs_failed: number;
  recent_clips: DashboardClip[];
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
  job_kind?: string;
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
  extract_audio: 'Extract the audio',
  asr_prep: 'Clean up the audio for recognition',
  transcribe: 'Recognise the speech',
  detect_scenes: 'Find scene changes',
  clip_subtitles: 'Write the captions',
  clip_loudness: 'Measure the loudness',
  clip_render: 'Render the clip',
  clip_thumbnail: 'Make a thumbnail',
  clip_qc: 'Check the result',
  metadata: 'Write the titles and descriptions',
  highlights: 'Ask the model for highlights',
};

export interface TranscriptMeta {
  id: string;
  source_id: string;
  version: number;
  kind: 'asr' | 'edit' | 'restore';
  is_current: boolean;
  engine: string;
  model: string;
  language: string | null;
  language_probability: number | null;
  requested_language: string | null;
  duration_s: number;
  segment_count: number;
  word_count: number;
  based_on_version: number | null;
  created_at: string;
}

export interface TranscriptWord {
  id: string;
  idx: number;
  start_ms: number;
  end_ms: number;
  text: string;
  probability: number | null;
}

export interface TranscriptSegment {
  id: string;
  idx: number;
  start_ms: number;
  end_ms: number;
  text: string;
  edited: boolean;
  words: TranscriptWord[];
}

export interface TranscriptPage {
  transcript: TranscriptMeta;
  offset: number;
  segments: TranscriptSegment[];
}

export interface TranscribeResult {
  reused: boolean;
  reason: string;
  transcript: TranscriptMeta | null;
  job: Job | null;
}

export interface AsrModel {
  name: string;
  mb: number;
  multilingual: boolean;
  note: string;
  downloaded: boolean;
}

export interface AsrStatus {
  available: boolean;
  device: string | null;
  compute_type: string | null;
  default_model: string;
  default_language: string | null;
  models: AsrModel[];
  languages: string[];
}

export interface ModelProgress {
  status: string;
  percent: number;
  done: boolean;
  error: string | null;
  idle?: boolean;
}

export const HIGHLIGHT_CATEGORIES: Record<string, string> = {
  conversation: 'Interesting conversation',
  humour: 'Humour',
  emotion: 'Emotion',
  education: 'Education',
  opinion: 'Strong opinion',
  surprising_fact: 'Surprising fact',
  controversy: 'Controversy',
  tutorial: 'Tutorial',
  qa: 'Question and answer',
  story: 'Story',
  other: 'Other',
};

export interface ScorePart {
  value: number;
  weight: number;
}

export interface HighlightCandidate {
  id: string;
  rank: number;
  start_ms: number;
  end_ms: number;
  duration_ms: number;
  text: string;
  category: string;
  hook: string;
  title: string;
  explanation: string;
  llm_score: number;
  standalone_score: number;
  confidence: number;
  final_score: number;
  score_parts: Record<string, ScorePart | string[]>;
  provider_name: string;
  model_name: string;
  status: 'new' | 'accepted' | 'rejected';
}

export interface HighlightRunSummary {
  id: string;
  source_id: string;
  transcript_id: string;
  created_at: string;
  candidate_count: number;
  request: { prompt?: string; categories?: string[]; min_seconds?: number; max_seconds?: number };
  stats: {
    windows: number;
    windows_failed: { window: number; code: string; message: string }[];
    raw_candidates: number;
    dropped: Record<string, number>;
    kept: number;
    models: Record<string, number>;
    scene_cuts: number;
    cost_est: number | null;
    tokens_in: number;
    tokens_out: number;
  };
}

export interface HighlightRun {
  run: HighlightRunSummary;
  candidates: HighlightCandidate[];
  score_note: string;
}

export interface FindHighlightsResult {
  reused: boolean;
  run: HighlightRunSummary | null;
  job: Job | null;
}

export type Aspect = '9:16' | '1:1' | '16:9';
export type Platform = 'generic' | 'youtube' | 'instagram';

export interface CropKeyframe {
  t_ms: number;
  cx: number;
  cy: number;
}

export interface CropSpec {
  mode: 'fill' | 'fit';
  zoom: number;
  keyframes: CropKeyframe[];
}

export interface ClipVersion {
  id: string;
  version: number;
  start_ms: number;
  end_ms: number;
  aspect: Aspect;
  crop: CropSpec;
  background: { type: 'blur' | 'solid'; color: string };
  audio: { gain_db: number; mute: boolean };
  caption_template_id: string | null;
  caption_overrides: Record<string, unknown>;
  transcript_id: string | null;
  created_at: string;
}

export interface QcCheck {
  check: string;
  severity: 'pass' | 'warn' | 'fail';
  message: string;
}

export interface ClipRender {
  id: string;
  kind: 'preview' | 'final';
  version: number;
  state: 'pending' | 'running' | 'done' | 'failed' | 'cancelled';
  error: string | null;
  created_at: string;
  width: number | null;
  height: number | null;
  duration_ms: number | null;
  size_bytes: number | null;
  loudness_lufs: number | null;
  qc_status: 'pass' | 'warn' | 'fail' | null;
  qc_report: { status: string; checks: QcCheck[] } | null;
  play_url: string | null;
  download_url: string | null;
  thumbnail_url: string | null;
  job: Job | null;
}

export interface Clip {
  id: string;
  project_id: string;
  source_id: string;
  candidate_id: string | null;
  title: string;
  current_version: number;
  version: ClipVersion;
  style: Record<string, unknown>;
  template_name: string | null;
  renders: ClipRender[];
  created_at: string;
}

export interface ClipSummary {
  id: string;
  source_id: string;
  title: string;
  current_version: number;
  start_ms: number;
  end_ms: number;
  aspect: Aspect;
  created_at: string;
  candidate_id: string | null;
  best_render_state: string | null;
  thumbnail_url: string | null;
}

export interface CaptionTemplate {
  id: string;
  name: string;
  is_builtin: boolean;
  style: Record<string, unknown>;
}

export interface CaptionCue {
  start_ms: number;
  end_ms: number;
  lines: { text: string; start_ms: number; end_ms: number }[][];
}

export interface PreviewStyle {
  style: Record<string, unknown>;
  css: {
    container: Record<string, string>;
    text: Record<string, string>;
    active_color: string;
    keyword_color: string;
    animation: 'none' | 'highlight' | 'karaoke' | 'pop';
    keywords: string[];
    font_px: number;
  };
  cues: CaptionCue[];
  output_width: number;
  output_height: number;
  fonts: string[];
}

export interface ClipWord {
  text: string;
  start_ms: number;
  end_ms: number;
  inside: boolean;
}

export interface MetadataFields {
  title: string;
  hook: string;
  description: string;
  short_description: string;
  hashtags: string[];
  tags: string[];
  thumbnail_text: string;
  category: string;
  audience: string;
}

export interface PlatformMetadata extends MetadataFields {
  platform: Platform;
  edited: boolean;
  generated_by: string | null;
  last_error: string | null;
  errors: string[];
  warnings: string[];
}

export interface ClipMetadata {
  platforms: Record<Platform, PlatformMetadata>;
  limits: Record<string, Record<string, number>>;
  soft_hashtag_limits: Record<string, number>;
  job: Job | null;
}

export interface DashboardClip {
  id: string;
  title: string;
  source_id: string;
  updated_at: string;
}
