# Project: AI Video Clipper — Self-Hosted, Open-Source AI Clipping Platform

> **Final build specification, v2.** This replaces the original prompt. The changes are: the ingestion strategy follows what the platforms actually allow, the milestone order respects dependencies, MinIO is gone, publishing uses the real platform APIs, the testing tiers are honest, and long tasks are split so the build stays feasible.

---

## 0. How to use this prompt (read first)

This specification is too large to build in one pass. Run it as follows:

1. **Session 1 – Milestone 0 only.** Give the agent this entire file and ask it to do **Milestone 0 (Discovery & Architecture)** and then stop. Nothing else is built until the human approves.
2. **One milestone per session after that.** Each later session gets this file plus `docs/ARCHITECTURE.md`, `docs/DECISIONS.md` and `docs/PROGRESS.md` from the repo, and builds **one** milestone.
3. **The repo is the source of truth.** The agent writes its decisions, deviations and known gaps to `docs/DECISIONS.md` and `docs/PROGRESS.md`. It never relies on chat history.
4. **Gate.** A milestone is done only when its exit criteria in §15 pass. Tests that were skipped or that need credentials are reported as **SKIPPED**, never as PASSED.

---

## 1. Role

Act as a senior software architect, Python backend engineer, Angular frontend engineer, ML engineer (speech + LLM), DevOps engineer and product designer.

Deliver a working, self-hosted, open-source product, not a prototype, mockup or static UI. Every visible control does its real operation, or it is hidden and the reason is documented as unimplemented.

Never fake responses, metrics, previews or integration success. When an integration is not configured, the UI says so clearly.

---

## 2. Product positioning and scope

### 2.1 Who it is for
Creators, podcasters, educators and teams who **own or are licensed to use** their source footage and want to:
- turn long recordings into short vertical clips with captions,
- run everything **locally for free** (Ollama + faster-whisper), or plug in their own API keys,
- automate the pipeline and drive it from **Claude via MCP**.

### 2.2 What it is NOT
- Not a mass-scraping tool. Link ingestion (yt-dlp, §3.1) is for content the user owns or has permission to use. It has per-source attestation, is rate-limited, and never crawls channels.
- Not a "virality predictor." Scores are configurable heuristics plus LLM judgement, labelled as such.

### 2.3 Differentiators (protect these and don't dilute them)
1. Fully local mode with zero paid dependencies.
2. First-class MCP server (local + remote).
3. Transparent, editable AI decisions: timestamps, reasons and scores can all be inspected and changed.

### 2.4 Scope tiers
| Tier | Content |
|---|---|
| **MVP (Milestones 0–6)** | Local upload → transcription → highlight detection → editor → captions → MP4 render → download. Local + remote LLM providers. Auth. Dashboard. |
| **V1 (Milestones 7–10)** | YouTube + Instagram publishing, automation engine, MCP server, retention, hardening. |
| **V2 (Milestone 11)** | Live RTMP ingestion via MediaMTX. |
| **Future (interfaces only)** | Twitch, Discord, TikTok and other adapters. Interfaces and docs only, no fake implementations. |

---

## 3. Platform reality (constraints the design MUST respect)

Re-verify each of these against the official docs in Milestone 0 and record the date checked in `docs/DECISIONS.md`.

### 3.1 Ingestion
- **Primary UX: paste a YouTube video or live link.** The YouTube Data API has no media download, so link ingestion uses **yt-dlp** (Unlicense) in a `YtDlpProvider`:
  - **Video links:** fetch metadata, then download the best ≤1080p MP4 (configurable) into storage. After that the normal pipeline runs.
  - **Live links:** record the live HLS stream in rolling segments (supports `--live-from-start` where available) and feed the live pipeline. Clips can be made while the stream is still running.
  - **Fragility is a first-class concern.** YouTube actively fights downloaders: PO Tokens, JS challenges, SABR, IP blocks. So:
    - run yt-dlp in its own `ingest-worker` container that **auto-updates yt-dlp on a schedule** and on extractor failure;
    - bundle a JS runtime (Deno) and an optional **PO Token provider sidecar** (e.g. bgutil-ytdlp-pot-provider) as a compose profile;
    - support an optional user-supplied cookies file (encrypted at rest, never logged) and an optional proxy;
    - document that a **home/residential IP works far better than a VPS/datacenter IP**;
    - surface errors clearly (403 / "sign in to confirm" / rate-limit) with fix-it hints. Never fail silently.
  - **Compliance guardrails:** a per-source attestation checkbox ("I own this content or have permission to use it"); the feature can be toggled off by the admin; a per-day download limit; no channel or playlist crawling; this is not a DRM bypass (DRM-protected content is refused). The README states plainly that downloading may violate YouTube's Terms of Service and that responsibility lies with the user.
  - **Keep it isolated from the official API:** yt-dlp never uses the YouTube OAuth tokens or API project credentials used for publishing, so downloading can't put the publishing integration at risk.
  - **Fallbacks, always available:** (1) upload the original file; (2) for channels the user owns, import captions through the official API so transcription can be skipped; (3) live via OBS → RTMP → MediaMTX.
