"""Highlight detection against a REAL local model (Ollama). SKIPPED without TEST_OLLAMA_URL, never counted as passed.

A small model's taste is not something a test can promise, so these assert what the SERVER controls (the contract in
SPEC section 6.E): the job ends visibly, every candidate is a span of real stored words, nothing is invented, usage is
logged at zero cost. How many candidates the model finds, and how good they are, is printed so it can be reported."""

from __future__ import annotations

import asyncio
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.db.models import AiUsageLog, HighlightCandidate, TranscriptSegment, TranscriptWord, VideoSource
from app.ports.asr import SegmentData, TranscriptData, WordData
from app.services import transcripts
from tests.helpers import API, add_provider, discover, make_project, route, setup_admin, tus_upload, wait_source
from tests.integration.conftest import OLLAMA, OLLAMA_MODEL, needs_ollama

pytestmark = [pytest.mark.integration, needs_ollama]

TALK = [
    "Welcome back everyone, today we are talking about how to build a habit that actually lasts.",
    "Most people quit within three weeks, and the reason is almost never a lack of willpower.",
    "The real problem is that they start with goals that are far too big for a normal tuesday.",
    "I made this mistake myself when I tried to run five miles every single morning.",
    "By the second week I was exhausted, and by the third week I had stopped completely.",
    "Then a friend told me something that changed everything about how I think about change.",
    "She said, make it so small that it feels silly not to do it.",
    "So I started with putting on my running shoes and standing outside for one minute.",
    "That was the whole habit, and honestly it felt a little ridiculous at first.",
    "But here is the surprising part, within a month I was running again without forcing myself.",
    "Researchers call this the power of tiny habits, and the evidence for it is strong.",
    "A study of three thousand people found that small daily actions beat big occasional efforts.",
    "The group that did two minutes a day was far more likely to keep going after a year.",
    "Now let me change the subject and tell you about the worst coffee I ever had.",
    "It was in a small town, the cup was cold, and the owner insisted it was a special blend.",
    "I asked what made it special and he said, it is the same pot since monday.",
    "We both laughed so hard that I forgot to be annoyed about the coffee entirely.",
    "Anyway, back to habits, because the second big idea is about your environment.",
    "If the thing you want to do is hard to start, you will not start it on a bad day.",
    "So put the book on your pillow, leave the guitar out of its case, fill the water bottle.",
    "Make the good action easy and the bad action just a little bit harder.",
    "The third idea is to never miss twice, because one skipped day is an accident.",
    "Two skipped days in a row is the beginning of a new habit, and not the one you want.",
    "If you remember only one thing from today, remember to start small and to start now.",
    "Thank you for listening, and I will see you in the next episode.",
]


def spoken() -> list[tuple[int, int, str, list[tuple[int, int, str]]]]:
    out, t = [], 1000
    for s in TALK:
        words, start = [], t
        for w in s.split():
            words.append((t, t + 420, w))
            t += 420
        out.append((start, t, s, words))
        t += 400
    return out


@pytest.fixture(scope="session")
def clip(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, int]:
    sp = spoken()
    dur = sp[-1][1] // 1000 + 2
    out = tmp_path_factory.mktemp("hll") / "talk.mp4"
    f = ["-f", "lavfi", "-i"]
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *f, "color=c=0x303030:s=320x240:r=10:d=" + str(dur),
         *f, f"sine=frequency=250:duration={dur}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)],
        check=True,
    )  # fmt: skip
    return out, dur


