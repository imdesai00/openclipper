# OpenClipper — Architecture (DRAFT v1, Milestone 0)

> Status: **DRAFT awaiting human approval.** Decisions D-002…D-014 are accepted (see `DECISIONS.md`). Where the SPEC and this file differ, this file plus DECISIONS win.

**Changes vs SPEC:** yt-dlp ingestion is an optional compose profile `ingest`, off by default (D-010). Upload (tus) is the default path.

## 1. System overview

```
                         ┌──────────────┐
  Browser (Angular) ───▶ │  frontend    │ nginx: static app + reverse proxy /api, /ws, /files (tus)
                         └──────┬───────┘
                                │ internal network
     Claude Desktop (stdio) ┐   ▼
     Claude remote (HTTPS) ─┼─▶ mcp ──REST(scoped token)──▶ api (FastAPI)
                            ┘                               │  │   ▲ Redis pub/sub (progress)
                                                            │  ▼   │
                                   postgres ◀──────────── services ── redis (broker + results + events)
                                                            │
             ┌──────────────┬──────────────┬────────────────┼──────────────┬───────────────┐
             ▼              ▼              ▼                ▼              ▼               ▼
        worker-default  worker-media   worker-asr      worker-llm    worker-publish   ingest-worker
        (orchestration) (FFmpeg, scene, (faster-whisper,(Ollama/remote (YT/IG upload)  (yt-dlp, Deno,
                         reframe)       WhisperX opt)  providers)                      PO-token sidecar)
                                                            │
                                                         ollama            beat (Celery scheduler)
                         storage: LocalFilesystem volume | S3-compatible (profile `s3`)
       profile `live` (V2): mediamtx + live-worker (long-running process, not Celery)
```

Key properties:
- **Stateless API**, heavy work in Celery workers on dedicated queues: `default`, `media`, `asr`, `llm`, `publish`, `ingest`.
- **One image per runtime family**, many services: `backend` image runs `api`, `beat`, `worker-default`, `worker-media`, `worker-llm`, `worker-publish` with different commands. `asr` gets its own image (CTranslate2/torch weight). `ingest` gets its own image (yt-dlp + Deno, auto-updated, isolated credentials).
- **Small-hardware mode (D-004)**: a single `worker-all` service consuming every queue, for 4-core/16 GB machines. 

## 2. Backend layout (hexagonal)

```
backend/
  app/
    domain/          # entities, value objects, state machines (pure Python, no I/O)
    ports/           # Protocols: VideoSourceProvider, StorageBackend, TranscriptionEngine,
                     #   DiarizationEngine, LLMProvider, PublishingAdapter, Notifier, Clock
    services/        # application services = the ONLY business logic (used by REST, Celery, MCP)
    pipeline/
      stages/        # one module per stage: validate, ingest, probe, extract_audio, asr_prep,
                     #   transcribe, align, diarize, scenes, highlight_map, highlight_reduce,
                     #   cut, reframe, subtitles, captions_render, loudnorm, thumbnail, qc, export
      runner.py      # checkpointing, idempotency keys, leases, progress events
    adapters/
      storage/       # local_fs.py, s3.py
      sources/       # ytdlp.py, upload_tus.py, direct_url.py, youtube_api.py, (rtmp.py V2), stubs
      asr/           # faster_whisper.py, whisperx.py
      llm/           # ollama.py, openai_compat.py (OpenRouter, Groq, custom), openai.py,
                     #   anthropic.py, gemini.py
      publishing/    # youtube.py, instagram.py, tiktok_stub.py
      notify/        # in_app.py, (email/webhook per decision)
      media/         # ffmpeg.py (arg-list runner w/ timeouts), ffprobe.py, scenedetect.py, faces.py
    api/v1/          # FastAPI routers: thin, call services only
    ws/              # WebSocket hub subscribing to Redis pub/sub
    tasks/           # Celery task wrappers: thin, call services/pipeline only
    db/              # SQLAlchemy models, session, Alembic env
    security/        # argon2id, JWT, refresh rotation, crypto (AES-GCM envelope), SSRF guard
    config.py        # pydantic-settings; refuses to start without APP_ENCRYPTION_KEY
  migrations/
  tests/{unit,integration,external,e2e-fixtures}/
mcp/                 # separate package; REST client + MCP SDK; no DB access
frontend/            # Angular workspace
ingest/              # ingest-worker image (yt-dlp updater + Celery `ingest` queue consumer)
deploy/              # compose files, nginx conf, mediamtx conf
docs/
```

