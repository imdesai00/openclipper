# Progress

| # | Milestone | Status | Notes |
|---|---|---|---|
| 0 | Discovery & architecture | **done** (approved) | Open: local-LLM benchmark not run (D-005); goal question Q-13 unanswered (D-015) |
| 1 | Skeleton, compose, DB, auth | **done** | all exit criteria verified, see below |
| 2 | Storage, uploads, proxy, optional ingest | **done, with one item not verifiable here** (see below) | |
| 3 | AI providers | **done, with five hosted providers unverified live** (see below) | |
| 4 | Transcription | **next** | |
| 5–11 | | not started | |

## Milestone 1 verification (2026-11-27)
Exit criteria (SPEC §15): "`docker compose up` is healthy on a clean machine; auth tests pass".

| Check | Result | How |
|---|---|---|
| Backend unit tests | **PASS** 11/11 | `pytest tests/unit` |
| Backend integration tests (real Postgres 16, Valkey 8, Celery worker; no mocks) | **PASS** 27/27 | `pytest tests/integration` |
| Backend `ruff`, `ruff format`, `mypy --strict` | **PASS** | |
| Frontend unit tests (Vitest) | **PASS** 16/16 | `ng test` |
| Frontend lint / production build | **PASS** | |
| `docker compose up -d --build --wait` from a clean volume set | **PASS**, 6 services healthy | in this cloud sandbox, using the sandbox build override (D-013) |
| Browser E2E (Playwright, real stack, real Chromium) | **PASS** 8/8 on a fresh stack | setup wizard, validation, login, session restore on reload, invite flow, role guard, deactivation, axe WCAG 2 A/AA in light + dark, zero CSP violations |
| Data persists across `compose down` / `up` | **PASS** | admin could log in after restart |
| Migrations downgrade to base and upgrade again | **PASS** | integration test |
| Every foreign key has an index | **PASS** | integration test queries the catalog |
| GitHub Actions CI on a clean runner (backend, frontend, compose + browser E2E) | **PASS**, all 3 jobs, commit 621e5ec | `docker compose up --wait` with no sandbox override, then the same 8 Playwright tests |
| Not done: load test, independent security review, key rotation (arrives with M3), IPv6 | n/a | |

### Bugs found by testing in this milestone (all fixed)
- nginx dropped CSP/X-Frame-Options on every page (location-level `add_header`), found by checking real response headers.
- A strict CSP would have blocked Angular's inline critical-CSS loader and left the app unstyled; found by reading the built HTML, then guarded by a Playwright test that checks computed styles and CSP violations.
- Invalid nginx regex (unquoted `{8}`) crashed the frontend container; caught by the compose health check.
- Two of my own tests passed for the wrong reason (cookie never sent); rewritten to assert the exact error code.
- An un-hashed file was served with a 1-year `immutable` cache header.

## Known issues / limitations
- E2E must run against a fresh database and (within 5 minutes) a fresh login rate-limit window; repeated runs on the same stack hit the rate limiter. Documented in CONTRIBUTING.
- A refresh-token race between two browser tabs can sign one tab out (no grace window, D-019).
- `/ws/jobs` is proxied by nginx but not implemented yet (arrives with M2).
- Sandbox-only: building images needs `docker-compose.sandbox.yml` (TLS-inspecting proxy). Not needed elsewhere.
- Valkey instead of Redis (D-016) awaits your review.

## Skipped tests
None skipped. No external-credential tests exist yet.


---

## Milestone 2 verification (2026-11-27)
Exit criteria (SPEC §15, as amended by D-010): *"A 1 GB upload produces a proxy that plays in the browser"*; the YouTube-link case
applies only when the optional `ingest` profile is on.

