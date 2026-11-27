# CLAUDE.md — Working rules for OpenClipper

This file is loaded into every agent session. It wins over chat history. The full product spec is `docs/SPEC.md`.

## Source of truth
- `docs/SPEC.md`: what we are building (do not edit without a human decision recorded in DECISIONS.md).
- `docs/ARCHITECTURE.md`: how it is built. Change it in the same commit as the code that changes the design.
- `docs/DECISIONS.md`: every decision, deviation from the spec, and dated verification of external facts (ADR style, append-only; supersede, never rewrite).
- `docs/PROGRESS.md`: milestone status, done / partial / not started / known issues / skipped tests with reasons.
- Never rely on chat history. If it matters, it goes into one of these files.

## Session protocol
1. Read SPEC §0, ARCHITECTURE, DECISIONS, PROGRESS before touching code.
2. Work on **exactly one milestone** per session (the one PROGRESS.md marks as next). Do not start the next one.
3. A milestone is done only when its SPEC §15 exit criteria pass. Run all tests before claiming it.
4. End every session by updating PROGRESS.md (and DECISIONS.md if anything was decided).
5. If a requirement can't be met through supported, official means, stop, write it in DECISIONS.md as an open question, and ask the human.

## Honesty rules (non-negotiable, from SPEC §13)
- No fake responses, metrics, previews, or "success" in production code paths.
- No "published" status without platform confirmation.
- Skipped / credential-gated / mocked-in-integration tests are reported as **SKIPPED** with a reason, never PASSED.
- A control that doesn't work is hidden and listed in PROGRESS.md as unimplemented.
- Never quote performance numbers that were not measured in this repo.

## Architecture rules
- Hexagonal: domain + application services never import platform SDKs. Adapters implement ports in `backend/app/adapters/`.
- One service layer: REST routers, Celery tasks and the MCP server are thin; all logic lives in `backend/app/services/`.
- No heavy work in HTTP handlers: enqueue, return `job_id`.
- Pipeline stages are idempotent, keyed by `(source_id, stage, params_hash)`, checkpointed, and each in its own module + task + tests. No monolithic processing file.
- Config over code: model IDs, weights, limits, prices, templates are data (DB `settings` / config files), never constants.
- No binary media in Postgres. Storage keys only.
- One caption template schema → compiled to CSS (preview) and ASS (render).

## Security rules
- Secrets never reach the browser, logs, images or git. API returns masked values only.
- Credentials encrypted with AES-GCM envelope encryption (`APP_ENCRYPTION_KEY`); app refuses to start without it.
- FFmpeg/ffprobe/yt-dlp invoked with argument lists, never shell strings; always with timeouts.
- No user-controlled filesystem paths. Randomized storage keys.
- Direct URLs go through the SSRF guard (block private/loopback/link-local/metadata, re-resolve on every redirect).
- yt-dlp never touches YouTube OAuth tokens or API credentials used for publishing.

## Code conventions
- Backend: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic. `ruff` (lint + format), `mypy --strict` on `app/`. `pytest` with markers: `unit`, `integration`, `external`, `e2e`.
- Frontend: Angular (current LTS), standalone components, Signals, strict TS, Tailwind, feature folders. ESLint + Prettier.
- Migrations: Alembic only; never edit an applied migration.
- UUIDv7 primary keys; every FK indexed with explicit `ON DELETE`.
- Pin dependency versions. Adding a dependency = check its license and add it to `THIRD_PARTY_LICENSES.md`.

## Git
- Small, focused commits with clear messages. Never commit `.env`, keys, cookies files, model weights or media.
- Don't push or open PRs unless asked.