## 3. Job & pipeline model

- `processing_jobs` = one user-visible job (e.g. "analyze source"). `job_stages` = one row per stage with `status`, `params_hash`, `output_asset_id`, `attempts`, `heartbeat_at`, `error_code`, `error_message`.
- Idempotency key: `(source_id, stage, params_hash)`. Runner checks for a `succeeded` stage with matching key and a still-existing output asset → skip.
- Orchestration with Celery chains built by `services/jobs.py`; each stage task re-reads state from DB (no large payloads through the broker).
- Leases: runner takes `asset_leases` on inputs before a stage and releases on completion; retention never deletes leased assets.
- Recovery: beat task moves `running` stages with stale heartbeat back to `queued`.
- Progress: stages publish `{job_id, stage, pct, msg}` to Redis channel `jobs:{user_id}`; API WS hub forwards to authenticated sockets.

## 4. Highlight detection (SPEC §6.E)

1. Window transcript by token budget derived from the model's context length (provider metadata → configured fallback) minus prompt + output reserve; overlap ~15%.
2. Map: per window, prompt with segment IDs (`[s0412] text…`); LLM returns JSON `{candidates:[{start_segment_id,end_segment_id,category,hook,title,score,explanation,standalone,confidence}]}`. JSON-schema/tool mode where supported, else validate + ≤2 repair retries, then visible failure.
3. Server maps IDs → word timestamps; unknown IDs are rejected (prevents invented timestamps).
4. Snap to sentence boundaries; enforce min/max duration + padding.
5. Text always re-derived from stored transcript.
6. Reduce: dedupe overlaps (keep stronger), weighted ranking (weights in `settings`).

## 5. Captions

`caption_templates.style` (JSONB, versioned schema) → `compile_css()` for editor preview and `compile_ass()` for libass render. Golden tests render both and compare layout boxes within tolerance.

## 6. Storage

`StorageBackend` port: `put_stream, get_stream, presign_or_local_url, delete, exists, stat, list_prefix`. Keys: `{project_id}/{kind}/{uuid}.{ext}`. Media served to the browser via short-lived signed URLs (S3) or an authenticated API route with Range support (local FS).
Self-hosted S3 for profile `s3`: SeaweedFS (D-006).

## 7. Auth

Setup wizard → first admin. Invite-only. Argon2id. Access JWT (15 min) in memory; refresh token (rotating, reuse detection, family revocation) in httpOnly SameSite=Strict cookie. API tokens (hashed, scoped `read|write|publish|delete`) for MCP. Authorization checked in services via project membership, not only in routers.
Tenancy: small team, invite-only (D-003).

## 8. Data model (ERD outline)

```
users 1─* user_sessions, api_tokens, audit_logs, notifications
users *─* projects  (project_members: role)
projects 1─* video_sources 1─1 source_permissions
video_sources 1─* media_assets (original, proxy, audio, waveform, clip renders, thumbnails)
video_sources 1─* processing_jobs 1─* job_stages ─0..1 media_assets(output)
video_sources 1─* transcripts(versioned) 1─* transcript_segments 1─* transcript_words ─*1 speakers
transcripts 1─* highlight_candidates ─0..1 clips 1─* clip_versions ─* caption_templates
ai_providers 1─* ai_models;  ai_providers 1─1 encrypted_credentials;  ai_usage_logs → ai_models, jobs
price_table → ai_models
social_accounts(encrypted tokens) 1─* publishing_jobs ─* clip_versions; publishing_history
automation_rules 1─* automation_runs
retention_policies (global | per project);  asset_leases → media_assets, processing_jobs
settings (key/value JSONB, scoped global|project)
```
Full column-level ERD to be produced after decisions are approved.

## 9. API plan

`/api/v1/{auth, setup, dashboard, projects, sources, uploads(tus), jobs, transcripts, highlights, clips, editor, captions, templates, providers, models, usage, publishing, social-accounts, automation, storage, retention, settings, audit, health}`; WS `/ws/jobs`, `/ws/live`. Error envelope `{code, message, details, request_id}`, cursor pagination. Endpoint-level table to follow in this doc after approval.

## 10. Deployment

`docker-compose.yml` (core, CPU) + `docker-compose.gpu.yml` overlay + profiles `live`, `s3`, `potoken`. Only `frontend` (and optionally `mcp`) publish ports. Multi-stage, non-root images, health checks, `depends_on: service_healthy`, resource limits, named volumes for DB, storage, model caches.

