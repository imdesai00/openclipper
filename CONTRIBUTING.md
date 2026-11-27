# Contributing

Read `CLAUDE.md` (the working rules, including the honesty rules) and `docs/SPEC.md` first.
The project is built one milestone at a time; `docs/PROGRESS.md` says which one is next.

## Development setup
Requirements: Docker + Compose, Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22.

```bash
# backend tests need a real Postgres and Valkey/Redis (no mocks in the integration tier) and ffmpeg on PATH
# optional: a real S3-compatible server for the storage contract tests (otherwise those are reported SKIPPED)
#   docker run -d --name oc-s3 -p 58333:8333 chrislusf/seaweedfs:4.55 server -s3 -dir=/data
#   export TEST_S3_ENDPOINT=http://127.0.0.1:58333
docker run -d --name oc-pg -e POSTGRES_PASSWORD=dev -p 55432:5432 postgres:16-alpine
docker run -d --name oc-redis -p 56379:6379 valkey/valkey:8-alpine
cd backend && uv sync && uv run pytest -q
```

```bash
cd frontend && npm ci && npm run lint && npx ng test --watch=false && npm run build
```

```bash
# full stack + browser tests (needs a FRESH database: the first test is the setup wizard)
make up
cd e2e && npm ci && npx playwright install chromium && E2E_BASE_URL=http://127.0.0.1:8080 npx playwright test
```

Tests that need third-party credentials must be marked `external` and reported as SKIPPED without them,
never as passed. Run `make lint` before committing. Adding a dependency means checking its license and
re-running `python3 scripts/gen_licenses.py`.

By contributing you agree your work is licensed under AGPL-3.0-only.