- The YouTube Data API is still used for metadata where configured, and for publishing (§3.2).
- **Direct media URLs** are allowed only with SSRF protection: block private, link-local and loopback ranges and cloud metadata IPs, re-resolve DNS on every redirect, allow only http/https, check content type, and enforce size and time limits.
- **Twitch / Discord:** future adapters only, and only through the RTMP path or official exports. Recording Discord voice needs every participant's consent and has to follow Discord's developer policy. Do not build it now.

### 3.2 Publishing
- **YouTube upload:** OAuth 2.0, `videos.insert` with resumable upload. Quota is per Google Cloud project and limited (re-check the current default allocation). Uploads from **unverified API projects may be locked to private** until the project passes Google's compliance audit. Show this to the user and document how to request the audit.
- **Instagram Reels:** needs an Instagram **professional (Business/Creator) account**, Meta app permissions (including `instagram_content_publish`) and **Meta App Review** before people other than the app's own testers can use it. Use the **resumable upload flow (`upload_type=resumable` → `rupload.facebook.com`)** so a self-hosted install does **not** need a public video URL. Poll the container `status_code` until `FINISHED`, then publish. Read the publishing limit from `/<IG_ID>/content_publishing_limit` instead of hardcoding it, because the documented number has changed over time. Containers expire after 24 hours.
- **Platform confirmation is the only success signal.** Until then the status is `uploading`, `processing` or `awaiting_confirmation`.

### 3.3 AI / ML
- **faster-whisper** transcribes but does **not** do speaker diarization.
- **Diarization (pyannote via WhisperX)** needs a free Hugging Face token **and** acceptance of the gated model terms (the diarization pipeline and its segmentation model). It is slow on CPU. So diarization is **optional, off by default**, and enabled by supplying `HF_TOKEN`. Without it, the system falls back to a single speaker and still works.
- **CPU-only defaults:** whisper `small` (int8) on CPU and `large-v3` or `large-v3-turbo` when a GPU is present. Model sizes are configurable. Do not quote speed numbers you haven't measured. Benchmark them in Milestone 4 and publish the measured numbers in the docs.
- **LLM context limits:** long transcripts must be **chunked** (see §6.E). Never send a 2-hour transcript in one prompt to a 4k/8k-context local model.

### 3.4 Storage
- **Do NOT use MinIO.** The community edition went into maintenance mode in late 2025 and stopped publishing official images and binaries. Use a storage abstraction with:
  - `LocalFilesystemStorage` (default, Docker volume),
  - `S3CompatibleStorage` (boto3; works with AWS S3, Cloudflare R2, Backblaze B2, SeaweedFS, Garage and others).
  - Optional compose profile for a self-hosted S3 server. Choose a maintained one in Milestone 0 and check its license.

### 3.5 Claude / MCP
- **Local MCP:** stdio server for Claude Desktop.
- **Remote MCP:** Streamable HTTP over HTTPS, reachable from Anthropic's cloud, with OAuth as required by the current Claude custom-connector docs. A self-hosted box behind NAT needs a tunnel or reverse proxy (for example Cloudflare Tunnel, Tailscale Funnel, or Caddy with a domain). Document all three. Verify plan availability (Pro/Max/Team) against the current Anthropic docs at build time.

---

## 4. Technology stack

