# Progress

| # | Milestone | Status | Notes |
|---|---|---|---|
| 0 | Discovery & architecture | **done** (approved) | Open: local-LLM benchmark not run (D-005); goal question Q-13 unanswered (D-015) |
| 1 | Skeleton, compose, DB, auth | **done** | all exit criteria verified, see below |
| 2 | Storage, uploads, proxy, optional ingest | **done, with one item not verifiable here** (see below) | |
| 3 | AI providers | **next** | |
| 4–11 | | not started | |

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
| **Real YouTube download** | **SKIPPED, not verified** | This sandbox's proxy intercepts TLS (`CERTIFICATE_VERIFY_FAILED`); yt-dlp uses its own certificate bundle. The failure is mapped to a clear message. CI runs `python -m app.adapters.sources.selfcheck` where it will be PASS, or SKIPPED if YouTube blocks the runner. |
| GitHub Actions CI | see the end of this file | |

### Bugs found by testing in this milestone (all fixed)
- **Every service was built as the optional ingest image** (it was the Dockerfile's last stage), so the API claimed a download service was running. Found by the browser test on the settings page. Compose now pins `target: runtime`.
- **Uploads failed permanently when the browser went offline**, although the UI promised they resume: tus-js-client refuses to retry while `navigator.onLine` is false. Now network errors are retried, the UI shows "Connection lost. Waiting to resume…" at once, and the upload continues by itself when the connection returns (verified with a real 1.1 GB upload).
- Waveform returned 2013 buckets for a 2000 target (decoded audio is slightly longer than the container says). Buckets are now merged to the target.
- yt-dlp errors were reported as "The download failed: null", and TLS failures were not recognised. Fixed and tested.
- yt-dlp's self-updater could not write uv's cache directory, so it never updated. Fixed (`UV_CACHE_DIR`).
- The S3 test fixture leaked one bucket per test; SeaweedFS ran out of volumes mid-run. One bucket per module now, deleted at the end.
- The delete dialogs were rendered (hidden) for viewers who cannot delete anything. Now only rendered when the action is allowed.
- Test-only mistakes caught on the way: a cookie that never reached the server made two tests pass for the wrong reason; wrong expectations for WebSocket handshake rejection, ffmpeg sine amplitude and metadata whitespace.

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