| Check | Result | How |
|---|---|---|
| Backend unit tests | **PASS** 143/143 | `pytest tests/unit` (storage, SSRF guard, ffprobe, ranges, URL/yt-dlp parsing, waveform) |
| Backend integration tests (real Postgres, Valkey, Celery worker, ffmpeg, uvicorn, SeaweedFS; no mocks) | **PASS** 83, 1 skipped (of 84) | `pytest tests/integration` |
| Storage contract, local backend | **PASS** | same suite |
| Storage contract, S3 backend vs real SeaweedFS 4.55 (incl. 70 MB multipart) | **PASS** locally and wired into CI | `TEST_S3_ENDPOINT=...`; reported **SKIPPED** when no S3 server is configured |
| Backend `ruff`, `ruff format`, `mypy --strict` | **PASS** | |
| Frontend unit tests (Vitest) | **PASS** 41 | `ng test` |
| Frontend lint, production build | **PASS** | main bundle 79 kB gzipped |
| Compose stack healthy from clean volumes (7 services + migrate) | **PASS** | here (sandbox build override) |
| Browser E2E (Playwright, real Chromium, real stack) | **PASS** 17/17 | includes axe WCAG A/AA scans, CSP-violation watch, WebSocket, delete confirmations |
| **1.1 GB upload through the UI, network cut mid-upload, automatic resume, 720p proxy plays in Chromium** | **PASS** | `E2E_LARGE=1`, 13 s end to end on this machine |
| Optional `ingest` profile builds, starts, reports a live heartbeat, self-updates yt-dlp into its volume | **PASS** | image has yt-dlp 2026.11.23 and Deno 2.1.4, runs as uid 10001 |
| **Real YouTube download** | **SKIPPED, NOT VERIFIED** | Sandbox: TLS interception (`CERTIFICATE_VERIFY_FAILED`). GitHub runner: reached YouTube, got its **bot check** for both test videos (a datacenter IP, as the spec predicted). In both places the failure is reported with an accurate, actionable message. Nobody has yet seen OpenClipper download a YouTube video successfully. Try it from a home connection and tell me. |
| GitHub Actions CI on a clean runner | **PASS**, all 3 jobs, commit d5fa5c4: backend, frontend, and compose + 17/17 browser tests including the 1.1 GB interrupted upload; the ingest image builds, goes healthy and reports a heartbeat | see Actions run 45951981047 |

