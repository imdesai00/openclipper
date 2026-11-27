# Progress

| # | Milestone | Status | Notes |
|---|---|---|---|
| 0 | Discovery & architecture | **awaiting approval** | Docs written; benchmark + Q-13 outstanding. |
| 1 | Skeleton, compose, DB, auth | not started | |
| 2 | Storage, ingest, uploads, proxy | not started | |
| 3 | AI providers | not started | |
| 4 | Transcription | not started | |
| 5 | Highlights | not started | |
| 6 | Editor, captions, render (MVP) | not started | |
| 7 | Publishing | not started | |
| 8 | Automation | not started | |
| 9 | MCP | not started | |
| 10 | Retention, hardening, docs | not started | |
| 11 | Live | not started | |

**Next:** finish Milestone 0 after the human answers the open questions.

## Known environment constraints
- 2026-11-27: Agent cloud container has Docker CLI + Compose but **no running Docker daemon**; Python 3.13 (not 3.12), Node 22, ffmpeg, uv available; 4 CPU / 15 GB RAM. YouTube reachability from here not yet confirmed.

## Skipped tests
_None yet._

## 2026-11-27 update
- M0: decisions D-002…D-014 recorded; §3 claims verified via search (see DECISIONS); ERD, API plan, wireframes, test plan written.
- M0 still open: D-005 LLM benchmark (not run; cloud box is a poor place, 673 MB Ollama download was cancelled and removed), Q-13 goal question, human approval.