**Frontend:** Angular (latest LTS), standalone components, TypeScript strict, Signals, Tailwind CSS, Lucide icons, feature-based folders, WebSocket job updates. Use the browser's native `<video>` with a **low-res proxy** for editing.

**Backend:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.x (async), Alembic, REST + WebSockets.

**Database:** PostgreSQL 16.

**Queue:** Celery + Redis, with **separate queues**: `default`, `media` (FFmpeg), `asr` (transcription), `llm`, `publish`. Celery beat is the scheduler. **Redis pub/sub** carries progress events from workers to the API's WebSocket hub.

**Live (V2):** a dedicated long-running `live-worker` service, **not** a Celery task, because stream consumers run for hours.

**Media:** FFmpeg/FFprobe (with libass for captions), PySceneDetect, OpenCV, MediaPipe (face detection for reframing).

**AI:** faster-whisper (default ASR), WhisperX (optional alignment + diarization), Ollama, OpenRouter, plus providers through one abstraction.

**Storage:** the abstraction from §3.4.

**Uploads:** **resumable chunked uploads (tus protocol)** so multi-GB files survive proxies and dropped connections.

**MCP:** official Python MCP SDK, stdio + Streamable HTTP.

**Deployment:** Docker, Docker Compose with profiles (`gpu`, `live`, `s3`), health checks, `.env`-based config.

**Licensing:** pin versions and generate `THIRD_PARTY_LICENSES.md`. Flag any copyleft or gated components. Model weights such as Llama or Gemma have **their own licenses**. Document that users accept them when pulling models.

---

## 5. Architecture rules

1. **Hexagonal / ports and adapters.** Domain services never import platform SDKs directly. Ports: `VideoSourceProvider`, `StorageBackend`, `TranscriptionEngine`, `DiarizationEngine`, `LLMProvider`, `PublishingAdapter`, `Notifier`.
2. **One service layer.** REST routers, Celery tasks and the MCP server all call the **same application services**. No business logic in routers or MCP tool handlers.
3. **Stateless API.** Heavy work never runs in request handlers. Requests enqueue jobs and return a `job_id`.
4. **Persistent, resumable jobs.** Every pipeline stage writes its state and output artifact. On retry, finished stages are skipped (checkpointing). Jobs are idempotent and keyed by `(source_id, stage, params_hash)`.
5. **Asset locking.** Assets referenced by active jobs carry a lease and can't be deleted by retention.
6. **Config over code.** Model IDs, scoring weights, caption templates, limits and prices are all data, not constants.
7. **Single caption template definition.** One JSON template schema is compiled **both** to CSS (editor preview) and to ASS (FFmpeg render), so the preview matches the final render.

---

## 6. Modules

### A. Dashboard
Totals for projects, sources, clips, jobs (running/failed), published posts, AI usage and estimated spend, storage used against quota, and recent activity. **Every number is a DB query.** Empty states rather than zeros-that-look-like-data.

### B. Video sources
`VideoSourceProvider` interface:
```
validate_source(input) -> ValidationResult
get_metadata(source) -> SourceMetadata
get_source_status(source) -> SourceStatus
get_authorized_media(source) -> MediaHandle | NotAvailable(reason)
get_stream_information(source) -> StreamInfo | NotApplicable
```
Implementations:
- `YtDlpProvider`: **primary path**; YouTube video and live links (and later, other yt-dlp-supported sites behind an admin allow-list), per §3.1.
- `LocalUploadProvider` (tus resumable upload): always-available fallback.
- `DirectUrlProvider` (SSRF-safe, see §3.1).
- `YouTubeApiProvider` (official metadata + optional caption import for owned channels).
- `RtmpLiveProvider` (V2).
- `TwitchProvider` and `DiscordProvider`: interface stubs that return `NotImplemented`, with docs only.

On ingestion: validate the container and codecs with ffprobe, store the original, and generate a **720p/480p proxy MP4** for the editor plus a waveform JSON.

The `source_permissions` record stores the user's attestation ("I own or am licensed to use this content"), a timestamp and the user ID.