### Bugs found by testing in this milestone (all fixed)
- **Every service was built as the optional ingest image** (it was the Dockerfile's last stage), so the API claimed a download service was running. Found by the browser test on the settings page. Compose now pins `target: runtime`.
- **Uploads failed permanently when the browser went offline**, although the UI promised they resume: tus-js-client refuses to retry while `navigator.onLine` is false. Now network errors are retried, the UI shows "Connection lost. Waiting to resume…" at once, and the upload continues by itself when the connection returns (verified with a real 1.1 GB upload).
- Waveform returned 2013 buckets for a 2000 target (decoded audio is slightly longer than the container says). Buckets are now merged to the target.
- yt-dlp errors were reported as "The download failed: null", and TLS failures were not recognised. Fixed and tested.
- yt-dlp's self-updater could not write uv's cache directory, so it never updated. Fixed (`UV_CACHE_DIR`).
- The S3 test fixture leaked one bucket per test; SeaweedFS ran out of volumes mid-run. One bucket per module now, deleted at the end.
- The delete dialogs were rendered (hidden) for viewers who cannot delete anything. Now only rendered when the action is allowed.
- Test-only mistakes caught on the way: a cookie that never reached the server made two tests pass for the wrong reason; wrong expectations for WebSocket handshake rejection, ffmpeg sine amplitude and metadata whitespace.

### What this means for the YouTube feature
The plumbing is real and tested (admin switch, daily limit, worker liveness, queue routing, error mapping, self-updating yt-dlp, DRM/channel/live refusal, isolated container). The **download itself is unproven** because both places I could test from are blocked. Treat link downloads as experimental until you have run one successfully on your own network.

### Not built in this milestone (documented, not hidden)
- PO-token sidecar and a user-supplied cookies file for yt-dlp (D-026).
- Per-process memory/CPU limits for ffmpeg beyond container limits (D-022).
- Audio-only sources (D-021, **your decision needed**, Q-14).
- Project member management has an API and tests but no screen yet.
- `S3` compose profile (the adapter exists and passes its contract tests; the SeaweedFS service is not in `docker-compose.yml` yet, and the app has not been run end to end with `STORAGE_BACKEND=s3`).
- Retention/cleanup of expired media (Milestone 10); `asset_leases` are written so it can respect them.

### Known issues / limitations
- A refresh-token race between two tabs can sign one tab out (D-019). Unchanged.
- Admin dashboards show counts only; no charts.
- The sandbox build override is still needed here for the TLS proxy and Docker Hub rate limits (D-013, D-028).

## Skipped tests (reported, never counted as passed)
| Test | Reason |
|---|---|
| Storage contract, S3 variant, when `TEST_S3_ENDPOINT` is unset | no S3-compatible server configured (runs in CI and in the local run above) |
| `test_large_object_uses_multipart...[local]` | multipart is specific to S3 |
| Real YouTube download | blocked by TLS interception in this sandbox |

---

## Milestone 3 verification (2026-11-27)
Exit criteria (SPEC §15): *"Local-only and remote-only test prompts both succeed; key never visible in any API response."*

| Check | Result | How |
|---|---|---|
| **Local-only prompt through the real route, real Ollama + real model** | **PASS** | `qwen2.5:0.5b` answers "pong"; tokens counted, cost 0, usage logged; also through the browser UI |
| **Remote-only prompt** | **PASS against a real HTTP server speaking the OpenAI protocol; NOT verified against any hosted service** | no API keys exist in this environment. Mode REMOTE/HYBRID/LOCAL enforcement, key transmission, retries, cost and failure handling are tested end to end against that server. The five hosted adapters are tested against fixtures only. |
| **Key never visible in any API response** | **PASS** | one test exercises every AI endpoint, the usage report, error responses, application logs, audit log and every AI table in the database, with a server that echoes the key back in its error text |
| Backend unit tests | **PASS** 180/180 (36 new: vault, adapters) | |
| Backend integration tests (real Postgres, Valkey, Celery, ffmpeg, uvicorn, SeaweedFS, Ollama) | **PASS** 115, **6 SKIPPED** (of 121) | |
| Frontend unit tests | **PASS** 52 (11 new) | |
| Browser E2E (real Chromium + real stack incl. Ollama) | **PASS** 23, 1 skipped (the 1 GB test needs `E2E_LARGE=1`) | adds: add Ollama, download a model with a progress bar, route a job, run a prompt, key never shown/stored/sent back, remove provider, axe WCAG scans |
| `ruff`, `ruff format`, `mypy --strict` | **PASS** | |
| Key rotation without decrypting; wrong key changes nothing | **PASS** | integration tests drive the real CLI |
| GitHub Actions CI on a clean runner (commit 594fc49, run 45980781914) | **PASS**, all 3 jobs | backend: 180 unit + 115 integration passed, 6 skipped as listed, real Ollama model used; browser: 24/24 passed including the AI flow and the 1 GB upload |

### Bugs found by testing in this milestone (all fixed)
- "Model rejects JSON-schema mode" was remembered per service instance, but a new instance is built per request, so it would never be remembered. Now process-wide.
- A dark-mode contrast failure in the browser test turned out to be a mid-animation sample (test timing), not a defect; the scan now waits for the 150 ms transition.
- Test mistakes caught on the way: a scripted reply that was not valid JSON made a repair test fail (correctly); ambiguous locators; a fixture that reset the database under a running app.

### Skipped tests (reported, never counted as passed)
| Test | Reason |
|---|---|
| `test_real_hosted_provider[openrouter, openai, anthropic, gemini, groq]` | no API keys. Set `TEST_<KIND>_API_KEY` and `TEST_<KIND>_MODEL` to run them |
| `test_large_object_uses_multipart...[local]` | S3-only behaviour |
| 1 GB browser upload | needs `E2E_LARGE=1` (runs in CI) |

### Not built / known gaps
- **Hosted providers are unproven live** (D-032). If you have a free OpenRouter or Gemini key, running the external tests is the single most useful check you can do.
- No **default local model** is recommended: the benchmark from D-005 is still not run. The 0.5B test model is far too small for highlight detection.
- Master key from a Docker secret file (`*_FILE`) is not supported yet (D-034).
- Spend caps (daily/monthly hard stop) belong to the automation milestone; the usage data they need is recorded now.
- GPU overlay for Ollama (`docker-compose.gpu.yml`) is not written yet.
- The price editor on the models table uses a browser prompt box (works, not pretty).
- Gemini structured output is prompt-based; native schema mode was not verified against the live API.
- No charts in the usage report, tables only.