async def test_a_real_local_model_finds_clips_that_are_real_stretches_of_the_transcript(
    live, media_worker, clip
) -> None:  # type: ignore[no-untyped-def]
    path, dur = clip
    await live.app.state.redis.set("llm:heartbeat", "1", ex=900)
    async with httpx.AsyncClient(base_url=live.url, timeout=300) as c:
        h = await setup_admin(c)
        p = await add_provider(c, h, kind="ollama", name="Ollama", base_url=OLLAMA, timeout_s=240)
        models = await discover(c, h, p["id"])
        m = next(x for x in models if x["model_id"] == OLLAMA_MODEL)
        await route(c, h, "highlight", m["id"])
        pid = await make_project(c, h)
        sid = await tus_upload(c, h, pid, path)
        assert (await wait_source(c, h, sid))["status"] == "ready"

        from app.services.jobs import create_job

        data = TranscriptData(
            "en", 0.99, float(dur), "test", "synthetic",
            [SegmentData(s / 1000, e / 1000, txt, [WordData(a / 1000, b / 1000, w) for a, b, w in ws]) for s, e, txt, ws in spoken()],
        )  # fmt: skip
        async with live.app.state.sessionmaker() as s:
            src = await s.get(VideoSource, uuid.UUID(sid))
            assert src
            job = await create_job(s, src, None, kind="transcribe_source", params={})
            await transcripts.persist_asr(s, source=src, job=job, data=data)
            await s.commit()

        t0 = time.monotonic()
        r = await c.post(
            f"{API}/sources/{sid}/highlights",
            headers=h,
            json={"prompt": "the most useful advice", "min_seconds": 10, "max_seconds": 40, "max_candidates": 5},
        )
        assert r.status_code == 202, r.text  # fmt: skip
        while True:
            src_j = (await c.get(f"{API}/sources/{sid}", headers=h)).json()
            j = src_j["highlights_job"]
            if j["status"] in ("succeeded", "failed"):
                break
            assert time.monotonic() - t0 < 600, j
            await asyncio.sleep(1)
        took = time.monotonic() - t0

        assert src_j["status"] == "ready"
        if j["status"] == "failed":  # allowed by the contract, but it must say why
            print(
                f"\nMEASURED local model {OLLAMA_MODEL}: job FAILED in {took:.0f}s: {j['error_code']}: {j['error_message']}"
            )
            # a model that cannot answer is a visible, explained failure; a crash in our own code is never acceptable
            assert j["error_code"] in {"llm_failed", "invalid_structured_output", "route_unusable"}, j
            assert j["error_message"]
            return
        run = (await c.get(f"{API}/sources/{sid}/highlights/{src_j['highlights']['run_id']}", headers=h)).json()
        st = run["run"]["stats"]
        print(
            f"\nMEASURED local model {OLLAMA_MODEL}: {took:.0f}s, windows={st['windows']} (failed {len(st['windows_failed'])}), "
            f"raw={st['raw_candidates']}, kept={st['kept']}, dropped={st['dropped']}, repairs={st['repairs']}, "
            f"tokens in/out={st['tokens_in']}/{st['tokens_out']}, ctx={st['model_context']} ({st['context_source']}), window_tokens={st['window_tokens']}"
        )
        for x in run["candidates"]:
            print(
                f"  #{x['rank']} {x['start_ms'] / 1000:.1f}-{x['end_ms'] / 1000:.1f}s [{x['category']}] final={x['final_score']} llm={x['llm_score']}: {x['title']!r}"
            )

        # The contract. Re-derive every candidate's words from the stored transcript, independently of the service.
        async with live.app.state.sessionmaker() as s:
            cands = list((await s.execute(select(HighlightCandidate).order_by(HighlightCandidate.rank))).scalars())
            assert len(cands) == len(run["candidates"])
            words = {}
            for seg in (await s.execute(select(TranscriptSegment))).scalars():
                for w in (await s.execute(select(TranscriptWord).where(TranscriptWord.segment_id == seg.id))).scalars():
                    words[(seg.idx, w.idx)] = w
            for x in cands:
                a, b = words[(x.start_seg, x.start_word)], words[(x.end_seg, x.end_word)]
                # padding only ever widens the span by a little: never invented time, never outside the media
                assert a.start_ms - 150 <= x.start_ms <= a.start_ms and b.end_ms <= x.end_ms <= b.end_ms + 250
                assert 0 <= x.start_ms < x.end_ms <= dur * 1000
                api = next(y for y in run["candidates"] if y["id"] == str(x.id))
                expected = " ".join(
                    w.text
                    for k, w in sorted(words.items())
                    if (x.start_seg, x.start_word) <= k <= (x.end_seg, x.end_word)
                )
                assert api["text"] == expected and expected in " ".join(TALK)  # a contiguous stretch of what was said
            usage = list((await s.execute(select(AiUsageLog))).scalars())
            assert usage and all((u.cost_est is None or u.cost_est == 0) for u in usage)  # local: free, never invented
