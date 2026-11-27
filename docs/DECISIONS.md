# Decisions log

Append-only ADR log. Format: `D-NNN — title — status — date`. Supersede, never rewrite.

---

## Accepted decisions

### D-001 — Repo is the source of truth; spec at docs/SPEC.md — accepted — 2026-11-27
Spec v2 copied verbatim to `docs/SPEC.md`. Agent rules in `/CLAUDE.md`.

### D-002 — Project license: AGPL-3.0 — accepted — 2026-11-27 (answer to Q-01)
Stops closed-source SaaS forks. Consequence: every dependency must be AGPL-compatible; checked per dependency in `THIRD_PARTY_LICENSES.md`.

### D-003 — Tenancy: small team, invite-only, roles admin/editor/viewer — accepted — 2026-11-27 (Q-02)

### D-004 — Minimum hardware: 4 cores / 16 GB RAM / 50 GB disk, no GPU — accepted — 2026-11-27 (Q-03)
"Small-hardware mode": one `worker-all` consuming every queue, ASR and LLM never run concurrently. Whisper `small` int8 default on CPU. **Not yet benchmarked.** No speed numbers may be published until measured (M4).

### D-005 — Default local LLM chosen by benchmark — accepted — 2026-11-27 (Q-04)
Benchmark 2–3 candidate models (Ollama, ≈3–8B) on generated test footage. **Status: NOT DONE.** The cloud container can't run it comfortably (see D-013). Done on the human's machine or in the first part of M5. Until then no default model is named.

### D-006 — Self-hosted S3 profile: SeaweedFS (Apache-2.0) — accepted — 2026-11-27 (Q-05)
Garage is AGPL-3.0 and targets multi-node setups; MinIO CE is dead (see verification table). Re-check SeaweedFS maintenance status when M2 starts.

### D-007 — Notifications: in-app + webhook (V1); SMTP email optional — accepted — 2026-11-27 (Q-06)

### D-008 — Primary remote-MCP exposure: Cloudflare Tunnel; document Tailscale Funnel and Caddy too — accepted — 2026-11-27 (Q-07)

### D-009 — Telemetry: none — accepted — 2026-11-27 (Q-08)

### D-010 — yt-dlp link ingestion is an optional, off-by-default add-on; file upload is the default path — accepted — 2026-11-27 (Q-09)
Reason: YouTube's developer policies list downloading content as an unauthorized activity, and YouTube publishing needs a Google compliance audit for public videos. Shipping a downloader in the default install puts that audit at risk. Consequences:
- compose profile `ingest` (ingest-worker + optional PO-token sidecar), disabled unless the admin turns it on;
- the default UX is upload (tus) first; the "paste a link" UI only appears when the profile is enabled;
- yt-dlp stays isolated from all publishing credentials (as in SPEC §3.1);
- SPEC §2/§3.1 "primary UX: paste a link" is **superseded** by this decision. Milestone 2's exit criterion changes to: a 1 GB upload produces a playable proxy; the YouTube link case is tested only when the `ingest` profile is on.
- Even so, this lowers the audit risk; it doesn't remove it. Anyone who publishes to YouTube with a public-facing project should read Google's policy themselves. Documented in the README.

### D-011 — Frontend: Angular — accepted — 2026-11-27 (Q-10)
The human knows Angular. Use the current stable major at M1 (see verification table).

### D-012 — External tests need human-supplied accounts; SKIPPED until then — accepted — 2026-11-27 (Q-11)

### D-013 — Where things run (Q-12) — accepted — 2026-11-27, **revised in Milestone 1**
Originally assumed the cloud container could not run Docker. **Wrong:** `dockerd` can be started inside it
(`nohup dockerd &`), images pull, and the full `docker compose up` works. So the compose acceptance check runs here
and again in GitHub Actions on a clean runner. Caveat: the sandbox's network proxy intercepts TLS, so building needs
`docker-compose.sandbox.yml` (gitignored: host network + the proxy CA passed as an optional BuildKit secret).
Normal users do not need it.

### D-014 — Python version — accepted — 2026-11-27
Spec says 3.12; the container has 3.13. Target 3.12 in Docker images; use `uv` to get 3.12 locally.

### D-015 — Goal (Q-13) — **still open**
The human has not answered. Treated as: personal tool first, public OSS project second. Ask again before M7.

---

## External-fact verification (SPEC §3), checked 2026-11-27 via web search

Search snippets are not the same as reading the full official page. "Confirmed" below means the official source says it in the search result; "Partly" means something is missing and must be checked directly in the milestone that needs it.

