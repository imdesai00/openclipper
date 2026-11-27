"""Metadata against a REAL local model (Ollama). SKIPPED without TEST_OLLAMA_URL, never counted as passed.

What a 0.5B model writes is not something a test can promise; these assert what the SERVER controls: the job ends
visibly, and whatever was stored respects every hard platform limit and is tidy. What the model actually wrote is
printed so it can be reported."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from app.services import clip_metadata_logic as ml
from tests.clip_helpers import new_clip, ready_source
from tests.helpers import API, add_provider, discover, make_project, route, setup_admin
from tests.integration.conftest import OLLAMA, OLLAMA_MODEL, needs_ollama

pytestmark = [pytest.mark.integration, needs_ollama]


async def test_a_real_local_model_writes_metadata_that_respects_every_platform_limit(
    live, media_worker, red_blue
) -> None:  # type: ignore[no-untyped-def]
    await live.app.state.redis.set("llm:heartbeat", "1", ex=900)
    async with httpx.AsyncClient(base_url=live.url, timeout=300) as c:
        h = await setup_admin(c)
        p = await add_provider(c, h, kind="ollama", name="Ollama", base_url=OLLAMA, timeout_s=240)
        m = next(x for x in await discover(c, h, p["id"]) if x["model_id"] == OLLAMA_MODEL)
        await route(c, h, "metadata", m["id"])
        pid = await make_project(c, h)
        sid = await ready_source(c, h, pid, live, red_blue)
        clip = await new_clip(c, h, sid, 4000, 14000)
        t0 = time.monotonic()
        r = await c.post(f"{API}/clips/{clip['id']}/metadata/generate", headers=h, json={})
        assert r.status_code == 202, r.text
        while True:
            out = (await c.get(f"{API}/clips/{clip['id']}/metadata", headers=h)).json()
            j = out["job"]
            if j and j["status"] in ("succeeded", "failed"):
                break
            assert time.monotonic() - t0 < 600, j
            await asyncio.sleep(1)
        took = time.monotonic() - t0
        if j["status"] == "failed":  # allowed, but it must be an explained model failure and never a crash of ours
            print(
                f"\nMEASURED local model {OLLAMA_MODEL}: metadata job FAILED in {took:.0f}s: {j['error_code']}: {j['error_message']}"
            )
            assert (
                j["error_code"] in {"llm_failed", "invalid_structured_output", "route_unusable"} and j["error_message"]
            )
            return
        wrote = [p_ for p_, v in out["platforms"].items() if v["title"]]
        print(f"\nMEASURED local model {OLLAMA_MODEL}: metadata in {took:.0f}s, platforms written: {wrote}, failed: "
              f"{ {p_: v['last_error'] for p_, v in out['platforms'].items() if v['last_error']} }")  # fmt: skip
        for p_ in wrote:
            v = out["platforms"][p_]
            print(f"  {p_}: title={v['title']!r} hashtags={v['hashtags']} tags={v['tags']}")
            assert v["errors"] == [], v["errors"]  # no hard limit is exceeded in anything stored
            assert all(t.startswith("#") and " " not in t for t in v["hashtags"])
            assert v["generated_by"] and OLLAMA_MODEL in v["generated_by"]
            assert ml.validate(p_, v)[0] == []
        assert wrote, "the job succeeded, so at least one platform must have been written"