## 11. Top risks (ranked)

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| 1 | Local LLM (CPU, small model) produces poor highlights | Kills differentiator #1 | Benchmark spike in M0/M5 before committing; strong heuristics + reduce step; clear "use bigger model" guidance |
| 2 | yt-dlp breakage / IP blocking (esp. datacenter IPs, CI) | Primary UX fails | Upload path always works; ingest isolated; auto-update; tests SKIPPED with reason when blocked |
| 3 | Downloader vs Google's API audit: policies list downloading as unauthorized (confirmed) | Public YouTube uploads may stay locked private | Optional `ingest` profile, off by default (D-010). Reduces the risk, does not remove it |
| 4 | Scope: 11 milestones of large surface area | Never ships | Strict MVP gate; one milestone per session; cut list in DECISIONS |
| 5 | Editor (timeline, waveform, crop keyframes) complexity in Angular | Slow M6 | Build server-rendered preview first; keep timeline custom-but-minimal |
| 6 | RAM on 16 GB CPU box (Ollama 7–8B + whisper + Postgres + workers) | OOM | Concurrency limits per queue; ASR and LLM never run concurrently in small mode |
| 7 | Remote MCP OAuth server is its own project | M9 slips | stdio first; remote MCP can slip to M10 |

## 12. Column-level data model (v1)

All tables: `id uuid PK (v7)`, `created_at`, `updated_at` unless noted. FK = indexed. `ON DELETE`: C = cascade, R = restrict, N = set null.

| Table | Key columns |
|---|---|
| users | email (uniq), password_hash (argon2id), role enum(admin,editor,viewer), is_active, last_login_at |
| user_sessions | user_id→users C, refresh_hash, family_id, replaced_by, expires_at, revoked_at, ip, user_agent |
| api_tokens | user_id→users C, name, token_hash, scopes text[], last_used_at, expires_at, revoked_at |
| projects | name, owner_id→users R, retention_override jsonb null, deleted_at |
| project_members | project_id→projects C, user_id→users C, role; uniq(project_id,user_id) |
| video_sources | project_id→projects C, kind enum(upload,direct_url,ytdlp,youtube_api,rtmp), origin_url, title, status enum, duration_s, original_asset_id→media_assets N, proxy_asset_id→media_assets N, waveform_asset_id→media_assets N, media_hash, meta jsonb, created_by→users R |
| source_permissions | source_id→video_sources C (uniq), user_id→users R, attested_at, statement_text |
| media_assets | project_id→projects C, kind enum(original,proxy,audio,waveform,clip_render,thumbnail,subtitle,temp), storage_backend, storage_key (uniq), size_bytes, sha256, mime, duration_s, width, height, expires_at, deleted_at |
| asset_leases | asset_id→media_assets C, job_id→processing_jobs C, expires_at |
| processing_jobs | project_id→projects C, source_id→video_sources C null, kind, status enum(queued,running,succeeded,failed,cancelled), requested_by→users N, params jsonb, error_code, error_message, heartbeat_at |
| job_stages | job_id→processing_jobs C, stage, params_hash, status, attempts, output_asset_id→media_assets N, started_at, finished_at, error_code, error_message; uniq(job_id,stage); index(source via job, stage, params_hash) |
| transcripts | source_id→video_sources C, version int, engine, model, language, cache_key (source_media_hash+engine+model+language), is_current, edited_by→users N |
| speakers | transcript_id→transcripts C, label, display_name |
| transcript_segments | transcript_id→transcripts C, idx int, start_ms, end_ms, text, speaker_id→speakers N; uniq(transcript_id,idx) |
| transcript_words | segment_id→transcript_segments C, idx, start_ms, end_ms, text, confidence |
| highlight_candidates | source_id→video_sources C, transcript_id→transcripts R, start_segment_idx, end_segment_idx, start_ms, end_ms, category, hook, title, score, explanation, standalone_note, confidence, provider, model, raw jsonb, status enum(new,accepted,rejected) |
| clips | project_id→projects C, source_id→video_sources C, candidate_id→highlight_candidates N, current_version_id→clip_versions N, status |
| clip_versions | clip_id→clips C, version int, start_ms, end_ms, aspect, crop jsonb, caption_template_id→caption_templates N, caption_overrides jsonb, audio jsonb, branding jsonb, render_asset_id→media_assets N, thumbnail_asset_id→media_assets N, qc_report jsonb, created_by→users N |
| clip_metadata | clip_version_id→clip_versions C, platform enum(generic,youtube,instagram), title, hook, description, short_description, hashtags text[], tags text[], thumbnail_text, audience; uniq(version,platform) |
| caption_templates | name, schema_version, style jsonb, is_builtin, owner_id→users N |
| ai_providers | kind enum(ollama,openrouter,openai,anthropic,gemini,groq,openai_compat), name, base_url, enabled, mode enum(local,remote), rate_limit jsonb |
| encrypted_credentials | provider_id→providers C (uniq) or social_account_id→social_accounts C, ciphertext, wrapped_dek, key_version, mask |
| ai_models | provider_id→ai_providers C, model_id, context_length, supports_json_schema, is_free, discovered_at, enabled |
| task_model_routes | task enum(highlight,metadata,translation,captions_cleanup), model_id→ai_models R, fallback_order int |
| price_table | model_id→ai_models C, input_per_mtok, output_per_mtok, source enum(provider,user), currency |
| ai_usage_logs | model_id→ai_models N, job_id→processing_jobs N, task, tokens_in, tokens_out, cost_est, latency_ms, status, error |
| social_accounts | project_id→projects C, platform enum(youtube,instagram), external_id, display_name, auto_publish_opt_in, needs_reauth, token_expires_at |
| publishing_jobs | clip_version_id→clip_versions R, account_id→social_accounts R, state enum(draft,approved,scheduled,uploading,processing,awaiting_confirmation,published,failed,cancelled), scheduled_at, dedupe_hash (uniq unless override), platform_container_id, error_code, error_message, approved_by→users N |
| publishing_history | publishing_job_id→publishing_jobs R, platform_post_id, url, confirmed_at |
| automation_rules | project_id→projects C, trigger jsonb, steps jsonb, mode enum(manual,semi,auto), enabled, limits jsonb |
| automation_runs | rule_id→automation_rules C, status, started_at, finished_at, summary jsonb |
| retention_policies | scope enum(global,project), project_id null, kind→asset kind, ttl_days |
| audit_logs | actor_id→users N, actor_kind enum(user,mcp,system), action, target_type, target_id, details jsonb, ip, request_id (no updated_at) |
| notifications | user_id→users C, kind, payload jsonb, read_at |
| settings | scope, project_id null, key, value jsonb; uniq(scope,project_id,key) |
| mcp_confirmations | token_hash, user_id, action, payload_hash, expires_at, used_at |

