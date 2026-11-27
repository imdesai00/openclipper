"""Highlight detection through the real pipeline: real Celery worker, real ffmpeg, real Postgres, and a real HTTP
server standing in for the model so replies can be scripted (including wrong ones).

What these prove is OUR side of the contract: a model can only point at segments, everything else is derived from the
stored transcript, and every failure is visible. They do not prove a model finds good highlights: the tests that
use a real local model are in test_highlights_local_model.py."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select

from app.db.models import HighlightCandidate, HighlightRun, MediaAsset, ProcessingJob, VideoSource
from app.ports.asr import SegmentData, TranscriptData, WordData
from app.services import transcripts
from tests.fake_llm import FakeLLM
from tests.helpers import (
    API,
    add_provider,
    discover,
    invite_user,
    make_project,
    route,
    setup_admin,
    tus_upload,
    wait_source,
)

pytestmark = pytest.mark.integration

LLM_KEY = "llm:heartbeat"
SENTENCES = [
    "Welcome back to the show everybody, today is special.",
    "The first surprising thing is that sleep matters more than diet.",
    "Researchers followed ten thousand people for twenty years.",
    "Those who slept badly had far worse health in the end.",
    "Now let me tell you a story about my grandmother.",
    "She never owned an alarm clock and woke every day at dawn.",
    "People thought she was lucky but it was simply routine.",
    "That is all for today, thank you so much for listening.",
]
WORDS_MS = 500
GAP_MS = 500


def _spoken() -> list[tuple[int, int, str, list[tuple[int, int, str]]]]:
    """(segment start, end, text, [(word start, end, word)]) with each sentence its own segment, 0.5 s per word."""
    out, t = [], 1000
    for s in SENTENCES:
        words, start = [], t
        for w in s.split():
            words.append((t, t + WORDS_MS, w))
            t += WORDS_MS
        out.append((start, t, s, words))
        t += GAP_MS
    return out


DURATION_S = 48


@pytest.fixture(scope="session")
def cut_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """48 s of video with HARD cuts at 16 s and 32 s (black, white, dark gray: ffmpeg's scene score is mostly luma, so cuts must differ in brightness) and a tone."""
    d = tmp_path_factory.mktemp("hl")
    out = d / "cuts.mp4"
    f = ["-f", "lavfi", "-i"]
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         *f, "color=c=black:s=320x240:r=25:d=16", *f, "color=c=white:s=320x240:r=25:d=16", *f, "color=c=0x404040:s=320x240:r=25:d=16",
         *f, f"sine=frequency=300:duration={DURATION_S}",
         "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0[v]", "-map", "[v]", "-map", "3:a",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)],
        check=True,
    )  # fmt: skip
    return out


@pytest.fixture(scope="session")
def tone_only(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("hla") / "podcast.m4a"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=300:duration={DURATION_S}",
         "-c:a", "aac", str(out)],
        check=True,
    )  # fmt: skip
    return out


@pytest.fixture
async def fake():  # type: ignore[no-untyped-def]
    f = FakeLLM()
    yield f
    f.close()


@pytest.fixture
async def env(live, media_worker, fake):  # type: ignore[no-untyped-def]
    await live.app.state.redis.set(LLM_KEY, "1", ex=600)
    async with httpx.AsyncClient(base_url=live.url, timeout=120) as c:
        admin = await setup_admin(c)
        p = await add_provider(c, admin, base_url=fake.url, name="fake", is_local=True)
        m = (await discover(c, admin, p["id"]))[0]
        await route(c, admin, "highlight", m["id"])
        pid = await make_project(c, admin)
        yield c, admin, pid, live, fake


async def ready_source(c, h, pid, live, path: Path, *, with_transcript: bool = True) -> str:  # type: ignore[no-untyped-def]
    sid = await tus_upload(c, h, pid, path)
    src = await wait_source(c, h, sid)
    assert src["status"] == "ready", src
    if with_transcript:
        await insert_transcript(live, sid)
    return sid


async def insert_transcript(live, sid: str) -> None:  # type: ignore[no-untyped-def]
    """Stored exactly as real recognition stores it (same persistence code), so only the words are synthetic."""
    from app.services.jobs import create_job

    data = TranscriptData(
        "en", 0.99, float(DURATION_S), "test", "synthetic",
        [SegmentData(s / 1000, e / 1000, text, [WordData(a / 1000, b / 1000, w) for a, b, w in words]) for s, e, text, words in _spoken()],
    )  # fmt: skip
    async with live.app.state.sessionmaker() as s:
        src = await s.get(VideoSource, uuid.UUID(sid))
        assert src
        job = await create_job(s, src, None, kind="transcribe_source", params={})
        await transcripts.persist_asr(s, source=src, job=job, data=data)
        await s.commit()


