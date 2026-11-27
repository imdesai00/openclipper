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

### D-013 — Where things run (Q-12) — accepted — 2026-11-27
Cloud container (no Docker daemon, 4 CPU / 15 GB, Python 3.13): unit tests and integration tests for components that run natively (Postgres/Redis installed natively if possible, FFmpeg, faster-whisper tiny). The human runs the full `docker compose up` acceptance on their machine; results get recorded in PROGRESS.md. No claim of "compose is healthy" is made unless it was actually run.

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