### C. Processing pipeline (independent, testable stages)
1. Source validation
2. Ingestion + proxy generation
3. Metadata extraction (ffprobe)
4. Audio extraction (16 kHz mono WAV for ASR)
5. **ASR pre-processing** (loudness leveling and optional denoise for recognition only)
6. Transcription
7. Word-level alignment
8. Speaker diarization (optional)
9. Scene detection
10. Highlight candidate generation (LLM)
11. Highlight validation and scoring
12. Clip cut (frame-accurate)
13. Reframing (9:16 / 1:1 / 16:9, face/speaker tracking)
14. Subtitle generation
15. Caption rendering (ASS/libass)
16. **Final loudness normalization** (EBU R128, `loudnorm` two-pass, target configurable, default −14 LUFS)
17. Thumbnail generation
18. Quality validation (duration, A/V sync, resolution, caption bounds, silent-audio and black-frame checks)
19. Export

Each stage has its own service, its own Celery task, its own persisted output and its own tests.

### D. Transcription
- Language detection, multi-language, word timestamps, editable transcript (with versioning), retry.
- **Cache:** transcripts are keyed by `(source_media_hash, engine, model, language)`. Regenerating clips **never** re-transcribes unless the user forces it.
- Transcript edits flow into captions **without** re-running ASR.

### E. Highlight detection (core intelligence)
Categories: interesting conversations, humour, emotion, education, strong opinions, surprising facts, controversy, tutorials, complete Q&A, standalone stories. Users can add custom prompts, for example: "Find the five most interesting discussions about AI, 30–60s each."

**Algorithm (required):**
1. Split the transcript into **overlapping windows** sized to the selected model's context length (read it from model metadata, or from a configured fallback value).
2. **Map:** per window, the LLM returns candidates as **structured JSON** (schema-validated; at most 2 repair retries, then fail visibly).
3. Candidates reference **segment/word IDs**, not free-text timestamps. The server derives the timestamps from the transcript, so they can't be invented.
4. **Boundary snapping:** extend or trim to sentence boundaries, avoid cutting mid-word or mid-thought, respect min/max duration, and add optional padding.
5. **Validation:** the transcript text of each candidate is **pulled from the stored transcript**, never taken from the LLM. Reject candidates that overlap a stronger one or fail the duration rules.
6. **Reduce:** global ranking with configurable weights (LLM score, category match to the user prompt, standalone-context score, speech density, scene changes, audio energy).

Fields returned per candidate: start, end, duration, transcript (from the source), hook, suggested title, category, score 0–100, explanation, standalone-context assessment, confidence, and the model and provider used.

The UI labels the score as **"AI relevance score, not an engagement prediction."**

### F. Editor
Proxy video preview, timeline with waveform and scene markers, start/end trim (frame-stepped), transcript panel (click a word to seek, select words to set the range), caption editor, aspect ratio (9:16 / 1:1 / 16:9), crop box with speaker-tracking toggle and manual keyframes, fonts, caption templates, background (blur/solid/image) for letterboxing, intro/outro clips, logo overlay, audio gain/mute/music bed, **server-rendered preview** (low-res, fast) and final export.

Editing never re-runs transcription or highlight detection. Every save creates a `clip_version`.

### G. Captions and subtitles
Word-level, sentence-level and animated (karaoke/pop) captions, keyword highlighting, speaker-based styles, multi-language, optional translation (through an LLM provider, marked as machine translated). Configurable safe areas per platform. Sync check: alert if caption drift relative to word timestamps goes over a threshold. Export SRT/VTT in addition to burned-in captions.

### H. Clip metadata
Per clip and **per platform**, generated independently: title, hook, description, short description, hashtags, YouTube tags, Instagram caption, thumbnail text, category, target audience. Every field is editable and enforces platform character limits.

**Removed from the original spec:** "suggested publishing time." There's no analytics data to support it. Replace it with **user-defined posting slots** in the scheduler.

### I. AI providers
Providers: Ollama, OpenRouter, OpenAI, Anthropic, Google Gemini, Groq, and any OpenAI-compatible endpoint.

Users can: add a provider and key, validate credentials, **discover models** (where the provider supports it), test a prompt, set default and per-task models (`highlight`, `metadata`, `translation`, `captions_cleanup`), view usage and estimated cost, set fallbacks, and remove credentials.

Modes: **LOCAL / REMOTE / HYBRID** (per-task routing). A workflow that runs fully local never asks for a remote key.