| Claim | Result | Source | Action |
|---|---|---|---|
| Videos uploaded via `videos.insert` from **unverified API projects created after 2020-07-28 are locked to private**; lifted by passing the compliance audit | **Confirmed** | [YouTube quota & compliance audits](https://developers.google.com/youtube/data/guides/quota_and_compliance_audits) | M7 docs |
| Google's developer policies list **downloading content** as an unauthorized activity | **Confirmed** (the audit scope was not found) | [YouTube API Developer Policies](https://developers.google.com/youtube/terms/developer-policies) | Drove D-010 |
| `videos.insert` costs 1,600 quota units; default 10,000/day | **Mostly confirmed**: one page version showed a conflicting older "100 inserts/day" number; recheck at M7 | [Quota cost table](https://developers.google.com/youtube/v3/determine_quota_cost) | Recheck M7 |
| Instagram Reels: resumable upload (`upload_type=resumable`, `rupload.facebook.com`), `content_publishing_limit` endpoint | **Confirmed** | [Meta content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/) | M7 |
| Instagram resumable upload "only for apps using Facebook Login for Business" | **New caveat, not in SPEC** | Meta docs (search summary) | Verify at M7: may restrict which login flow we use |
| Instagram publishing without App Review for the owner's own account | **Partly**: docs say apps in development mode are for people with roles on the app; App Review is needed for Advanced Access (other users). The exact table was not retrievable | [Meta app review](https://developers.facebook.com/docs/instagram-platform/app-review) | Verify at M7 |
| MinIO CE stopped publishing binaries/images (Oct 2025) and went to maintenance mode (Dec 2025) | **Confirmed**; later reports say archived (Feb–Apr 2026, single-source) | [MinIO repo](https://github.com/minio/minio) | D-006 |
| SeaweedFS = Apache-2.0; Garage = AGPL-3.0 | **Confirmed** | Multiple comparisons (one source mixed them up) | D-006 |
| yt-dlp needs an external JS runtime (Deno recommended) since 2025.11.12, plus the `yt-dlp-ejs` component; PO tokens via a provider plugin | **Confirmed** (late-2025 sources; nothing found for 2026) | [yt-dlp EJS](https://github.com/yt-dlp/ejs), [issue #15012](https://github.com/yt-dlp/yt-dlp/issues/15012) | Recheck when the `ingest` profile is built |
| pyannote diarization needs a HF token + accepting gated terms; community-1 pipeline is CC-BY-4.0 | **Confirmed** | [pyannote community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) | M4 |
| Claude custom connectors: remote MCP over Streamable HTTP, OAuth supported; available on Free (1 connector), Pro, Max, Team, Enterprise | **Confirmed**, and **SPEC says Pro/Max/Team only**, which is outdated | [Claude custom connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp) | M9 |
| Angular current LTS | **Conflicting search results**: v21 listed active until 2026-05, LTS until 2027-05; sources say v22 released 2026-06 with 24-month support | [angular.dev releases](https://angular.dev/reference/releases) | Check the page directly at M1 and pin the version then |

### Spec corrections these produce
1. SPEC §3.1 "primary UX is paste a link" → superseded by D-010.
2. SPEC §3.5 plan availability → also Free (1 connector).
3. SPEC §3.2 Instagram → add the Facebook-Login-for-Business caveat for resumable upload (verify).
4. SPEC §3.2 "100 default inserts" is not in the spec; keep the 1,600-unit figure, verify at M7.

---

## Still open
- Q-13 / D-015: what the human wants this to be.
- D-005 benchmark not run.
- Remaining M0 deliverables: column-level ERD, API endpoint table, low-fi wireframes, acceptance test plan.

### D-016 — Broker/cache image: Valkey 8 instead of Redis — accepted — 2026-11-27
`redis:7-alpine` resolved to Redis 7.4.12, which is under RSALv2/SSPLv1 (source-available, not open source).
That is a poor fit for an AGPL project and SPEC §4 requires flagging gated components. Valkey 8 is BSD-3-Clause, maintained
by the Linux Foundation, and speaks the same protocol (Celery and redis-py need no change). The compose service is still
named `redis`. All 38 backend tests pass against Valkey. **Human review requested:** you did not specify this.

### D-017 — Milestone 1 schema is a subset of the ERD — accepted — 2026-11-27
Tables created now: `users, user_sessions, invitations, audit_logs, settings`. `invitations` is new (not in SPEC §7):
it is how invite-only registration works. `api_tokens` is deferred to the MCP milestone (nothing uses it before then);
`notifications`, `projects` etc. arrive with the milestone that uses them. Each milestone adds its own migration.

### D-018 — Frontend stack versions — accepted — 2026-11-27
Angular 22.3 (latest; `npm view @angular/cli` = 22.3.0; the support-window dates in search results conflicted, so
re-check angular.dev before Milestone 10). Tests use Vitest (Angular's default). Icons: `@lucide/angular`
(the old `lucide-angular` package does not support Angular 22). Tailwind CSS 4.

### D-019 — Auth design details — accepted — 2026-11-27
- Access token: JWT HS256, 15 min, held in memory only (never web storage). Refresh token: opaque, stored as SHA-256,
  rotated on every use, httpOnly + SameSite=Strict cookie scoped to `/api/v1/auth`.
- Presenting an already-rotated refresh token revokes the whole token family (theft response). No grace window for
  concurrent refreshes; the web client therefore shares one in-flight refresh. Revisit if multi-tab use causes
  surprise sign-outs.
- Cookie-authenticated endpoints (refresh, logout) also require header `X-OpenClipper-Client: web`.
- First-admin setup is serialized with a Postgres advisory lock (tested with 5 concurrent requests: exactly one wins).
- Rate limiting is Redis-backed. If Redis is down the limiter fails open **and logs an error**; readiness also reports 503.
- Password policy: 12-128 characters, no composition rules.

---

## Milestone 2 decisions (2026-11-27)

### D-020 — Resumable uploads: our own tus 1.0.0 server + tus-js-client — accepted
Creation, termination and expiration extensions. Bytes are staged on local disk and moved into the `StorageBackend` on
completion, so local and S3 storage share one code path. A PATCH holds `SELECT … FOR UPDATE NOWAIT` on the upload row, so a
second concurrent PATCH gets 423 (tus-js-client retries 423). Bytes received before a dropped connection are committed, so
the client resumes from the server's real offset. Per-PATCH limit 512 MiB (nginx allows 600 MiB), total limit
`MAX_UPLOAD_BYTES` (20 GiB default). Client: `tus-js-client` 4.3.1 (MIT) with a per-project fingerprint and token refresh
before each request, so uploads outlive the 15-minute access token.

### D-021 — Only files with a video track are accepted (audio-only is rejected) — accepted, **needs your review**
SPEC §6.B says "validate with ffprobe" and the editor/reframing/render pipeline needs a picture. Podcast audio files
(`.mp3`, `.m4a`) are therefore rejected with `no_video_stream` for now. If podcasters should be able to upload audio, that
needs a design decision (render a waveform/background video for audio-only sources). **Open question Q-14.**

### D-022 — Hostile-media defences — accepted
Extension allow-list at upload, then ffprobe decides: only container families `mov/mp4, matroska/webm, avi, mpegts, flv, asf,
mpeg` pass; HLS/concat/image formats are rejected. Every ffprobe/ffmpeg call uses `-protocol_whitelist file`, argument
lists only, `nice`, timeouts, and a cancel check. Tested with a real playlist disguised as `.mp4`: a listening server
received zero requests. Not done: per-process memory/CPU rlimits (container limits in compose apply instead).

### D-023 — Pipeline execution model — accepted
One Celery task per stage (`pipeline.validate|proxy|waveform|thumbnail|download`), chained, each on its own queue.
Tasks carry only a job id and re-read all state from the DB. `job_stages` rows record status, params hash, attempts, output
asset. Checkpoint key `(source_id, stage, params_hash)`: a re-run reuses a succeeded stage only when its output file still
exists. Failed stages are retried in place and finished stages are not re-run (tested). Workers heartbeat every 5 s; a beat
task requeues jobs whose heartbeat is older than 120 s. `acks_late` + `reject_on_worker_lost` cover hard worker crashes.
Assets in use hold an `asset_leases` row (the retention milestone must honour it). Progress goes worker → Redis pub/sub →
WebSocket hub, filtered per user by project membership.

### D-024 — Browser media access uses short-lived signed URLs — accepted
`<video>`/`<img>` cannot send an Authorization header. The API signs a 10-minute token bound to one asset id; only proxy,
waveform and thumbnail assets are servable (originals never are). The player re-fetches links and continues from the same
position when one expires. HTTP Range (206/416) is supported for seeking.

### D-025 — Project authorization — accepted
Admins can access every project. Others need a `project_members` row (owner/editor/viewer); a global `viewer` is read-only
everywhere. Non-members get 404, not 403, so project existence does not leak. Deleting a project needs the exact name and is
refused while jobs run.

### D-026 — Link sources — accepted, with gaps
- **Direct URLs:** SSRF guard on every hop (http/https only, no credentials, only globally routable addresses; a host that
  resolves to a mix of public and private is refused), connecting to the validated IP while keeping the hostname for TLS,
  content-type allow-list, size cap enforced on declared and streamed bytes, time cap. Home-lab option
  `DIRECT_URL_ALLOW_PRIVATE` (default off).
- **YouTube (yt-dlp):** optional `ingest` compose profile + admin switch + daily limit; the UI only offers it when the switch
  is on **and** the ingest worker's heartbeat is present. Single video links only (channels, playlists, handles refused),
  live streams refused until the live milestone, DRM refused. yt-dlp runs as a subprocess, self-updates into a volume at start
  and every 12 h, and once more when an extractor error occurs. Errors map to actionable messages. The ingest container gets
  an explicit minimal environment (no `env_file`).
- **Not built in M2 (deferred, say if you want them sooner):** the PO-token sidecar; a user-supplied cookies file (it must be
  encrypted at rest, and the encryption helper arrives with the providers milestone); the formal `VideoSourceProvider`
  interface (there are only two concrete providers, so an abstraction now would be unused code; revisit at the live milestone).
- **Not verified here:** a real YouTube download. See PROGRESS.

### D-027 — Packaging lesson — accepted
The Dockerfile's last stage is the optional `ingest` image, and Docker builds the last stage by default. Compose therefore pins
`target: runtime` for every normal service. Found by the browser test that checks the "download service" status.

### D-028 — Sandbox-only: Docker Hub rate limiting — informational
This sandbox shares an IP that exhausts Docker Hub's anonymous pull limit. Base images were pulled through
`mirror.gcr.io` and tagged locally. Not needed on a normal machine or in GitHub Actions.

---

## Answers from the human (2026-11-27, start of Milestone 3)

### D-029 — Audio-only sources must be supported, in addition to video — accepted (answers Q-14), **design pending**
The human wants both audio and video files ("audio with video both"). Until this is built, audio-only files are still rejected
with `no_video_stream` (D-021). Open design question for the editor/render milestone: what picture does a clip of a podcast
get (waveform, cover art, a background, captions only)? Needs a short design decision before Milestone 6. Probing and the
proxy/waveform stages must accept a missing video track and produce an audio proxy.

### D-030 — Goal: a public open-source project — accepted (answers Q-13, closes D-015)
Consequences we now hold ourselves to: documentation and honest limits are product features; every claim is backed by a test or
labelled unverified; contributors can run everything with Docker alone; CI is the gate; no telemetry (D-009); no features that
only make sense for a hosted business (billing, tenants). The AGPL license (D-002) stays.

### D-031 — Valkey instead of Redis confirmed by the human (D-016)

## Milestone 3 decisions (2026-11-27)

### D-032 — AI provider architecture — accepted
Port `LLMProvider` with four adapter families: **Ollama** (native `/api/chat`, `/api/tags`, `/api/show`), **OpenAI-compatible**
(serves OpenAI, OpenRouter, Groq and any compatible server such as LM Studio, vLLM or Ollama's `/v1`), **Anthropic** (Messages
API; structured output through a forced tool call) and **Gemini** (`generateContent`; key in the `x-goog-api-key` header;
structured output by prompt + validate + repair because Gemini's schema dialect differs from JSON Schema).
**What has actually been verified against a live service:** Ollama native, and the OpenAI-compatible adapter (against Ollama's
`/v1` and a scripted real HTTP server). **Verified only against recorded-style fixtures, NOT against the live service:**
OpenRouter, OpenAI, Groq, Anthropic, Gemini. Their live tests exist and are SKIPPED without API keys (`TEST_<KIND>_API_KEY`,
`TEST_<KIND>_MODEL`). Treat those five as unproven until someone runs them with a key.

### D-033 — Mode defaults to LOCAL, enforced where the call is made — accepted
Modes: LOCAL (only providers marked local), REMOTE (only hosted), HYBRID (anything the routes name). A fresh install is LOCAL,
so nothing leaves the machine until an admin changes it. Enforcement is in `LLMService`, not the UI, and is tested by counting the
requests a remote test server receives (zero in LOCAL). Hosted kinds cannot be marked local; for a custom OpenAI-compatible
server the admin says whether it is local.

### D-034 — Credentials — accepted, with a gap
AES-256-GCM envelope encryption: a random data key per secret, wrapped by the master key from `APP_ENCRYPTION_KEY`; both layers
bind the owning provider id as AAD, so a copied ciphertext does not open. Rotation re-wraps only the data keys
(`python -m app.cli rotate-encryption-key`, with `APP_ENCRYPTION_KEY_PREVIOUS` holding the old key) and never decrypts a secret;
`check-encryption` verifies every record. Keys are masked (`sk-…abcd`) in every API response; the typed key is cleared from the
form after saving. Error text from providers is redacted (a test server that echoes the Authorization header proves it).
**Gap:** the master key is read from the environment only; Docker/Kubernetes secret files (`*_FILE`) are not supported yet.

### D-035 — Resilience rules — accepted
Up to 3 attempts per model with exponential backoff and jitter (`Retry-After` honoured, capped at 30 s); only timeouts, connection
failures, 408/429 and 5xx are retried; 400/401/403/404 never are. A per-provider circuit breaker (5 failures within 120 s opens it
for 30 s, shared through Redis so API and workers agree) makes a dead provider fail instantly. An optional per-provider
requests-per-minute limit. Fallback chain per task: when one model fails, the next is tried; if all fail the caller gets one error
listing every attempt. Every attempt, successful or not, is a row in `ai_usage_logs`.

### D-036 — Structured output — accepted
Schema-constrained generation is used when the provider supports it (Ollama `format`, OpenAI-style `json_schema`, Anthropic
tool call). Otherwise the schema is put in the prompt. Either way the answer is validated with Pydantic; on failure the error is
fed back for up to 2 repairs, then the model fails visibly and the next fallback is tried. A model that rejects `json_schema` is
remembered (per process) so the doomed variant is not retried.

### D-037 — Cost estimates — accepted
Price precedence: local provider = 0; your price override; the price the provider publishes (OpenRouter does); `:free` models = 0;
otherwise **unknown**. Unknown is stored as NULL, shown as "cost unknown", counted separately in the usage report and never
shown as $0. Everything is labelled an estimate. Free hosted models carry the badge "Free: rate-limited, availability not
guaranteed" (SPEC §6.I).

### D-038 — Test prompts run inside the HTTP request — accepted, deviation from SPEC §13.5
The admin "try a prompt" call waits for the model in the request (non-blocking async I/O, bounded by the provider timeout, admin
only) so the admin sees the answer immediately. All real LLM work in later milestones runs in workers on the `llm` queue.

### D-039 — Provider addresses are admin-chosen and may be local — accepted
A provider's base URL can point at localhost or a private network (that is how Ollama and LM Studio are reached), so it is
deliberately **not** subject to the SSRF guard used for user-supplied video links. Only admins can set it; credentials in the URL
and non-http(s) schemes are refused.

### D-040 — Ollama in the default stack — accepted
`ollama/ollama:0.40.18` (pinned to the version tested), CPU-only, memory limit 8 GB, models stored in the `ollamadata` volume,
`OLLAMA_NUM_PARALLEL=1`. No model is downloaded automatically and **no default model is recommended yet**: the benchmark that
D-005 calls for has not been run. The tests use `qwen2.5:0.5b` (397 MB) only because it is small enough for CI; its answers are
not representative of what highlight detection needs.

## Milestone 4 decisions (2026-11-27)

### D-041 — faster-whisper in its own worker image — accepted
Speech recognition is a `TranscriptionEngine` port (`ports/asr.py`) with one adapter, `adapters/asr/faster_whisper.py`
(faster-whisper on CTranslate2). Only the `worker-asr` image has the libraries (`asr` extra in `pyproject.toml`, Dockerfile
target `asr`); the API and media images do not, so they stay small and import the engine lazily. **No model is baked into the
image**: models are downloaded once into the `asrmodels` volume, either when an admin presses Download in Settings or on first
use, with progress. `av` is pinned `<17` because PyAV 17+ breaks faster-whisper 1.2.1 (`open() got an unexpected keyword
argument 'metadata_errors'`, observed); we decode our own 16 kHz WAV with `wave` + numpy so PyAV is never called.
The Hugging Face downloader keeps state in `$HF_HOME`; in the container `/app` is read-only for the `app` user, so it is set to
`/data/models/.hf` (found only by running the real image: tests as root did not catch it).

### D-042 — Transcription is its own job and never touches the source's status — accepted
Job kind `transcribe_source` with stages `extract_audio` (16 kHz mono WAV), `asr_prep` (loudness leveling, optional denoise, for
recognition only; skipped when both are off) and `transcribe` (queue `asr`). Stage outputs are checkpointed like every other
stage (`(source_id, stage, params_hash)`); the `transcribe` hash includes model, language, VAD and a `force_nonce`, so "force"
re-runs recognition and everything else is reused. A failed transcription leaves the video `ready` and playable (tested).
Whether the service is up is a Redis key (`asr:heartbeat`, 60 s TTL, refreshed every 15 s and after every model download). With
no heartbeat the API answers **503 `asr_unavailable`** instead of queueing a job that nobody will run.

### D-043 — Transcript versions, caching and editing — accepted
Versions are immutable rows (`asr`, `edit`, `restore`); one is current (partial unique index). **Cache key** =
sha256(media hash, engine, model, language, VAD, loudnorm, denoise): same file + same settings returns the existing transcript
(HTTP 200, `reused: true`, no job). The same file uploaded again, even to another project, gets a **copy** of the earlier
result without running anything (`cloned_from`). `force: true` runs recognition again as a new version.
Editing a segment never re-runs ASR. Words that survive an edit (ignoring case and punctuation) keep their exact timings;
replaced words share the span of the words they replace, in proportion to length; inserted words use the gap they were inserted
into; all of it is clamped inside the segment and kept in order (`tidy`). Concurrent edits use optimistic concurrency: the client
sends the version it edited, a stale one gets **409 `version_conflict`** and nothing is overwritten. Restore creates a new
version (history is never rewritten). Subtitle cues are cut at 84 characters, 6 s, a 1.2 s pause or a segment boundary.

### D-044 — Audio-only sources are supported — accepted, supersedes the "rejected until built" part of D-029
ffprobe accepts a file with audio and no video (`has_video` false; width/height absent); a file with neither is `no_media_stream`.
The proxy for audio-only is AAC in `.m4a`, there is no thumbnail, the waveform works as before, and the source page shows an
audio player. Direct links and uploads accept mp3, m4a, wav, flac, ogg, oga, opus, aac. **Still pending (D-029):** what picture a
*clip* of audio-only content gets; that is a Milestone 6 design decision.

### D-045 — The ASR image contains GPL-licensed FFmpeg libraries — accepted for now, **needs a human licence check**
PyAV's wheel (a faster-whisper dependency) bundles FFmpeg shared libraries built with libx264, libx265 and `--enable-version3`
(read from the installed wheel). PyAV itself is BSD-3-Clause. We never call it, but it is imported and present in
`openclipper-asr`. AGPL-3.0 and GPL-3.0 are compatible, and the backend image already ships Debian's GPL ffmpeg as a separate
process (THIRD_PARTY_LICENSES). Anyone **redistributing the built image** must honour the GPL for those libraries. Not a lawyer's
opinion; recorded so it is not a surprise. Whisper weights: the Systran tiny/base/small model cards say MIT (checked); the other
catalog models were not individually checked.

### D-046 — Speaker diarization is NOT built — accepted deviation from SPEC §6.D ("optional")
The best open diarizer (pyannote) needs a gated model and a Hugging Face token the user must accept terms for; that cannot work
"with Docker alone" (D-030). The `speakers` table and `speaker_id` column exist and are unused. Revisit with a decision, not
silently.

### D-047 — Word timings come from faster-whisper itself, not WhisperX — accepted
SPEC §3.3 lists WhisperX as optional alignment. faster-whisper's own word timestamps are used. Measured on generated speech
(see PROGRESS): first word starts within +0.32 s of the truth and last words end 0.21 to 0.55 s early. That is enough to cut and
caption, but **not frame-accurate**; an editor that needs better should add forced alignment later. Real human speech has not
been measured.

### D-048 — The model catalog is a list in code, the default is data — accepted, small deviation from "config over code"
`pipeline/asr_options.py` lists the models an admin can pick (names, size, English-only flag) because the list must match what
faster-whisper can load. Which model is the default, and the default language, live in the `settings` table (`asr`) and are
changed in the UI. The language list (100 codes) is checked equal to faster-whisper's own list.

### D-049 — Defaults, and what is not known about them — accepted
Default model `small` on CPU with int8, VAD on, loudness leveling on, denoise off (denoising can remove quiet speech). `small`
was measured on this machine for speed only. **Not measured:** accuracy on real recordings, noisy audio, accents, or GPU speed.
`tiny` (used by the tests) mis-detected generated Spanish as Italian (p = 0.57); language auto-detection on short, synthetic
or noisy audio should not be trusted, and users can pick the language.

## Milestone 5 decisions (2026-11-27)

### D-050 — Highlights: the model points, the server measures — accepted
A model returns only the ids of the first and last transcript segment of a clip (plus a title, hook, category, three 0-100
scores and an explanation). The server turns ids into the real words, derives every time from those words, and reads the text
back from the stored transcript when it is shown; no text and no time ever comes from the model. Ids the model was not shown,
reversed ranges and segments without words are refused and **counted** (shown to the user as "left out: N named a part of the
video it was never shown"). Scores from the model are clamped to 0-100 rather than rejected, so a model that says 150 does not
fail a whole window; ids that are not ids ARE rejected, and the model is asked to repair (at most 2 times, then the window fails).

### D-051 — Windows, and who decides how big — accepted
The transcript is cut into overlapping runs of whole segments (about 15% overlap). The size is the smallest context among every
model the route could use (a fallback may take over mid-run), minus reserves for instructions and the answer, and never more
than `max_window_tokens` (default 3000), so a 200k-token hosted model is not handed one huge unfocused prompt. When a provider
does not report a context length, 4096 is assumed. Token counts are estimated at 3 characters per token on purpose (too many
is safe, too few truncates). The server is told the window size (`num_ctx`) because Ollama otherwise cuts the start of a long
prompt silently (found and fixed this milestone).

### D-052 — Snapping, length rules and scene cuts — accepted
Boundaries move to the nearest sentence start / end within 10 s (a sentence ends at . ? ! … or a pause of 700 ms or more, so
transcripts without punctuation still work). Too long: trimmed back to a sentence end, else dropped. Too short: extended over
following sentences, then over earlier ones, else dropped. Padding of 150 ms before and 250 ms after is capped at half the gap
to the neighbouring word. Overlap: a clip that overlaps a stronger one by more than 30% of the shorter is dropped.
Scene cuts use ffmpeg's scene score (threshold 0.3, `SCENE_THRESHOLD`) on the editing copy. **That score is mostly luma:** a
hue-only cut is missed, and a talking head has few cuts at all. Cuts are stored once per source and shared by every run.

### D-053 — The score — accepted, with an explicit disclaimer
`final = weighted mean` of: the model's score (0.50), match to the categories asked for (0.10), stand-alone sense (0.15),
speech density against 2.5 words/s (0.05), boundaries on scene cuts (0.05) and audio energy against the source's own 90th
percentile (0.15). A component that cannot be computed (no scene cuts, no audio, no categories requested) is **left out and the
rest renormalised**, never counted as zero. Weights live in the `settings` table and can be changed through the API. **They are
untuned rules of thumb**, and the UI calls the number "AI relevance score, not an engagement prediction" and lists each
component.

### D-054 — Highlights run as their own job in their own worker — accepted
Job kind `find_highlights` (stages `detect_scenes` on the media queue, `highlights` on the `llm` queue); like transcription it
never changes the source's status. Service `worker-llm` uses the normal backend image and publishes `llm:heartbeat`, so the API
answers 503 instead of queueing work nobody will do. The `highlights` stage runs in a worker thread, so it owns a small event
loop for the async model service. Results are stored as an immutable run keyed by a hash of (transcript version, request,
scoring settings, model route): an identical request is answered from the run with no model call; `force` asks again.

### D-055 — Failure semantics — accepted
A window the model cannot answer is recorded in the run (`windows_failed`) and the others continue; the UI says "N of M parts
could not be analysed". If every window fails, or no model is configured or usable, the job fails with the reason and stores
nothing. A job that reaches the end with zero candidates succeeds and says so.

### D-056 — Review only marks; it does not create clips — accepted
`new / accepted / rejected`, recorded with who and when. Cutting, reframing and captions are Milestone 6.

### D-057 — Local model status — accepted, D-005 still open
`qwen2.5:0.5b` is used for tests because it fits in CI. Measured here (PROGRESS): 61% of its candidates named segments that do
not exist (all refused) and it gives nearly everything 100. **No default model is recommended**, and the benchmark that D-005
asks for (several local models against human-picked highlights) has still not been run.

## Milestone 6 decisions (2026-11-27)

### D-058 — Milestone 6 scope: the core editor and render now, the rest later — accepted (human's choice)
Built in M6: clips with immutable saved versions; trim; 9:16, 1:1 and 16:9 output with a manual crop box and keyframes; blurred or
solid background; one caption template schema compiled to CSS (preview) and ASS (render), including word-level and karaoke
captions; per-platform metadata; a real render with loudness normalisation; quality checks; thumbnail; download; dashboard.
**Not built in M6, listed in PROGRESS as not built:** automatic face or speaker tracking (OpenCV/MediaPipe), intro/outro clips,
logo overlay, music bed, translation of captions. The SPEC lists all of them; they need their own decisions about new
dependencies, models and licences.

### D-059 — How M6's exit criterion is read — accepted (human's choice)
SPEC §15 says M6 passes when "MVP acceptance (§16.A) passes", but §16.A steps 14 (retention dry run and cleanup) and 16 (the same
workflow through Claude Desktop over MCP) belong to Milestones 10 and 9. **M6 is judged on steps 1-13 and 15** (through a rendered,
previewed and downloaded MP4 checked with ffprobe, and data surviving a restart). Steps 14 and 16 are verified in M10 and M9 and are
listed as not yet verified until then.

### D-060 — Audio-only clips get a waveform on a background — accepted (human's choice), closes D-029
A clip made from an audio-only source is rendered as video: an animated waveform over a solid colour or a blurred background,
with the captions on top. Cover-image support is not part of M6.


### D-061 — Clip data model, and three deviations from ARCHITECTURE §12 — accepted
Tables: `clips`, `clip_versions` (immutable; one row per save), `clip_renders`, `clip_metadata`, `caption_templates`. Deviations, each
chosen for a reason: (1) `clips.current_version` is an **integer**, not a `current_version_id` foreign key, because a clip and its
versions pointing at each other is a circular FK that makes inserts and deletes awkward; a save is conflict-checked by `base_version`
(409 `version_conflict`). (2) Renders live in their own `clip_renders` table (kind `preview` or `final`) instead of a
`render_asset_id` on the version, because one version has many renders (previews, forced re-renders, different engine versions).
(3) `clip_metadata` hangs on the **clip**, not the version: titles and descriptions are edited by hand and should not fork every time a
person nudges the trim.

### D-062 — Reframing in M6: manual crop box, constant zoom, pan keyframes — accepted
Aspects 9:16, 1:1, 16:9; mode `fill` (crop) or `fit` (whole picture on a blurred or solid background); zoom 1-4x held constant for the
clip; pan as `(t_ms, cx, cy)` keyframes, linearly interpolated, compiled to ffmpeg expressions. **No zoom animation and no automatic face
or speaker tracking** (D-058). The editor's crop overlay and the render use the same maths (`clip-math.ts` mirrors `crop_logic.py`).

### D-063 — One caption template, two compilers, and a measured size factor — accepted
`CaptionStyle` (pydantic, unknown keys rejected, lengths as a percentage of output height, fonts limited to Liberation Sans/Serif and
DejaVu Sans/Mono) compiles to CSS for the live overlay and to ASS for the render. libass sizes a font by a different rule than CSS, so
`ASS_SIZE_FACTOR` converts one to the other. **Measured 2026-11-27** from the ink width of bold "HHHH" at 200 px against Chromium:
Liberation Sans 1.12, Liberation Serif 1.108, DejaVu Sans 1.167, DejaVu Sans Mono 1.169. First measured with ffmpeg 6.1.1; **re-measured
inside the real worker image (Debian bookworm, ffmpeg 5.1.9, libass 0.17.1): 1.120, 1.110, 1.165, 1.169, all within 0.15% of the table.**
An integration test re-measures libass on whatever machine runs it, so a different libass or font version fails loudly instead of
making the render's captions a different size from the preview. **Limit:** the CSS overlay is an approximation (line breaking and
karaoke fill differ slightly); the editor also offers the server-compiled preview (`/clips/{id}/preview-style`) which is the same code
as the render. Not yet verified: libass behaviour of `BackColour` with `BorderStyle 3` (boxed template) was not compared pixel for pixel.

### D-064 — Platform text limits, verified 2026-11-27 from secondary sources only — accepted, needs a human re-check
Google and Meta's own help pages could not be retrieved here, so limits come from third-party summaries and **conflict**. Hard limits
(a save over them is refused; the model's answer schema carries them too): YouTube title 100, description 5000, tags 500 characters in
total; Instagram caption 2200 including hashtags; generic title 150, description 2000; cover-image words 40. **Hashtag counts are soft
warnings, never refusals**, because sources disagree: YouTube 15 (one summary says more than 60 are ignored), Instagram 30 (another
source says 5 is the new cap), generic 30. YouTube tags 500 vs 270 also conflicts; 500 is used because it is the widely quoted API
figure. A person should check the official pages before relying on any of these, and the numbers are data in `clip_metadata_logic.LIMITS`.

### D-065 — Render identity and reuse — accepted
A render is identified by `render_hash`: sha256 over the engine version, media hash, kind, range, aspect, crop, background, audio,
merged caption style, the transcript words in range and the captions flag. Asking again for the same thing returns the existing
render (HTTP 200, `reused`); `force` creates a new one with its own stage hashes. The job carries a full snapshot, so the stages are
pure functions of their parameters and never read mutable rows.

### D-066 — Quality checks, and what they do not do — accepted
Checks: picture and sound streams exist; output dimensions; duration within max(0.35 s, 2%); picture/sound length (0.15 s); loudness
within 2 LU of -14 (final only); black frames (warn at 10%, fail at 90%; ignored for audio-only clips; blackdetect `pic_th` 0.85
because burned-in captions otherwise hid an all-black picture, measured 19% before and fixed); silence; captions inside the frame;
caption timing against the words (80 ms). A **fail does not block the download**: the report is shown and the person decides. The
preview render runs structural checks only. These checks cannot judge whether a clip is good.

### D-067 — Loudness — accepted
Two-pass `loudnorm` to -14 LUFS integrated with a true-peak ceiling. Measured on the browser-test clip (generated speech): **-15.5
LUFS**, inside the 2 LU tolerance but not on target (the limiter and the short, uneven clip are the likely cause; not investigated).
The integration test on its own fixture landed within 1.5 LU. "Mute" is `volume=0` (a first version emitted `0dB`, which is unity,
and a real render measured it at -51.8 LUFS rather than silent).

### D-068 — Fonts come from Debian packages; licence review needed — OPEN QUESTION for the human
The image installs `fonts-liberation` 1:1.07.4 and `fonts-dejavu-core` 2.37 so libass can draw captions. Read from the image's own
copyright files (2026-11-27): **Liberation 1.07.4 is GPL-2 with a font exception** (not the OFL of Liberation 2.x): a document that
embeds the font is not itself covered by the GPL, and a redistribution of the font in a physical product must offer the source.
DejaVu is the Bitstream Vera licence (permissive, attribution required) with public-domain changes. Burned-in captions are pixels, not
an embedded font, so the exception most likely covers them, but that is a legal judgement this project has not had made. The fonts are
not in the git repository; anyone redistributing the *image* carries the font licences. Decision needed: keep, or switch to OFL-only
fonts (Liberation 2.x or another family) before the first release.

### D-069 — Browser tests raise the per-email login limit on their own throwaway stack — accepted
The product default stays 8 attempts per 5 minutes per email. The e2e suite signs in as one admin once per spec file and passed 8
with this milestone's new spec, so CI (and a local stack) sets `LOGIN_RATE_LIMIT_PER_EMAIL=100` for the test stack only.

### D-070 — Downloads are signed media URLs with an attachment header — accepted
Clip renders are served by the existing signed-token media endpoint; `?dl=1&name=...` adds `Content-Disposition: attachment` with a
sanitized file name, for `clip_render` only. Original uploads are still never served (403).
