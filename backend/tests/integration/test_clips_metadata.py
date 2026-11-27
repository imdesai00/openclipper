"""Per-platform metadata through the real stack, with a real HTTP server standing in for the model so its answers can
be scripted: good ones, ones that break a platform limit, and failures."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.db.models import ClipMetadata
from tests.clip_helpers import new_clip, ready_source
from tests.fake_llm import FakeLLM
from tests.helpers import API, add_provider, discover, invite_user, make_project, route, setup_admin

pytestmark = pytest.mark.integration
LLM_KEY = "llm:heartbeat"


def good(**kw: Any) -> tuple[int, Any, dict[str, str]]:
    body = {"title": "Why sleep beats diet", "hook": "Sleep matters more than you think", "description": "Researchers followed ten thousand people.",
            "short_description": "Sleep and health", "hashtags": ["sleep", "#Health"], "tags": ["sleep", "health"], "thumbnail_text": "SLEEP FIRST",
            "category": "Health", "audience": "Anyone who sleeps badly", **kw}  # fmt: skip
    return 200, FakeLLM.chat_body(json.dumps(body)), {}


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
        await route(c, admin, "metadata", m["id"])
        pid = await make_project(c, admin)
        yield c, admin, pid, live, fake


async def generate(c, h, clip_id: str, timeout: float = 90, **body: Any):  # type: ignore[no-untyped-def]
    r = await c.post(f"{API}/clips/{clip_id}/metadata/generate", headers=h, json=body)
    assert r.status_code == 202, r.text
    end = time.monotonic() + timeout
    while True:
        out = (await c.get(f"{API}/clips/{clip_id}/metadata", headers=h)).json()
        j = out["job"]
        if j and j["id"] == r.json()["job"]["id"] and j["status"] in ("succeeded", "failed"):
            return out
        assert time.monotonic() < end, j
        await asyncio.sleep(0.4)


async def test_each_platform_is_written_independently_from_the_clips_own_words(env, red_blue) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live, fake = env
    sid = await ready_source(c, h, pid, live, red_blue)
    clip = await new_clip(c, h, sid, 4000, 9000)
    fake.default = good()
    out = await generate(c, h, clip["id"])
    assert out["job"]["status"] == "succeeded"
    assert len(fake.chats()) == 3  # one call per platform, not one shared answer
    sent = [json.dumps(x["body"]["messages"]) for x in fake.chats()]
    assert any("YouTube Shorts" in s for s in sent) and any("Instagram Reel" in s for s in sent)
    assert all("surprising thing" in s and "Welcome back" not in s for s in sent)  # only words inside 4 s to 9 s
    for p in ("generic", "youtube", "instagram"):
        m = out["platforms"][p]
        assert (
            m["title"] == "Why sleep beats diet"
            and m["hashtags"] == ["#sleep", "#Health"]
            and m["generated_by"] == "fake / fake-model"
        )
        assert m["edited"] is False and m["errors"] == [] and m["last_error"] is None
    assert out["limits"]["youtube"]["title"] == 100 and out["soft_hashtag_limits"]["instagram"] == 30


async def test_an_answer_that_breaks_a_limit_is_sent_back_for_repair_not_cut_off(env, red_blue) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live, fake = env
    sid = await ready_source(c, h, pid, live, red_blue)
    clip = await new_clip(c, h, sid, 4000, 9000)
    fake.script.append(good())  # generic is fine
    fake.script.append(good(title="y" * 150))  # youtube: title far over 100 characters
    fake.default = good(title="Short enough")  # the repaired answer, and instagram
    out = await generate(c, h, clip["id"], platforms=["generic", "youtube", "instagram"])
    assert out["job"]["status"] == "succeeded"
    assert out["platforms"]["youtube"]["title"] == "Short enough"  # fixed by the model, never truncated by us
    repair = [json.dumps(x["body"]["messages"]) for x in fake.chats()][2]
    assert "not valid for the required format" in repair  # the model was told what was wrong


async def test_one_platform_failing_does_not_lose_the_others_and_says_why(env, red_blue) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live, fake = env
    sid = await ready_source(c, h, pid, live, red_blue)
    clip = await new_clip(c, h, sid, 4000, 9000)
    fake.script.append(good())
    fake.script.append((403, {"error": {"message": "forbidden"}}, {}))  # youtube cannot be answered
    fake.default = good()
    out = await generate(c, h, clip["id"])
    assert out["job"]["status"] == "succeeded"
    assert out["platforms"]["generic"]["title"] and out["platforms"]["instagram"]["title"]
    yt = out["platforms"]["youtube"]
    assert yt["title"] == "" and yt["last_error"] and "No model could answer" in yt["last_error"]


async def test_when_nothing_can_be_written_the_job_fails_with_a_reason_and_stores_nothing(env, red_blue) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live, fake = env
    sid = await ready_source(c, h, pid, live, red_blue)
    clip = await new_clip(c, h, sid, 4000, 9000)
    fake.default = (403, {"error": {"message": "nope"}}, {})
    out = await generate(c, h, clip["id"])
    assert (
        out["job"]["status"] == "failed"
        and out["job"]["error_code"] == "llm_failed"
        and "No metadata" in out["job"]["error_message"]
    )
    async with live.app.state.sessionmaker() as s:
        assert (await s.execute(select(ClipMetadata))).first() is None


async def test_hand_edits_are_validated_kept_and_never_overwritten_without_asking(env, red_blue) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live, fake = env
    sid = await ready_source(c, h, pid, live, red_blue)
    clip = await new_clip(c, h, sid, 4000, 9000)
    fake.default = good()
    await generate(c, h, clip["id"])
    put = lambda p, **f: c.put(f"{API}/clips/{clip['id']}/metadata/{p}", headers=h, json=f)  # noqa: E731

    ok = await put("youtube", title="My own title", hashtags=["#Mine", "mine", "two words"])
    assert (
        ok.status_code == 200
        and ok.json()["title"] == "My own title"
        and ok.json()["hashtags"] == ["#Mine", "#twowords"]
        and ok.json()["edited"] is True
    )
    over = await put("youtube", title="x" * 101)
    assert (
        over.status_code == 400 and over.json()["code"] == "limit_exceeded" and "Title is 101" in over.json()["message"]
    )
    cap = await put(
        "instagram", description="x" * 2190, hashtags=["#" + "h" * 9] * 5
    )  # the hashtags push the caption over 2200
    assert cap.status_code == 400 and "with its hashtags" in cap.json()["message"]
    soft = await put("youtube", hashtags=[f"#tag{i}" for i in range(18)])
    assert soft.status_code == 200 and soft.json()["warnings"] and not soft.json()["errors"]  # a warning, not a refusal
    assert (await put("tiktok", title="x")).status_code == 400
    assert (await c.get(f"{API}/clips/{clip['id']}/metadata", headers=h)).json()["platforms"]["youtube"][
        "title"
    ] == "My own title"

    # generating again refuses to replace the edit...
    calls = len(fake.chats())
    again = await c.post(f"{API}/clips/{clip['id']}/metadata/generate", headers=h, json={"platforms": ["youtube"]})
    assert again.status_code == 409 and again.json()["code"] == "edited_metadata" and len(fake.chats()) == calls
    # ...but another platform, or an explicit overwrite, goes ahead
    fake.default = good(title="Fresh from the model")
    assert (await generate(c, h, clip["id"], platforms=["generic"]))["platforms"]["generic"][
        "title"
    ] == "Fresh from the model"
    assert (await generate(c, h, clip["id"], platforms=["youtube"], overwrite=True))["platforms"]["youtube"][
        "title"
    ] == "Fresh from the model"


async def test_requests_are_gated_and_authorised(env, red_blue, live) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, _, fake = env
    bare = await ready_source(c, h, pid, live, red_blue, transcript=False)
    nt = await new_clip(c, h, bare, 4000, 9000)
    r = await c.post(f"{API}/clips/{nt['id']}/metadata/generate", headers=h, json={})
    assert r.status_code == 409 and r.json()["code"] == "no_transcript"
    sid = await ready_source(c, h, pid, live, red_blue)
    clip = await new_clip(c, h, sid, 4000, 9000)
    other = await invite_user(c, h, "other@example.com")
    assert (await c.post(f"{API}/clips/{clip['id']}/metadata/generate", headers=other, json={})).status_code in (
        403,
        404,
    )
    assert (await c.get(f"{API}/clips/{clip['id']}/metadata", headers=other)).status_code in (403, 404)
    assert (
        await c.put(f"{API}/clips/{clip['id']}/metadata/generic", headers=other, json={"title": "x"})
    ).status_code in (403, 404)
    await live.app.state.redis.delete(LLM_KEY)
    down = await c.post(f"{API}/clips/{clip['id']}/metadata/generate", headers=h, json={})
    assert down.status_code == 503 and down.json()["code"] == "llm_unavailable" and fake.chats() == []


async def test_no_model_configured_is_explained_before_any_job_exists(live, media_worker, red_blue) -> None:  # type: ignore[no-untyped-def]
    await live.app.state.redis.set(LLM_KEY, "1", ex=600)
    async with httpx.AsyncClient(base_url=live.url, timeout=60) as c:
        h = await setup_admin(c)
        pid = await make_project(c, h)
        sid = await ready_source(c, h, pid, live, red_blue)
        clip = await new_clip(c, h, sid, 4000, 9000)
        r = await c.post(f"{API}/clips/{clip['id']}/metadata/generate", headers=h, json={})
        assert r.status_code == 409 and r.json()["code"] == "no_model_configured"