Rules:
- Keys never reach the browser. After save, the API returns only masked values (`sk-…abcd`).
- **Encryption at rest:** AES-GCM envelope encryption with a master key from `APP_ENCRYPTION_KEY` (env or Docker secret). Support key rotation with a re-encrypt command. Refuse to start if the key is missing.
- Timeouts, retries with jitter, per-provider rate limits, circuit breaker, fallback chain. Every failure is logged and visible on the job.
- **Cost estimation:** use provider pricing where the API exposes it (for example the OpenRouter model list). Otherwise use a **user-editable price table**. Local inference costs 0. Label it as an estimate.
- Free and rate-limited models carry a visible badge: "rate-limited, availability not guaranteed."
- Structured output: use native JSON-schema or tool-calling modes when the provider supports them, and fall back to prompt + validate + repair.

### J. Live monitoring (V2)
MediaMTX receives authorized RTMP (with a per-session stream key). `live-worker` keeps a rolling segment buffer (HLS/fMP4 segments), extracts audio, transcribes incrementally (windowed), runs highlight checks on a sliding window, tracks candidate timestamps, and cuts clips from the buffer. It detects disconnects, reconnects within a grace period, and cleans up buffers. Concurrent sessions are limited by configurable CPU/RAM/GPU budgets, and new sessions are rejected with a clear error when the budget is exceeded.

### K. Social publishing
Adapters: `YouTubePublisher` and `InstagramPublisher` (per §3.2). `TikTokPublisher` stays an interface only and is clearly marked unavailable in the UI.

Flow: **draft → approval → scheduled → uploading → processing → published | failed | cancelled.**
- Duplicate prevention: hash of (clip_version, account, platform). A second publish of the same hash needs explicit override.
- Retries only for retry-safe errors. Errors are stored with the platform's error code and message.
- Manual cancellation before the platform confirms. After confirmation, link to the platform's own delete flow.
- History stores the platform post ID and URL **only after confirmation.**
- OAuth tokens are encrypted, refreshed automatically, and the UI warns when re-authorization is needed.

### L. Automation engine
Rules: trigger (new source ingested, live session ended, schedule) → steps (transcribe → highlights → max N clips → min score → captions → metadata → render → approval or auto-publish → record → retention).
Modes: manual, semi-automatic (stops at approval), fully automatic.
Guardrails: **global emergency pause**, max clips per day, max AI spend per day/month (hard stop), duplicate prevention, failure notifications (in-app + optional email/webhook). Auto-publish needs a one-time explicit opt-in per social account.

### M. MCP server
A separate container that calls the **REST API with a scoped token**. It doesn't connect to the DB.

Tools: `list_projects, create_project, add_video_source, get_source_status, start_video_analysis, get_transcript, find_highlights, create_clip, edit_clip, generate_captions, generate_metadata, render_clip, get_job_status, list_clips, publish_clip, schedule_clip, get_usage_report, get_storage_report, delete_project`.

Additional rules:
- Pydantic-typed inputs and outputs, with paginated list tools.
- **Two-step confirmation** for `publish_clip`, `schedule_clip` (with auto-publish) and `delete_project`: the first call returns a summary and a `confirmation_token` (expires in 5 minutes); the action runs only on a second call with that token.
- **Source upload through MCP:** large files can't go through tool arguments. `add_video_source` accepts a server-side path from an allow-listed import directory, a direct URL (SSRF-safe), or a YouTube URL with a reference to an existing upload, and returns a tus upload URL for the user.
- Per-user auth (API tokens with scopes such as `read`, `write`, `publish`, `delete`), rate limits, audit log of every tool call.
- Transports: stdio (Claude Desktop) and Streamable HTTP (remote connector) with OAuth.

### N. Storage and retention
Defaults (all configurable per project and globally): temp files 24h after success, sources 7d, clips 30d, thumbnails 30d, transcripts and metadata 90d, failed-job files 7d.
Scheduled cleanup, orphan sweep (files with no DB row and DB rows with no file), quota with warnings at 80% and 95%, storage dashboard, soft delete → permanent delete, deletion audit log, **dry-run mode** that shows what would be deleted. Never delete leased assets.