Added vs SPEC list: `clip_metadata`, `task_model_routes`, `mcp_confirmations` (needed for per-platform metadata, per-task routing, two-step confirmation).

## 13. REST endpoint plan (/api/v1)

| Area | Endpoints | Min role |
|---|---|---|
| setup/auth | GET setup/status; POST setup/admin (only when no users); POST auth/login, refresh, logout; GET auth/me; POST users/invite (admin); API tokens CRUD | public / any / admin |
| projects | CRUD; members CRUD | viewer read, editor write, owner delete |
| sources | POST (url+attestation) ; GET list/detail; DELETE; POST uploads via tus at `/files/` (hook creates source) | editor |
| jobs | POST sources/{id}/analyze; GET jobs, jobs/{id}; POST jobs/{id}/retry, cancel | editor |
| transcripts | GET; PATCH segments/words (new version); POST regenerate?force | editor |
| highlights | POST find (prompt, count, duration); GET list; PATCH accept/reject | editor |
| clips | CRUD; POST versions; POST preview-render; POST render; GET download (Range) | editor |
| captions/templates | templates CRUD; POST captions/generate; GET export srt/vtt | editor |
| metadata | POST generate (per platform); PATCH | editor |
| providers/models | providers CRUD, POST test, POST discover; models list/patch; routes PUT; GET usage | admin |
| publishing | accounts: OAuth start/callback/disconnect; jobs: create, approve, schedule, cancel, list | editor / publish |
| automation | rules CRUD; POST pause-all; runs list | admin/editor |
| storage/retention | GET report; policies PUT; POST cleanup?dry_run | admin |
| dashboard, settings, audit, health | GET dashboard; settings CRUD; GET audit; GET health/live, health/ready | any / admin |
| WS | /ws/jobs (auth via ticket), /ws/live (V2) | any |

Error envelope `{code,message,details,request_id}`; cursor pagination `?cursor=&limit=`.
