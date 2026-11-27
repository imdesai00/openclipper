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

