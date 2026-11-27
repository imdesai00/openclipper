# Acceptance test plan

Tiers per SPEC §11. Results are PASS / FAIL / SKIPPED(reason). SKIPPED is never PASS.

## Test footage (generated in CI)
- FFmpeg `testsrc2` video + TTS speech (espeak-ng/Piper) of known script → ground-truth text for WER.
- A 3-minute script with 4 planted "highlight-worthy" passages at known timestamps; a 2-hour version made by concatenation for chunking tests.

## Per-milestone gates
| M | Must pass (no mocks in integration) |
|---|---|
| 1 | Compose-up health check (run by human if no Docker here — recorded in PROGRESS); setup wizard once only; login, refresh rotation + reuse detection; role checks; Alembic up/down |
| 2 | tus upload resume after interrupt; 1 GB upload → ffprobe-valid proxy; reject non-media + oversize; SSRF guard (private/loopback/metadata/redirect-to-private); WS progress; source attestation required |
| 3 | Encryption roundtrip + key rotation; app refuses to start without key; API never returns key; Ollama + OpenAI-compatible prompt tests; fallback chain; circuit breaker |
| 4 | Real faster-whisper (tiny) WER ≤ threshold on TTS footage; cache hit on re-run; transcript edit → new version without ASR |
| 5 | Every candidate's words exist in transcript; no invented timestamps; invalid JSON → ≤2 repairs → visible failure; chunking on 2h transcript; works with local model |
| 6 | ffprobe on render: codec/resolution/duration; CSS-vs-ASS template golden test; loudnorm within tolerance; QC flags silent/black footage; Playwright: full flow upload→download |
| 7 | State machine unit tests with recorded fixtures; external tests SKIPPED without credentials |
| 8 | Spend cap halts run; pause-all; duplicate prevention |
| 9 | MCP scope enforcement; confirmation token expiry/reuse; audit log |
| 10 | Retention dry-run, lease protection, orphan sweep; backup→restore; restart persistence |
| 11 | Live-link and RTMP clip while stream runs |

## Core acceptance (SPEC §16.A, 16 steps) is the M6 gate; steps 14–16 close at M10/M9.