def reply(*cands: dict[str, Any]) -> tuple[int, Any, dict[str, str]]:
    return 200, FakeLLM.chat_body(json.dumps({"candidates": list(cands)})), {}


def cand(a: Any, b: Any, score: int = 80, **kw: Any) -> dict[str, Any]:
    return {"start_segment_id": a, "end_segment_id": b, "category": "education", "hook": f"hook {a}-{b}", "title": f"title {a}-{b}",
            "explanation": "because", "score": score, "standalone": 70, "confidence": 60, **kw}  # fmt: skip


async def find(c, h, sid, **body):  # type: ignore[no-untyped-def]
    return await c.post(
        f"{API}/sources/{sid}/highlights", headers=h, json={"min_seconds": 5, "max_seconds": 15, **body}
    )


async def wait_hjob(c, h, sid, timeout: float = 120) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    end = time.monotonic() + timeout
    while True:
        src = (await c.get(f"{API}/sources/{sid}", headers=h)).json()
        j = src.get("highlights_job")
        if j and j["status"] in ("succeeded", "failed", "cancelled"):
            return dict(src)
        assert time.monotonic() < end, f"highlight job did not finish: {j}"
        await asyncio.sleep(0.4)


async def get_run(c, h, sid, run_id):  # type: ignore[no-untyped-def]
    r = await c.get(f"{API}/sources/{sid}/highlights/{run_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def expect_text(a: int, b: int) -> str:
    return " ".join(SENTENCES[a : b + 1])


# ------------------------------------------------------------------------------------------------ tests
async def test_candidates_come_from_real_words_and_everything_else_is_dropped_visibly(env, cut_clip) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live, fake = env
    sid = await ready_source(c, h, pid, live, cut_clip)
    fake.default = reply(
        cand("s1", "s2", 90),  # good
        cand("s5", "s6", 80),  # good
        cand("s2", "s3", 40),  # overlaps the stronger first one
        cand("s99", "s100", 95),  # invented ids: the model was never shown these
        cand(4, 3, 85),  # reversed
        cand("s3", "s3", 20),  # 5.5 s: shorter than the 7 s asked for, so it grows over the next sentence
    )
    r = await find(c, h, sid, prompt="surprising facts", categories=["education"], min_seconds=7)
    assert r.status_code == 202 and r.json()["reused"] is False
    src = await wait_hjob(c, h, sid)
    assert src["highlights_job"]["status"] == "succeeded", src["highlights_job"]
    assert [s["stage"] for s in src["highlights_job"]["stages"]] == ["detect_scenes", "highlights"]
    assert src["status"] == "ready"  # finding highlights never changes the video's own status

    run = await get_run(c, h, sid, src["highlights"]["run_id"])
    cs = run["candidates"]
    assert [x["rank"] for x in cs] == [1, 2, 3]
    assert [x["title"] for x in cs] == ["title s1-s2", "title s5-s6", "title s3-s3"]
    # the text is the transcript's, word for word, whatever the model said
    assert (
        cs[0]["text"] == expect_text(1, 2) and cs[1]["text"] == expect_text(5, 6) and cs[2]["text"] == expect_text(3, 4)
    )
    spoken = _spoken()
    for x, (a, b) in zip(cs, [(1, 2), (5, 6), (3, 4)], strict=True):
        first_word, last_word = spoken[a][3][0], spoken[b][3][-1]
        assert (
            first_word[0] - 150 <= x["start_ms"] <= first_word[0] and last_word[1] <= x["end_ms"] <= last_word[1] + 250
        )
        assert x["duration_ms"] == x["end_ms"] - x["start_ms"] and 7000 <= x["duration_ms"] <= 15000 + 400
    stats = run["run"]["stats"]
    assert stats["dropped"] == {"unknown_segment": 1, "reversed": 1, "overlaps_stronger": 1}, stats
    assert (
        stats["raw_candidates"] == 6 and stats["kept"] == 3 and stats["windows"] == 1 and stats["windows_failed"] == []
    )
    assert run["score_note"] == "AI relevance score, not an engagement prediction."
    assert cs[0]["final_score"] >= cs[1]["final_score"] >= cs[2]["final_score"]
    assert (cs[0]["llm_score"], cs[0]["standalone_score"], cs[0]["confidence"]) == (90, 70, 60)
    assert cs[0]["provider_name"] == "fake" and cs[0]["status"] == "new"

    # What the model was shown: segment ids and the user's wish, never a request for timestamps
    sent = json.dumps(fake.chats()[0]["body"]["messages"])
    assert (
        f"[s1 00:{_spoken()[1][0] // 1000:02d}]" in sent and "surprising facts" in sent and "Never write times" in sent
    )

    # Scoring parts: every component is explained, and scene cuts were found in the real video
    parts = cs[0]["score_parts"]
    assert {"llm", "category", "standalone", "density", "energy"} <= set(parts)
    assert abs(sum(v["weight"] for k, v in parts.items() if k != "unavailable") - 1.0) < 0.02
    assert stats["scene_cuts"] == 2 and stats["audio_energy"] is True

    # Scene cuts really come from the picture: black -> white at 16 s, white -> dark gray at 32 s
    async with live.app.state.sessionmaker() as s:
        asset_id = (await s.get(VideoSource, uuid.UUID(sid))).scenes_asset_id  # type: ignore[union-attr]
        asset = await s.get(MediaAsset, asset_id)
    from app.config import get_settings

    doc = json.loads((Path(get_settings().storage_local_root) / asset.storage_key).read_text())  # type: ignore[union-attr]
    assert len(doc["cuts_ms"]) == 2 and abs(doc["cuts_ms"][0] - 16000) < 200 and abs(doc["cuts_ms"][1] - 32000) < 200


async def test_an_identical_request_is_answered_from_the_run_and_force_asks_again(env, cut_clip) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live, fake = env
    sid = await ready_source(c, h, pid, live, cut_clip)
    fake.default = reply(cand("s1", "s2"))
    assert (await find(c, h, sid)).status_code == 202
    await wait_hjob(c, h, sid)
    calls = len(fake.chats())
    again = await find(c, h, sid)
    assert again.status_code == 200 and again.json()["reused"] is True and again.json()["run"]["candidate_count"] == 1
    assert len(fake.chats()) == calls  # no second model call, no second bill
    async with live.app.state.sessionmaker() as s:
        assert (await s.execute(select(func.count()).select_from(HighlightRun))).scalar_one() == 1
    other = await find(c, h, sid, prompt="something else")  # a different wish is a different run
    assert other.status_code == 202
    await wait_hjob(c, h, sid)
    forced = await find(c, h, sid, force=True)
    assert forced.status_code == 202
    await wait_hjob(c, h, sid)
    async with live.app.state.sessionmaker() as s:
        assert (await s.execute(select(func.count()).select_from(HighlightRun))).scalar_one() == 3


async def test_a_part_the_model_could_not_answer_is_reported_and_the_rest_still_counts(env, cut_clip) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live, fake = env
    # tiny windows: the 8-sentence transcript needs several model calls
    saved = await c.put(
        f"{API}/highlights/settings",
        headers=h,
        json={"chars_per_token": 0.4, "max_window_tokens": 400, "fallback_context_tokens": 4096},
    )
    assert saved.status_code == 200, saved.text  # fmt: skip
    sid = await ready_source(c, h, pid, live, cut_clip)
    fake.script.append(
        (403, {"error": {"message": "forbidden"}}, {})
    )  # the first window cannot be answered (a 400 would only make us retry without schema mode)
    fake.default = reply(cand("s5", "s6", 80))
    assert (await find(c, h, sid)).status_code == 202
    src = await wait_hjob(c, h, sid)
    assert src["highlights_job"]["status"] == "succeeded", src["highlights_job"]
    stats = (await get_run(c, h, sid, src["highlights"]["run_id"]))["run"]["stats"]
    assert stats["windows"] >= 2 and len(stats["windows_failed"]) == 1 and stats["windows_failed"][0]["window"] == 0
    assert stats["windows_failed"][0]["code"] == "llm_failed"
    assert len(fake.chats()) == stats["windows"]  # every window was tried exactly once


async def test_when_no_part_can_be_answered_the_job_fails_with_a_reason_and_stores_nothing(env, cut_clip) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live, fake = env
    sid = await ready_source(c, h, pid, live, cut_clip)
    fake.default = (403, {"error": {"message": "nope"}}, {})
    assert (await find(c, h, sid)).status_code == 202
    src = await wait_hjob(c, h, sid)
    j = src["highlights_job"]
    assert (
        j["status"] == "failed"
        and j["error_code"] == "llm_failed"
        and "No part of the transcript" in j["error_message"]
    )
    assert src["status"] == "ready" and src["highlights"] is None
    async with live.app.state.sessionmaker() as s:
        assert (await s.execute(select(func.count()).select_from(HighlightRun))).scalar_one() == 0


async def test_a_model_that_returns_garbage_fails_visibly_after_two_repairs(env, cut_clip) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live, fake = env
    sid = await ready_source(c, h, pid, live, cut_clip)
    fake.default = (200, FakeLLM.chat_body("here are some great clips!!"), {})
    assert (await find(c, h, sid)).status_code == 202
    j = (await wait_hjob(c, h, sid))["highlights_job"]
    assert j["status"] == "failed"
    assert len(fake.chats()) == 3  # the answer plus at most two repairs, never more


async def test_audio_only_sources_work_and_scoring_leaves_scene_cuts_out(env, tone_only) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live, fake = env
    sid = await ready_source(c, h, pid, live, tone_only)
    fake.default = reply(cand("s1", "s2", 70))
    assert (await find(c, h, sid)).status_code == 202
    src = await wait_hjob(c, h, sid)
    assert src["highlights_job"]["status"] == "succeeded", src["highlights_job"]
    run = await get_run(c, h, sid, src["highlights"]["run_id"])
    assert run["run"]["stats"]["scene_cuts"] == 0
    assert "scene" in run["candidates"][0]["score_parts"]["unavailable"]  # left out, not scored as zero


async def test_requests_are_validated_and_gated(env, cut_clip, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live, fake = env
    bare = await ready_source(c, h, pid, live, cut_clip, with_transcript=False)
    none = await find(c, h, bare)
    assert none.status_code == 409 and none.json()["code"] == "no_transcript"  # transcribe first
    sid = await ready_source(c, h, pid, live, cut_clip)
    assert (await find(c, h, sid, categories=["nonsense"])).status_code == 400
    assert (await find(c, h, sid, min_seconds=30, max_seconds=10)).status_code == 400
    assert (await find(c, h, sid, max_candidates=99)).status_code == 422  # beyond the schema's own limit
    assert (await c.post(f"{API}/sources/{sid}/highlights", json={})).status_code == 401
    other = await invite_user(c, h, "other@example.com")
    assert (await find(c, other, sid)).status_code in (403, 404)
    assert (await c.get(f"{API}/sources/{sid}/highlights", headers=other)).status_code in (403, 404)
    await live.app.state.redis.delete(LLM_KEY)
    down = await find(c, h, sid)
    assert down.status_code == 503 and down.json()["code"] == "llm_unavailable"
    async with live.app.state.sessionmaker() as s:
        assert (
            await s.execute(
                select(func.count()).select_from(ProcessingJob).where(ProcessingJob.kind == "find_highlights")
            )
        ).scalar_one() == 0
    assert (await c.get(f"{API}/highlights/settings", headers=other)).status_code == 403  # admin only


async def test_no_model_configured_is_explained_before_any_job_exists(live, media_worker, cut_clip) -> None:  # type: ignore[no-untyped-def]
    await live.app.state.redis.set(LLM_KEY, "1", ex=600)
    async with httpx.AsyncClient(base_url=live.url, timeout=60) as c:
        h = await setup_admin(c)
        pid = await make_project(c, h)
        sid = await ready_source(c, h, pid, live, cut_clip)  # a transcript exists, but no model was ever chosen
        r = await find(c, h, sid)
        assert r.status_code == 409 and r.json()["code"] == "no_model_configured"
        async with live.app.state.sessionmaker() as s:
            n = await s.execute(
                select(func.count()).select_from(ProcessingJob).where(ProcessingJob.kind == "find_highlights")
            )
            assert n.scalar_one() == 0


async def test_review_marks_candidates_and_is_recorded(env, cut_clip) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live, fake = env
    sid = await ready_source(c, h, pid, live, cut_clip)
    fake.default = reply(cand("s1", "s2", 90), cand("s5", "s6", 80))
    await find(c, h, sid)
    src = await wait_hjob(c, h, sid)
    run = await get_run(c, h, sid, src["highlights"]["run_id"])
    cid = run["candidates"][0]["id"]
    ok = await c.patch(f"{API}/highlights/{cid}", headers=h, json={"status": "accepted"})
    assert ok.status_code == 200 and ok.json()["status"] == "accepted" and ok.json()["text"] == expect_text(1, 2)
    assert (await c.patch(f"{API}/highlights/{cid}", headers=h, json={"status": "published"})).status_code == 422
    other = await invite_user(c, h, "x@example.com")
    assert (await c.patch(f"{API}/highlights/{cid}", headers=other, json={"status": "rejected"})).status_code in (
        403,
        404,
    )
    again = await get_run(c, h, sid, run["run"]["id"])
    assert [x["status"] for x in again["candidates"]] == ["accepted", "new"]
    async with live.app.state.sessionmaker() as s:
        row = await s.get(HighlightCandidate, uuid.UUID(cid))
        assert row and row.reviewed_by is not None and row.reviewed_at is not None
    assert (await c.patch(f"{API}/highlights/{cid}", headers=h, json={"status": "new"})).json()["status"] == "new"