### O. Auth and security
- **First-run setup wizard** creates the admin. Public registration is **off by default**; the admin invites users.
- Roles: `admin`, `editor`, `viewer`.
- Argon2id password hashing; short-lived JWT access tokens plus rotating refresh tokens in httpOnly, SameSite cookies, with reuse detection.
- Backend authorization on every resource (project ownership/membership), input validation, rate limiting (login and API), strict CORS, CSP headers, audit logs.
- **File safety:** ffprobe validation before processing, extension and MIME allow-list, size limits, randomized storage keys, no user-controlled paths, FFmpeg run with argument lists (never through a shell string) and with timeouts and resource limits.

---

## 7. Database (PostgreSQL)

Tables: `users, user_sessions, api_tokens, projects, project_members, video_sources, source_permissions, media_assets, processing_jobs, job_stages, transcripts, transcript_segments, transcript_words, speakers, highlight_candidates, clips, clip_versions, caption_templates, ai_providers, ai_models, encrypted_credentials, ai_usage_logs, price_table, social_accounts, publishing_jobs, publishing_history, automation_rules, automation_runs, retention_policies, asset_leases, audit_logs, notifications, settings`.

Rules: UUID PKs (v7 preferred), FKs with explicit `ON DELETE` behaviour, indexes on every FK and hot filter, `created_at` and `updated_at`, soft-delete where needed, Alembic migrations only, transactions for multi-row operations, JSONB only for truly flexible data (template style and LLM raw output). **No binary media in Postgres**; `media_assets` stores only the storage key, size, hash, MIME type and duration.

---

## 8. REST API

Versioned under `/api/v1`: `auth, setup, dashboard, projects, sources, uploads (tus), jobs, transcripts, highlights, clips, editor, captions, templates, providers, models, usage, publishing, social-accounts, automation, storage, retention, settings, audit, health`.

Pydantic schemas for every request and response, consistent error envelope (`code`, `message`, `details`, `request_id`), cursor pagination, OpenAPI docs at `/api/docs` (disabled or protected in production by config).
WebSockets: `/ws/jobs`, `/ws/live`, authenticated, with per-user channel filtering.

---

## 9. Frontend

Minimal, professional SaaS UI: neutral palette, dark and light themes, one accent colour, consistent spacing scale, subtle borders, minimal shadows, no decorative gradients or animations, responsive (the editor is desktop-first and shows a "use a larger screen" notice on mobile).

Screens: setup wizard, login, dashboard, projects, new import (upload/URL/YouTube link), source details, processing status (per-stage timeline), live monitor (V2), editor, clip library, clip details, providers, models, social accounts, publishing calendar, automation builder, storage and retention, settings, audit log.

Every screen has loading, empty, error and validation states. Destructive actions need confirmation dialogs (type the project name to delete it permanently). Accessibility: keyboard navigation in the editor (J/K/L, I/O for in and out points, space to play), visible focus, WCAG AA contrast.

---

## 10. Docker

Services: `frontend` (nginx serving the built Angular app), `api`, `worker-media`, `worker-asr`, `worker-llm`, `worker-default`, `beat`, `postgres`, `redis`, `ollama`, `mcp`. Profiles: `gpu` (NVIDIA runtime for ASR and Ollama), `live` (MediaMTX + live-worker), `s3` (self-hosted S3 server).

Provide `docker-compose.yml`, `docker-compose.gpu.yml`, Dockerfiles (multi-stage, non-root), `.env.example`, named volumes, health checks, `depends_on` with `condition: service_healthy`, restart policies, CPU/RAM limits, an internal network with only `frontend` (and optionally `mcp`) published. No secrets in images. A `make up` or a single documented command brings everything up on CPU-only hardware. Model weights download on first run with visible progress and are cached in volumes.

---

## 11. Testing (honest tiers)

| Tier | Runs in CI | Mocks allowed | Reporting |
|---|---|---|---|
| **Unit** | Yes | Yes, labelled | PASS/FAIL |
| **Integration (local)** | Yes | **No.** Real Postgres, Redis, Celery, FFmpeg, faster-whisper (tiny model), Ollama (small model), MCP | PASS/FAIL |
| **External** | Only when credentials are present | No | PASS/FAIL, or **SKIPPED (no credentials)**. Never counted as passed |
| **E2E** | Yes (Playwright) | No | PASS/FAIL |

