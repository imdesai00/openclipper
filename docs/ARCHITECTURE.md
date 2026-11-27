# OpenClipper — Architecture (DRAFT v0, Milestone 0)

> Status: **DRAFT, not approved.** Sections marked ⏳ depend on open decisions in `DECISIONS.md`. Nothing here is final until the human approves.

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
- ⏳ **Small-hardware mode**: a single `worker-all` service consuming every queue, for 4-core/16 GB machines. Decided by DECISIONS Q3.

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
⏳ Self-hosted S3 server for profile `s3`: candidates Garage (AGPL-3.0) vs SeaweedFS (Apache-2.0). DECISIONS Q5.

## 7. Auth

Setup wizard → first admin. Invite-only. Argon2id. Access JWT (15 min) in memory; refresh token (rotating, reuse detection, family revocation) in httpOnly SameSite=Strict cookie. API tokens (hashed, scoped `read|write|publish|delete`) for MCP. Authorization checked in services via project membership, not only in routers.
⏳ Tenancy model: DECISIONS Q2.

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
| 3 | yt-dlp feature conflicts with Google API compliance audit for YouTube publishing | Public YouTube uploads stay locked private | Verify in M0; possibly make link-ingest a separately-installed plugin/profile ⏳ Q9 |
| 4 | Scope: 11 milestones of large surface area | Never ships | Strict MVP gate; one milestone per session; cut list in DECISIONS |
| 5 | Editor (timeline, waveform, crop keyframes) complexity in Angular | Slow M6 | Build server-rendered preview first; keep timeline custom-but-minimal |
| 6 | RAM on 16 GB CPU box (Ollama 7–8B + whisper + Postgres + workers) | OOM | Concurrency limits per queue; ASR and LLM never run concurrently in small mode |
| 7 | Remote MCP OAuth server is its own project | M9 slips | stdio first; remote MCP can slip to M10 |
