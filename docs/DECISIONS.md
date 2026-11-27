# Decisions log

Append-only ADR log. Format: `D-NNN — title — status (proposed | accepted | superseded by D-xxx) — date`.
Open questions are `Q-NN` until the human answers; the answer becomes a `D-NNN`.

---

## Open questions (Milestone 0) — awaiting human

| ID | Question | Recommendation | Answer |
|---|---|---|---|
| Q-01 | Project license | AGPL-3.0 (stops closed SaaS forks; matches self-hosted positioning) | ⏳ |
| Q-02 | Tenancy | Small team, invite-only, roles admin/editor/viewer (spec default) | ⏳ |
| Q-03 | Minimum hardware for CPU-only promise | 4 cores / 16 GB RAM / 50 GB disk; whisper `small` int8; LLM picked by Q-04 | ⏳ |
| Q-04 | Default local LLM | Benchmark 3 candidates (≈3–8B, Ollama) on generated test set in M0 spike | ⏳ |
| Q-05 | Self-hosted S3 for `s3` profile | SeaweedFS (Apache-2.0) over Garage (AGPL) — verify maintenance status | ⏳ |
| Q-06 | Notification channels beyond in-app | Webhook in V1; SMTP email optional | ⏳ |
| Q-07 | Primary remote MCP exposure | Cloudflare Tunnel (free, no open ports); document others | ⏳ |
| Q-08 | Telemetry | None | ⏳ |
| Q-09 | yt-dlp ingestion placement (core vs optional profile) given YouTube API audit risk | Ship as optional `ingest` profile, off by default; upload is the default path | ⏳ |
| Q-10 | Frontend framework | Keep Angular only if the human knows it; otherwise reconsider | ⏳ |
| Q-11 | Who runs the external tests (YouTube/IG/OpenRouter accounts) | Human supplies test accounts later; SKIPPED until then | ⏳ |
| Q-12 | Where builds/tests run (this cloud container has no Docker daemon and likely no YouTube access) | Develop + unit/integration here; compose acceptance on human's machine | ⏳ |

## External-fact verification (SPEC §3) — pending

Each row must be checked against official docs during M0 with the date recorded. Status: `UNVERIFIED` until then.

| Claim | Source to check | Status | Date | Notes |
|---|---|---|---|---|
| YouTube Data API has no media download | developers.google.com/youtube/v3 | UNVERIFIED | | |
| Unverified API projects: uploads locked private until audit | YouTube API docs, videos.insert | UNVERIFIED | | |
| YouTube API default quota + videos.insert cost | YouTube quota docs | UNVERIFIED | | |
| YouTube API developer policies vs apps that download content (audit risk) | YouTube API Services Developer Policies | UNVERIFIED | | Drives Q-09 |
| IG Reels resumable upload via rupload.facebook.com, no public URL needed | Meta Instagram Platform docs | UNVERIFIED | | |
| IG `content_publishing_limit` endpoint, 24h container expiry | Meta docs | UNVERIFIED | | |
| IG dev-mode app usable by app's own testers without App Review | Meta docs | UNVERIFIED | | Matters for self-hosters |
| MinIO community edition maintenance mode | MinIO GitHub/announcements | UNVERIFIED | | |
| pyannote gated models + HF token requirement | Hugging Face model cards | UNVERIFIED | | |
| yt-dlp current requirements (Deno/JS runtime, PO tokens) | yt-dlp wiki | UNVERIFIED | | |
| Claude remote MCP connector: transport, OAuth, plan availability | Anthropic docs | UNVERIFIED | | |
| Angular current LTS version | angular.dev/reference/releases | UNVERIFIED | | |

---

## Accepted decisions

### D-001 — Repo is the source of truth; spec stored at docs/SPEC.md — accepted — 2026-11-27
Copied the v2 spec verbatim into `docs/SPEC.md`. Agent rules in `/CLAUDE.md`.
