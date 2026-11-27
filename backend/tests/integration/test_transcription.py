"""Real speech recognition: espeak-ng speaks known sentences, the real `tiny` model transcribes them in a real Celery worker.

SKIPPED (never passed) when faster-whisper, espeak-ng or the model download is unavailable.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import ProcessingJob, Transcript
from app.pipeline.registry import TRANSCRIBE
from app.services.transcript_logic import norm
from tests.helpers import API, invite_user, make_project, setup_admin, tus_upload, wait_source

pytestmark = pytest.mark.integration

ASR_KEY = "asr:heartbeat"
PHRASES = [
    "The quick brown fox jumps over the lazy dog near the river bank.",
    "Artificial intelligence is changing how people edit video and make short clips.",
    "Please remember to subscribe, and thank you for watching this episode.",
]
GAP_S = 3.0


# ------------------------------------------------------------------------------------------------ prerequisites
def _need() -> None:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        pytest.skip("SKIPPED: faster-whisper is not installed (uv sync --extra asr)")
    if not shutil.which("espeak-ng"):
        pytest.skip("SKIPPED: espeak-ng is not installed (it generates the spoken test audio)")


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    return float(out.stdout.strip())


def _speak(text: str, out: Path, voice: str = "en") -> None:
    subprocess.run(["espeak-ng", "-v", voice, "-s", "150", "-w", str(out), text], check=True)


@pytest.fixture(scope="session")
def speech(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """speech.wav: three spoken sentences separated by 3 s of silence, with the true start/end of each."""
    _need()
    d = tmp_path_factory.mktemp("speech")
    parts: list[Path] = []
    spans: list[tuple[float, float]] = []
    t = 0.0
    for i, text in enumerate(PHRASES):
        p = d / f"p{i}.wav"
        _speak(text, p)
        dur = _duration(p)
        spans.append((t, t + dur))
        t += dur + GAP_S
        parts.append(p)
    gap = d / "gap.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono",
                    "-t", str(GAP_S), str(gap)], check=True)  # fmt: skip
    lst = d / "list.txt"
    seq: list[Path] = []
    for i, p in enumerate(parts):
        seq += [p] + ([gap] if i < len(parts) - 1 else [])
    lst.write_text("".join(f"file '{p}'\n" for p in seq))
    out = d / "speech.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-ar", "22050",
                    "-ac", "1", str(out)], check=True)  # fmt: skip
    return {"path": out, "spans": spans, "text": " ".join(PHRASES)}


@pytest.fixture(scope="session")
def spanish(tmp_path_factory: pytest.TempPathFactory) -> Path:
    _need()
    out = tmp_path_factory.mktemp("es") / "hola.wav"
    _speak("Hola, esto es una prueba del reconocimiento de voz en español. Muchas gracias por escuchar.", out, "es")
    return out


@pytest.fixture(scope="session")
def asr_worker(media_worker):  # type: ignore[no-untyped-def]
    """The session test worker also serves the `asr` queue. Here the model is downloaded once into the shared test cache."""
    _need()
    import app.tasks.asr  # noqa: F401  registers asr.download_model
    from app.adapters.asr.faster_whisper import FasterWhisperEngine
    from app.ports.asr import ASRError

    try:
        FasterWhisperEngine(get_settings()).ensure_model("tiny", lambda *_: None)
    except ASRError as exc:
        pytest.skip(f"SKIPPED: could not download the tiny speech model ({exc.code})")
    return media_worker


def _redis_url() -> str:
    from tests.conftest import REDIS_URL

    return REDIS_URL


async def _alive(live) -> None:  # type: ignore[no-untyped-def]
    """What worker-asr's heartbeat does (see test_heartbeat_reports_device_and_downloaded_models)."""
    await live.app.state.redis.set(
        ASR_KEY, json.dumps({"device": "cpu", "compute": "int8", "downloaded": ["tiny"]}), ex=600
    )