Required coverage: auth, validation, DB ops, provider config and failures, job processing, retry and checkpoint resume, FFmpeg outputs (duration, codecs, resolution verified by ffprobe), **highlight timestamp integrity** (every candidate maps to real transcript words), caption sync, retention (including lease protection), publishing state machines (with recorded platform fixtures for unit tests and real calls in the external tier), MCP authorization and confirmation tokens, frontend routing, forms, provider setup, project creation, editor, job status and publishing flows.

**Test footage:** generate it inside CI. Use FFmpeg test patterns plus speech from a local TTS (for example espeak-ng or Piper) with known text, so transcription accuracy can be checked against ground truth. Optionally add a CC0 speech sample with its licence recorded.

The test report must list skipped tests separately with their reasons.

---

## 12. Operations
Structured JSON logs with request and job correlation IDs; error reporting (optional self-hosted Sentry/GlitchTip through a DSN); job monitoring (Flower, admin-only); per-stage duration metrics; CPU/RAM/disk monitoring, plus GPU where available; AI usage and spend; `make backup` and `make restore` (pg_dump + storage sync) with a documented restore test; job recovery on restart (stuck `running` jobs go back to `queued` after a heartbeat timeout); graceful shutdown (workers finish or checkpoint the current stage).

---

## 13. Non-negotiable rules
1. No static frontend called "done."
2. No fake API responses in production code paths.
3. No provider or OAuth secrets in the frontend or in logs.
4. No video binaries in PostgreSQL.
5. No long processing in HTTP handlers.
6. No silent provider failures.
7. No "published" status without platform confirmation.
8. No bypassing of source permissions: link ingestion requires the per-source attestation, respects the admin toggle and limits, refuses DRM content, and never crawls.
9. No unverified integration presented as working.
10. No media deletion outside retention policy or explicit user action.
11. No hardcoded model IDs or availability.
12. GPU is never mandatory.
13. No monolithic processing file.
14. No business logic duplicated between REST, Celery and MCP.
15. No essential control left non-functional; unfinished features are hidden and documented.
16. No test reported as passed if it was skipped or mocked in the integration tier.

---

## 14. Deliverables
1. Angular source  2. FastAPI source  3. Schema + Alembic migrations  4. Celery workers  5. Processing pipeline  6. AI provider integrations  7. MCP server  8. Dockerfiles + Compose (+ GPU, live, S3 profiles)  9. `.env.example`  10. Automated tests + CI workflow (GitHub Actions)  11. OpenAPI docs  12. Install guide  13. Ollama guide (including model recommendations by RAM/VRAM)  14. OpenRouter guide  15. External providers guide  16. Claude MCP guide (local + remote, with tunnel options)  17. YouTube OAuth + publishing guide (including the audit process)  18. Instagram setup guide (professional account, Meta app, App Review)  19. Storage and retention guide  20. Troubleshooting guide  21. `THIRD_PARTY_LICENSES.md`  22. `CONTRIBUTING.md`, `SECURITY.md`, `LICENSE`  23. `docs/ARCHITECTURE.md`, `docs/DECISIONS.md`, `docs/PROGRESS.md`

---

## 15. Milestones (dependency-ordered)

> The original plan built highlight detection (M4) **before** provider management (M5), but highlight detection depends on providers. This plan fixes that order.

