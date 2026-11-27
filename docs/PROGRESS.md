# Progress

| # | Milestone | Status | Notes |
|---|---|---|---|
| 0 | Discovery & architecture | **done** (approved) | Open: local-LLM benchmark not run (D-005); goal question Q-13 unanswered (D-015) |
| 1 | Skeleton, compose, DB, auth | **done** | all exit criteria verified, see below |
| 2 | Storage, uploads, proxy, optional ingest | **next** | |
| 3–11 | | not started | |

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