@pytest.fixture
async def env(live, media_worker, asr_worker):  # type: ignore[no-untyped-def]
    await _alive(live)
    async with httpx.AsyncClient(base_url=live.url, timeout=120) as c:
        admin = await setup_admin(c)
        pid = await make_project(c, admin)
        yield c, admin, pid, live


# ------------------------------------------------------------------------------------------------ helpers
def wer(ref: str, hyp: str) -> float:
    r, h = [norm(x) for x in ref.split() if norm(x)], [norm(x) for x in hyp.split() if norm(x)]
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / len(r)


async def wait_tjob(c: httpx.AsyncClient, h: dict[str, str], sid: str, timeout: float = 240) -> dict[str, Any]:
    end = time.monotonic() + timeout
    while True:
        src = (await c.get(f"{API}/sources/{sid}", headers=h)).json()
        tj = src.get("transcription_job")
        if tj and tj["status"] in ("succeeded", "failed", "cancelled"):
            return src
        assert time.monotonic() < end, f"transcription did not finish: {tj}"
        await asyncio.sleep(0.5)


async def transcribe(c, h, sid, **body):  # type: ignore[no-untyped-def]
    return await c.post(f"{API}/sources/{sid}/transcribe", headers=h, json=body)


async def full(c, h, sid, **params):  # type: ignore[no-untyped-def]
    r = await c.get(f"{API}/sources/{sid}/transcript", headers=h, params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def count(live, model, *where):  # type: ignore[no-untyped-def]
    async with live.app.state.sessionmaker() as s:
        return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


# ------------------------------------------------------------------------------------------------ tests
async def test_real_transcription_is_accurate_with_correct_word_timings(env, speech) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await tus_upload(c, h, pid, speech["path"])
    src = await wait_source(c, h, sid)
    assert src["status"] == "ready" and src["has_video"] is False and src["has_audio"] is True  # audio-only is a source

    r = await transcribe(c, h, sid)
    assert r.status_code == 202 and r.json()["reused"] is False
    src = await wait_tjob(c, h, sid)
    tj = src["transcription_job"]
    assert tj["status"] == "succeeded", tj
    assert [s["stage"] for s in tj["stages"]] == ["extract_audio", "asr_prep", "transcribe"]
    assert src["status"] == "ready"  # transcribing never changes the source's own status

    t = await full(c, h, sid)
    meta = t["transcript"]
    assert (meta["version"], meta["kind"], meta["is_current"], meta["model"]) == (1, "asr", True, "tiny")
    assert meta["language"] == "en" and meta["language_probability"] > 0.8
    text = " ".join(s["text"].strip() for s in t["segments"])
    score = wer(speech["text"], text)
    print(f"\nMEASURED tiny WER={score:.3f} on {len(speech['text'].split())} words: {text!r}")
    assert score <= 0.30, text

    words = [w for s in t["segments"] for w in s["words"]]
    assert meta["word_count"] == len(words) > 25
    assert all(w["start_ms"] <= w["end_ms"] for w in words)
    assert all(a["end_ms"] <= b["start_ms"] + 1 for a, b in zip(words, words[1:], strict=False))
    # word timings land where the speech really is (ground truth from the generated audio)
    for i, (start, end) in enumerate(speech["spans"]):
        inside = [w for w in words if start - 0.5 <= w["start_ms"] / 1000 <= end + 0.5]
        assert inside, f"no words near phrase {i}"
        d0, d1 = inside[0]["start_ms"] / 1000 - start, inside[-1]["end_ms"] / 1000 - end
        print(f"MEASURED phrase {i}: first word start error {d0:+.2f}s, last word end error {d1:+.2f}s")
        assert abs(inside[0]["start_ms"] / 1000 - start) < 0.7, (i, inside[0], start)
        assert abs(inside[-1]["end_ms"] / 1000 - end) < 0.9, (i, inside[-1], end)
    gaps = [
        w
        for w in words
        if any(
            e + 0.4 < w["start_ms"] / 1000 < s2 - 0.4
            for (_, e), (s2, _) in zip(speech["spans"], speech["spans"][1:], strict=False)
        )
    ]
    assert not gaps, f"words were placed inside silence: {gaps}"  # fmt: skip


async def test_second_request_reuses_the_cache_and_force_recognises_again(env, speech) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await tus_upload(c, h, pid, speech["path"])
    await wait_source(c, h, sid)
    assert (await transcribe(c, h, sid)).status_code == 202
    await wait_tjob(c, h, sid)
    first = (await full(c, h, sid))["transcript"]

    again = await transcribe(c, h, sid)
    assert again.status_code == 200 and again.json()["reused"] is True and again.json()["job"] is None
    assert again.json()["transcript"]["id"] == first["id"]
    assert await count(live, ProcessingJob, ProcessingJob.kind == TRANSCRIBE) == 1  # no second job was created
    assert await count(live, Transcript) == 1

    # the same file uploaded again (another source) is served from cache, even with no ASR worker alive
    sid2 = await tus_upload(c, h, pid, speech["path"], filename="copy.wav")
    await wait_source(c, h, sid2)
    await live.app.state.redis.delete(ASR_KEY)
    clone = await transcribe(c, h, sid2)
    assert clone.status_code == 200 and clone.json()["reused"] is True, clone.text
    t2 = await full(c, h, sid2)
    assert t2["transcript"]["id"] != first["id"] and t2["transcript"]["source_id"] == sid2
    assert [s["text"] for s in t2["segments"]] == [s["text"] for s in (await full(c, h, sid))["segments"]]
    assert await count(live, ProcessingJob, ProcessingJob.kind == TRANSCRIBE) == 1

    await _alive(live)
    forced = await transcribe(c, h, sid, force=True)
    assert forced.status_code == 202
    await wait_tjob(c, h, sid)
    cur = (await full(c, h, sid))["transcript"]
    assert cur["version"] == 2 and cur["kind"] == "asr" and cur["id"] != first["id"]
    assert await count(live, ProcessingJob, ProcessingJob.kind == TRANSCRIBE) == 2
    versions = (await c.get(f"{API}/sources/{sid}/transcript/versions", headers=h)).json()
    assert [(v["version"], v["is_current"]) for v in versions] == [(2, True), (1, False)] or [
        (v["version"], v["is_current"]) for v in versions
    ] == [(1, False), (2, True)]


async def test_edits_keep_word_timings_create_versions_and_never_run_recognition(env, speech) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await tus_upload(c, h, pid, speech["path"])
    await wait_source(c, h, sid)
    await transcribe(c, h, sid)
    await wait_tjob(c, h, sid)
    v1 = await full(c, h, sid)
    seg = v1["segments"][0]
    old_words = seg["words"]
    new_text = seg["text"].strip().replace(old_words[1]["text"].strip(" ,."), "slow", 1)

    r = await c.patch(
        f"{API}/sources/{sid}/transcript",
        headers=h,
        json={"base_version": 1, "edits": [{"segment_idx": 0, "text": new_text}]},
    )
    assert r.status_code == 200, r.text  # fmt: skip
    assert r.json()["version"] == 2 and r.json()["kind"] == "edit" and r.json()["based_on_version"] == 1
    v2 = await full(c, h, sid)
    s0, s1 = v2["segments"][0], v2["segments"][1]
    assert s0["text"].strip() == new_text and s0["edited"] is True and s1["edited"] is False
    assert [(w["start_ms"], w["end_ms"]) for w in s0["words"]][0] == (old_words[0]["start_ms"], old_words[0]["end_ms"])
    assert [(w["start_ms"], w["end_ms"]) for w in s0["words"]][2:] == [
        (w["start_ms"], w["end_ms"]) for w in old_words[2:]
    ]
    assert [w["text"] for w in s1["words"]] == [w["text"] for w in v1["segments"][1]["words"]]  # untouched segment
    assert await count(live, ProcessingJob, ProcessingJob.kind == TRANSCRIBE) == 1  # editing never re-runs ASR

    # optimistic concurrency: an editor holding a stale version is told, nothing is overwritten
    stale = await c.patch(
        f"{API}/sources/{sid}/transcript",
        headers=h,
        json={"base_version": 1, "edits": [{"segment_idx": 1, "text": "nope"}]},
    )
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"  # fmt: skip
    assert (await full(c, h, sid))["transcript"]["version"] == 2
    bad = await c.patch(
        f"{API}/sources/{sid}/transcript",
        headers=h,
        json={"base_version": 2, "edits": [{"segment_idx": 999, "text": "x"}]},
    )
    assert bad.status_code in (400, 404, 422)  # fmt: skip
    dup = await c.patch(
        f"{API}/sources/{sid}/transcript",
        headers=h,
        json={"base_version": 2, "edits": [{"segment_idx": 0, "text": "a"}, {"segment_idx": 0, "text": "b"}]},
    )
    assert dup.status_code == 400  # fmt: skip

    # restore brings back the original wording as a NEW version; history is never rewritten
    rs = await c.post(f"{API}/sources/{sid}/transcript/restore", headers=h, json={"version": 1})
    assert rs.status_code == 201 and rs.json()["version"] == 3 and rs.json()["kind"] == "restore"
    v3 = await full(c, h, sid)
    assert [s["text"] for s in v3["segments"]] == [s["text"] for s in v1["segments"]]
    old = await full(c, h, sid, version=2)
    assert old["segments"][0]["text"].strip() == new_text  # version 2 is still readable

    # exports follow the current version
    srt = await c.get(f"{API}/sources/{sid}/transcript/export", headers=h, params={"format": "srt"})
    assert srt.status_code == 200 and srt.text.startswith("1\n00:00:") and "-->" in srt.text
    vtt = await c.get(f"{API}/sources/{sid}/transcript/export", headers=h, params={"format": "vtt", "version": 2})
    assert vtt.text.startswith("WEBVTT") and "slow" in vtt.text
    txt = await c.get(f"{API}/sources/{sid}/transcript/export", headers=h, params={"format": "txt"})
    assert txt.text.strip().split("\n")[0].strip() == v1["segments"][0]["text"].strip()
    assert (
        await c.get(f"{API}/sources/{sid}/transcript/export", headers=h, params={"format": "doc"})
    ).status_code == 400


async def test_explicit_language_is_honoured_for_non_english_speech(env, spanish) -> None:  # type: ignore[no-untyped-def]
    """Auto-detection is proven on English above. `tiny` misdetects espeak's robotic Spanish (measured: Italian, p=0.57),
    so this test checks what we control: a requested language is passed through and recorded, and the text is Spanish."""
    c, h, pid, live = env
    sid = await tus_upload(c, h, pid, spanish)
    await wait_source(c, h, sid)
    assert (await transcribe(c, h, sid, language="es")).status_code == 202
    src = await wait_tjob(c, h, sid)
    assert src["transcription_job"]["status"] == "succeeded", src["transcription_job"]
    t = await full(c, h, sid)
    text = " ".join(s["text"] for s in t["segments"]).lower()
    print(f"\nMEASURED es (forced) text: {text!r}")
    assert t["transcript"]["language"] == "es" and t["transcript"]["requested_language"] == "es"
    assert any(w in text for w in ("hola", "prueba", "gracias", "voz")), text


async def test_requests_are_validated_and_authorised(env, speech, media_dir: Path) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await tus_upload(c, h, pid, speech["path"])
    await wait_source(c, h, sid)
    assert (await c.post(f"{API}/sources/{sid}/transcribe", json={})).status_code == 401
    assert (await transcribe(c, h, sid, language="klingon")).status_code == 400
    assert (await transcribe(c, h, sid, model="gigantic")).status_code == 400
    mism = await transcribe(c, h, sid, model="tiny.en", language="es")
    assert mism.status_code == 400 and mism.json()["code"] == "model_language_mismatch"
    assert (await transcribe(c, h, str(uuid.uuid4()))).status_code == 404

    other = await invite_user(c, h, "other@example.com")  # a user who is not in the project
    assert (await transcribe(c, other, sid)).status_code in (403, 404)
    assert (await c.get(f"{API}/sources/{sid}/transcript", headers=other)).status_code in (403, 404)
    assert (await c.get(f"{API}/sources/{sid}/transcript", headers=h)).status_code == 404  # none yet

    silent = await wait_source(c, h, await tus_upload(c, h, pid, media_dir / "hd_silent.mp4"))
    no_audio = await transcribe(c, h, silent["id"])
    assert no_audio.status_code == 400 and no_audio.json()["code"] == "no_audio"

    await live.app.state.redis.delete(ASR_KEY)  # the worker-asr service is not running
    down = await transcribe(c, h, sid)
    assert down.status_code == 503 and down.json()["code"] == "asr_unavailable"
    assert await count(live, ProcessingJob, ProcessingJob.kind == TRANSCRIBE) == 0


async def test_audio_without_speech_fails_visibly_and_leaves_the_source_alone(env, small_mp4: Path) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await tus_upload(c, h, pid, small_mp4)  # 3 s sine tone, no speech
    await wait_source(c, h, sid)
    assert (await transcribe(c, h, sid)).status_code == 202
    src = await wait_tjob(c, h, sid)
    tj = src["transcription_job"]
    assert tj["status"] == "failed" and tj["error_code"] == "no_speech", tj
    assert src["status"] == "ready" and src["error_code"] is None  # the video itself is still fine
    assert await count(live, Transcript) == 0  # no fake empty transcript was stored


async def test_heartbeat_reports_device_and_downloaded_models(env) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    from redis import Redis

    from app.adapters.asr.faster_whisper import FasterWhisperEngine
    from app.tasks.asr_heartbeat import beat_once

    r = Redis.from_url(_redis_url())
    try:
        await live.app.state.redis.delete(ASR_KEY)
        assert (await c.get(f"{API}/asr/status", headers=h)).json()["available"] is False
        beat_once(r, FasterWhisperEngine(get_settings()))
    finally:
        r.close()
    st = (await c.get(f"{API}/asr/status", headers=h)).json()
    assert st["available"] is True and st["device"] == "cpu" and st["default_model"]
    models = {m["name"]: m for m in st["models"]}
    assert models["tiny"]["downloaded"] is True and models["large-v3"]["downloaded"] is False


async def test_admin_can_change_defaults_and_trigger_a_model_download(env) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    editor = await invite_user(c, h, "ed@example.com")
    assert (
        await c.put(f"{API}/asr/settings", headers=editor, json={"model": "tiny", "language": None})
    ).status_code == 403
    ok = await c.put(f"{API}/asr/settings", headers=h, json={"model": "base", "language": "es"})
    assert ok.status_code == 200, ok.text
    st = (await c.get(f"{API}/asr/status", headers=h)).json()
    assert (st["default_model"], st["default_language"]) == ("base", "es")
    assert (await c.put(f"{API}/asr/settings", headers=h, json={"model": "nope", "language": None})).status_code == 400

    assert (await c.post(f"{API}/asr/models/nope/download", headers=h)).status_code == 400
    assert (await c.post(f"{API}/asr/models/tiny/download", headers=editor)).status_code == 403
    assert (await c.post(f"{API}/asr/models/tiny/download", headers=h)).status_code == 202
    end = time.monotonic() + 60
    while True:
        p = (await c.get(f"{API}/asr/models/tiny/progress", headers=h)).json()
        if p.get("done"):
            break
        assert time.monotonic() < end, p
        await asyncio.sleep(0.3)
    assert p["error"] is None and p["percent"] == 100
