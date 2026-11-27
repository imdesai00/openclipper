# OpenClipper

Self-hosted, open-source AI video clipping: long recordings → short captioned clips, fully local
(faster-whisper + Ollama) or with your own API keys, drivable from Claude via MCP.

> **Status: Milestone 4 of 11.** What works today: setup wizard, invite-only users, projects, resumable video
> uploads (multi-GB, survive dropped connections), direct video links, a browser-playable proxy, waveform and
> thumbnail, live progress, and an AI settings page (local models through the included Ollama, or your own API keys, with encrypted keys, usage and cost tracking), and **transcription** (local speech recognition with word timings, an editable transcript with version history, SRT/VTT/TXT export; audio-only files work too). **There is no highlight detection or clip rendering yet.**
> Progress and known gaps: [`docs/PROGRESS.md`](docs/PROGRESS.md).

## Quick start (CPU-only, no accounts needed)
```bash
git clone <this repo> && cd openclipper
make up            # creates .env with fresh secrets, builds, starts, waits until every service is healthy
```
Open <http://localhost:8080> and create the admin account. Back up `APP_ENCRYPTION_KEY` from `.env`.

For anything other than `localhost`, serve it over HTTPS and set `COOKIE_SECURE=true` in `.env`.
Behind a TLS-inspecting proxy, pass your CA to the build with `docker build --secret id=extra_ca,src=ca.pem`.

## AI models
Open **AI** in the sidebar, add *Ollama (included)*, download a model, and pick it for each job. Nothing is sent to an
online service unless you switch the mode away from *Local only*. Hosted providers (OpenRouter, OpenAI, Anthropic, Gemini,
Groq, or any OpenAI-compatible server) need your own API key; keys are stored encrypted and shown only as `sk-…abcd`.
Note that the hosted providers' adapters have not yet been run against the live services (see `docs/PROGRESS.md`).

## Transcription
Runs on this machine with faster-whisper in the `worker-asr` service; nothing is sent anywhere. Open **Settings**, pick a
model under *Speech recognition* and press Download (the first time only; `small`, the default, is about 465 MB). Then open
any video or audio file and press **Transcribe**. Re-requesting the same settings reuses the existing transcript, and so does
uploading the same file again.

Measured on a 4-core 2.1 GHz CPU (generated speech, so real voices will be harder): `tiny` 0.06, `base` 0.10 and `small` 0.21
seconds of processing per second of audio. Details and limits: [`docs/PROGRESS.md`](docs/PROGRESS.md). To measure your own
machine: `cd backend && PYTHONPATH=. uv run --extra asr python ../scripts/bench_asr.py`. No speaker labelling yet. GPU is
untested.

## Optional: add videos from YouTube links
Off by default. Start the extra service with `docker compose --profile ingest up -d`, then switch it on under
**Settings**. Read the warning there first: downloading from YouTube can break its terms of service. It works best
from a home internet connection; on a server you will usually hit YouTube's bot check. Uploading a file always works.

## Documentation
- Spec: [`docs/SPEC.md`](docs/SPEC.md) · Architecture: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Decisions and verified platform facts: [`docs/DECISIONS.md`](docs/DECISIONS.md)
- Security: [`SECURITY.md`](SECURITY.md) · Contributing: [`CONTRIBUTING.md`](CONTRIBUTING.md)
- Licenses: AGPL-3.0-only ([`LICENSE`](LICENSE)); dependencies in [`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md)

## Responsibility
OpenClipper is for footage you own or are licensed to use. Features that fetch media from links are optional,
off by default, and may violate a platform's terms of service; responsibility lies with the person using them.