| # | Milestone | Exit criteria |
|---|---|---|
| **0** | **Discovery & architecture.** Verify §3 against current docs, license audit, open decisions (§17), ERD, API plan, Docker topology, wireframes (low-fi), acceptance test plan. **No code beyond spikes.** | Human approves `ARCHITECTURE.md` + `DECISIONS.md` |
| **1** | Repo skeleton, Docker Compose, Postgres + Alembic, Redis, Celery, health checks, CI, setup wizard + auth | `docker compose up` is healthy on a clean machine; auth tests pass |
| **2** | Storage abstraction, **yt-dlp ingest-worker (video links)**, tus uploads, ffprobe validation, proxy generation, projects/sources API + UI, WebSocket job progress | A YouTube link and a 1 GB upload both produce a proxy that plays in the browser |
| **3** | AI provider abstraction (Ollama + OpenRouter + OpenAI-compatible first, then the rest), credential encryption, model discovery, usage + cost logging | Local-only and remote-only test prompts both succeed; key never visible in any API response |
| **4** | Transcription (faster-whisper), alignment, optional diarization, transcript editor, caching | Real transcription in the integration test; re-run reuses the cache |
| **5** | Scene detection + highlight detection (chunked, schema-validated, snapped, scored) + review UI | All candidates map to real transcript words; works with a local model |
| **6** | Editor, reframing, caption engine (CSS/ASS from one template), metadata, render, quality checks, download, dashboard | **MVP acceptance (§16.A) passes** |
| **7** | YouTube + Instagram publishing, approval, scheduling, history | External tests pass with real test accounts, or are SKIPPED with a documented reason |
| **8** | Automation engine + guardrails + notifications | Semi-auto rule runs end to end; spend cap halts the run |
| **9** | MCP server (stdio + HTTP/OAuth), confirmation tokens, audit | Claude Desktop runs the MVP workflow through tools |
| **10** | Retention, backup/restore, security hardening, load test, full docs | §16.B passes; restart persistence verified |
| **11** | Live: YouTube live-link recording through yt-dlp + RTMP (MediaMTX) fallback, live-worker | A live YouTube link and an OBS push each produce a clip while the stream is running |

After every milestone: run all tests, fix failures, verify real integrations, update the docs and `PROGRESS.md` (done / partial / not started / known issues). **Do not continue while a critical test is failing.**

---

## 16. Acceptance criteria

### 16.A Core (no external accounts needed; must pass on CPU-only hardware)
1. `docker compose up` brings all core services to healthy.
2. Open the app and complete the setup wizard.
3. Create a project.
4. Paste a YouTube video link (with the attestation) **or** upload a file, and see it downloaded/validated with a proxy generated. If yt-dlp is blocked on the test network, record that test as SKIPPED with the reason, and run the upload path instead.
5. Select a **local** AI provider (Ollama).
6. Run transcription.
7. Generate highlight candidates.
8. Review the candidates (scores, reasons, real transcript text).
9. Edit a clip (trim, 9:16 reframe).
10. Add and edit captions with a template.
11. Generate a title, description and hashtags.
12. Render a real MP4 (verified by ffprobe).
13. Preview and download it.
14. Verify that the retention dry-run and actual cleanup work and respect leases.
15. Restart Docker and verify data persistence.
16. Repeat steps 3–13 through Claude Desktop (local MCP).

### 16.B Extended (needs user-supplied accounts or credentials)
17. Same workflow with a remote provider (OpenRouter or another).
18. Connect a YouTube account and publish with approval; confirmation is shown with the video URL.
19. Connect an Instagram professional account and publish a Reel with approval; confirmation is shown.
20. Run the workflow through a remote MCP connector over HTTPS.

The product must run with **no paid subscriptions** in the local configuration (hardware permitting), and paid integrations must work when valid credentials are supplied.

---

## 17. Open decisions to resolve in Milestone 0 (ask the human; don't assume)
1. **Project license:** AGPL-3.0 (stops closed SaaS forks) or MIT/Apache-2.0 (maximum adoption)?
2. **Tenancy:** single-user only, or a small team with roles? (Default: small team, invite-only.)
3. **Minimum supported hardware** for the CPU-only promise (for example 4 cores / 16 GB RAM) and the default whisper and Ollama models for it.
4. **Default local LLM** for highlight detection, chosen by benchmarking 2–3 candidates on the test set.
5. **Self-hosted S3 option** for the `s3` profile (maintained project, license checked).
6. **Notification channels** beyond in-app (email over SMTP? webhook?).
7. **Remote MCP exposure** method documented as the primary one (Cloudflare Tunnel, Tailscale Funnel or reverse proxy).
8. **Telemetry:** none by default (recommended for a self-hosted OSS product).

---

## 18. Before writing any code
Inspect these requirements, verify every claim in §3 against the current official documentation, audit dependency licenses, then produce: final architecture, ERD, API plan, Docker topology, low-fi wireframes, the milestone plan with risks, and the acceptance test plan. Where something cannot be done through supported official mechanisms, say so and propose the authorized alternative. **Stop and wait for approval.** Then build one milestone at a time.

The goal is a complete, functional, maintainable, honest AI clipping platform, not a demo.
