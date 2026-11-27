"""LLM behaviour: real Ollama + a scriptable real HTTP server. No mocks in this file."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from httpx import AsyncClient
from pydantic import BaseModel

from app.ports.llm import ChatMessage
from tests.conftest import make_client
from tests.fake_llm import FakeLLM
from tests.helpers import API, add_provider, ask, discover, route, set_mode, setup_admin
from tests.integration.conftest import OLLAMA, OLLAMA_MODEL, needs_ollama

pytestmark = pytest.mark.integration
FAST = {"LLM_BACKOFF_BASE_SECONDS": 0.01, "LLM_BREAKER_THRESHOLD": 3, "LLM_BREAKER_COOLDOWN_SECONDS": 1}


@pytest.fixture
async def ctx(app_factory):  # type: ignore[no-untyped-def]
    app = await app_factory(**FAST)
    async with make_client(app) as c:
        yield c, await setup_admin(c), app


async def usage_rows(c: AsyncClient, h: dict[str, str]) -> list[dict]:  # type: ignore[type-arg]
    return list((await c.get(f"{API}/usage", headers=h)).json()["recent"])


async def wire(
    c: AsyncClient,
    h: dict[str, str],
    fake: FakeLLM,
    *,
    name: str = "fake",
    local: bool = True,
    task: str = "highlight",
    **kw,
) -> tuple[dict, dict]:  # type: ignore[type-arg,no-untyped-def]
    p = await add_provider(c, h, base_url=fake.url, name=name, is_local=local, **kw)
    m = (await discover(c, h, p["id"]))[0]
    await route(c, h, task, m["id"])
    return p, m


# ======================================================================================= real Ollama
@needs_ollama
async def test_real_ollama_end_to_end_local_mode(ctx) -> None:  # type: ignore[no-untyped-def]
    c, h, _ = ctx
    assert (await c.get(f"{API}/ai/mode", headers=h)).json() == {
        "mode": "LOCAL"
    }  # a fresh install sends nothing anywhere
    p = await add_provider(c, h, kind="ollama", name="Ollama", base_url=OLLAMA)
    assert p["is_local"] is True and p["has_credential"] is False

    t = (await c.post(f"{API}/providers/{p['id']}/test", headers=h)).json()
    assert t["ok"] and t["message"].startswith("Ollama") and t["latency_ms"] >= 0
    assert (await c.get(f"{API}/providers", headers=h)).json()[0]["last_test_ok"] is True

    models = await discover(c, h, p["id"])
    m = next(x for x in models if x["model_id"] == OLLAMA_MODEL)
    assert m["context_length"] and m["context_length"] >= 2048 and m["is_free"] and m["supports_json_schema"]
    assert m["price_source"] == "local" and Decimal(m["input_per_mtok"]) == 0
    await route(c, h, "highlight", m["id"])

    r = await ask(c, h, "Reply with the single word: pong", max_tokens=16)
    assert r.status_code == 200, r.text
    out = r.json()
    assert "pong" in out["text"].lower() and out["tokens_in"] > 0 and out["tokens_out"] > 0
    assert Decimal(out["cost_est"]) == 0 and out["provider"] == "Ollama" and out["model"] == OLLAMA_MODEL
    u = (await c.get(f"{API}/usage", headers=h)).json()
    assert (u["calls"], u["errors"], Decimal(u["cost_est"])) == (1, 0, 0) and u["recent"][0]["status"] == "ok"
    assert u["by_model"][0]["tokens_in"] == out["tokens_in"]


class Fruit(BaseModel):
    fruit: str
    colour: str


@needs_ollama
async def test_real_ollama_schema_constrained_output(ctx) -> None:  # type: ignore[no-untyped-def]
    from app.security.crypto import Vault
    from app.services.llm import LLMService

    c, h, app = ctx
    p = await add_provider(c, h, kind="ollama", name="Ollama", base_url=OLLAMA)
    m = next(x for x in await discover(c, h, p["id"]) if x["model_id"] == OLLAMA_MODEL)
    await route(c, h, "metadata", m["id"])
    svc = LLMService(
        app.state.sessionmaker, app.state.redis, Vault.from_settings(app.state.settings), app.state.settings
    )
    res = await svc.complete(task="metadata", messages=[ChatMessage("user", "Name a fruit and its usual colour.")],
                             schema_model=Fruit, temperature=0, max_tokens=80)  # fmt: skip
    assert isinstance(res.parsed, Fruit) and res.parsed.fruit and res.parsed.colour and res.repairs == 0


@needs_ollama
async def test_openai_compatible_adapter_against_real_ollama_v1(ctx) -> None:  # type: ignore[no-untyped-def]
    c, h, _ = ctx
    p = await add_provider(c, h, name="via-v1", base_url=f"{OLLAMA}/v1", is_local=True)
    assert (await c.post(f"{API}/providers/{p['id']}/test", headers=h)).json()["ok"] is True
    m = next(x for x in await discover(c, h, p["id"]) if x["model_id"] == OLLAMA_MODEL)
    await route(c, h, "highlight", m["id"])
    r = await ask(c, h, "Reply with the single word: pong", max_tokens=16)
    assert r.status_code == 200 and "pong" in r.json()["text"].lower() and r.json()["tokens_in"] > 0


@needs_ollama
async def test_model_download_streams_progress(ctx) -> None:  # type: ignore[no-untyped-def]
    import json

    c, h, _ = ctx
    p = await add_provider(c, h, kind="ollama", name="Ollama", base_url=OLLAMA)
    async with c.stream("POST", f"{API}/providers/{p['id']}/pull", headers=h, json={"model": OLLAMA_MODEL}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) async for line in r.aiter_lines() if line.strip()]
    assert events and events[-1].get("status") == "success"
    other = await add_provider(c, h, name="not-ollama", base_url="http://127.0.0.1:9/v1")
    assert (await c.post(f"{API}/providers/{other['id']}/pull", headers=h, json={"model": "x"})).json()[
        "code"
    ] == "not_ollama"
    assert (
        await c.post(f"{API}/providers/{p['id']}/pull", headers=h, json={"model": "bad name; rm -rf /"})
    ).status_code == 422


@needs_ollama
async def test_fallback_chain_skips_a_dead_model_and_records_both(ctx) -> None:  # type: ignore[no-untyped-def]
    c, h, _ = ctx
    dead = await add_provider(c, h, name="dead", base_url="http://127.0.0.1:9/v1", is_local=True)
    # a model row for the dead provider without calling it: discovery would fail, so add via a real server first
    fake = FakeLLM()
    try:
        await c.patch(f"{API}/providers/{dead['id']}", headers=h, json={"base_url": fake.url})
        dm = (await discover(c, h, dead["id"]))[0]
        await c.patch(f"{API}/providers/{dead['id']}", headers=h, json={"base_url": "http://127.0.0.1:9/v1"})
    finally:
        fake.close()
    ol = await add_provider(c, h, kind="ollama", name="Ollama", base_url=OLLAMA)
    om = next(x for x in await discover(c, h, ol["id"]) if x["model_id"] == OLLAMA_MODEL)
    await route(c, h, "highlight", dm["id"], om["id"])
    r = await ask(c, h, "Reply with the single word: pong", max_tokens=16)
    assert r.status_code == 200 and r.json()["provider"] == "Ollama"
    assert [a["code"] for a in r.json()["failed_attempts"]] == ["unreachable"]
    rows = await usage_rows(c, h)
    assert (
        sorted((x["provider_name"], x["status"]) for x in rows) == [("Ollama", "ok")] + [("dead", "error")] * 3
    )  # 3 tries on the dead one


# ======================================================================================= modes
async def test_local_mode_never_contacts_a_remote_provider(ctx, fake: FakeLLM) -> None:  # type: ignore[no-untyped-def]
    c, h, _ = ctx
    await wire(c, h, fake, name="cloud", local=False)
    fake.requests.clear()  # discovery above is an admin action; what matters is what a *task* sends

    r = await ask(c, h)  # default mode is LOCAL
    assert r.status_code == 409 and r.json()["code"] == "route_unusable"
    assert "Local mode does not allow a remote provider" in str(r.json()["details"])
    assert fake.requests == []  # nothing left the machine

    await set_mode(c, h, "HYBRID")
    assert (await ask(c, h)).status_code == 200 and len(fake.chats()) == 1
    await set_mode(c, h, "REMOTE")
    assert (await ask(c, h)).status_code == 200 and len(fake.chats()) == 2


async def test_remote_mode_blocks_local_models(ctx, fake: FakeLLM) -> None:  # type: ignore[no-untyped-def]
    c, h, _ = ctx
    await wire(c, h, fake, local=True)
    await set_mode(c, h, "REMOTE")
    fake.requests.clear()
    r = await ask(c, h)
    assert (
        r.status_code == 409
        and "Remote mode does not allow a local provider" in str(r.json()["details"])
        and fake.requests == []
    )


async def test_routes_report_why_an_entry_is_unusable(ctx, fake: FakeLLM) -> None:  # type: ignore[no-untyped-def]
    c, h, _ = ctx
    p, m = await wire(c, h, fake, local=False)
    entry = (await c.get(f"{API}/ai/routes", headers=h)).json()["routes"]["highlight"][0]
    assert entry["usable"] is False and "Local mode" in entry["unusable_reason"]
    await set_mode(c, h, "HYBRID")
    assert (await c.get(f"{API}/ai/routes", headers=h)).json()["routes"]["highlight"][0]["usable"] is True
    await c.patch(f"{API}/providers/{p['id']}", headers=h, json={"enabled": False})
    assert (
        "switched off"
        in (await c.get(f"{API}/ai/routes", headers=h)).json()["routes"]["highlight"][0]["unusable_reason"]
    )
    r = await ask(c, h)
    assert r.status_code == 409 and r.json()["code"] == "route_unusable"


async def test_route_validation(ctx, fake: FakeLLM) -> None:  # type: ignore[no-untyped-def]
    import uuid

    c, h, _ = ctx
    _, m = await wire(c, h, fake)
    assert (await ask(c, h, task="metadata")).json()["code"] == "no_model_configured"
    assert (await c.put(f"{API}/ai/routes", headers=h, json={"routes": {"highlight": [m["id"], m["id"]]}})).json()[
        "code"
    ] == "duplicate_model"
    assert (
        await c.put(f"{API}/ai/routes", headers=h, json={"routes": {"highlight": [str(uuid.uuid4())]}})
    ).status_code == 404
    assert (await c.put(f"{API}/ai/routes", headers=h, json={"routes": {"nonsense": []}})).status_code == 422
    assert (await c.put(f"{API}/ai/mode", headers=h, json={"mode": "YOLO"})).status_code == 422
    assert (
        await c.put(f"{API}/ai/routes", headers=h, json={"routes": {"highlight": []}})
    ).status_code == 200  # empty clears
    assert (await ask(c, h)).json()["code"] == "no_model_configured"
    assert (await ask(c, h, prompt="")).status_code == 422


async def test_a_specific_model_can_be_tested_without_routing_and_disabled_models_are_refused(
    ctx, fake: FakeLLM
) -> None:  # type: ignore[no-untyped-def]
    c, h, _ = ctx
    p = await add_provider(c, h, base_url=fake.url, is_local=True)
    m = (await discover(c, h, p["id"]))[0]
    assert (await ask(c, h, model_id=m["id"])).status_code == 200  # no route needed
    assert (await c.patch(f"{API}/models/{m['id']}", headers=h, json={"enabled": False})).json()["enabled"] is False
    assert (await ask(c, h, model_id=m["id"])).json()["code"] == "route_unusable"


# ======================================================================================= resilience
async def test_transient_server_errors_are_retried_and_every_attempt_is_logged(ctx, fake: FakeLLM) -> None:  # type: ignore[no-untyped-def]
    c, h, _ = ctx
    await wire(c, h, fake)
    fake.script.extend([(503, {"error": "overloaded"}, {}), (503, {"error": "overloaded"}, {})])
    r = await ask(c, h)
    assert r.status_code == 200 and len(fake.chats()) == 3
    rows = sorted(await usage_rows(c, h), key=lambda x: x["attempt"])
    assert [(x["attempt"], x["status"], x["error_code"]) for x in rows] == [
        (1, "error", "provider_error"),
        (2, "error", "provider_error"),
        (3, "ok", None),
    ]


async def test_rate_limit_responses_honour_retry_after(ctx, fake: FakeLLM) -> None:  # type: ignore[no-untyped-def]
    c, h, _ = ctx
    await wire(c, h, fake)
    fake.script.append((429, {"error": "slow down"}, {"Retry-After": "0"}))
    r = await ask(c, h)
    assert r.status_code == 200 and len(fake.chats()) == 2


@pytest.mark.parametrize(("status", "code"), [(401, "auth_failed"), (404, "not_found"), (400, "bad_request")])
async def test_permanent_errors_are_not_retried_and_are_explained(ctx, fake: FakeLLM, status: int, code: str) -> None:  # type: ignore[no-untyped-def]
    c, h, _ = ctx
    await wire(c, h, fake)
    fake.default = (status, {"error": "no"}, {})
    r = await ask(c, h)
    assert r.status_code == 502 and r.json()["code"] == "llm_failed" and len(fake.chats()) == 1
    assert r.json()["details"][0]["code"] == code and r.json()["details"][0]["message"]
    assert [x["error_code"] for x in await usage_rows(c, h)] == [code]


async def test_circuit_breaker_opens_then_recovers_after_the_cooldown(ctx, fake: FakeLLM) -> None:  # type: ignore[no-untyped-def]
    c, h, _ = ctx
    p, _ = await wire(c, h, fake)
    fake.default = (503, {"error": "down"}, {})
    first = await ask(c, h)  # three attempts -> three failures -> the breaker opens
    assert first.status_code == 502 and len(fake.chats()) == 3
    n_rows = len(await usage_rows(c, h))

    second = await ask(c, h)  # fails instantly, sends nothing, logs nothing new
    assert second.json()["details"][0]["code"] == "circuit_open" and len(fake.chats()) == 3
    assert len(await usage_rows(c, h)) == n_rows

    fake.default = (200, FakeLLM.chat_body("back"), {})
    await asyncio.sleep(1.3)  # cooldown over: half-open, the next call is a real trial
    third = await ask(c, h)
    assert third.status_code == 200 and third.json()["text"] == "back" and len(fake.chats()) == 4
    assert (await ask(c, h)).status_code == 200  # and the breaker is closed again


async def test_the_breaker_is_per_provider_so_a_fallback_keeps_working(ctx, fake: FakeLLM, fake2: FakeLLM) -> None:  # type: ignore[no-untyped-def]
    c, h, _ = ctx
    _, m1 = await wire(c, h, fake, name="primary")
    p2 = await add_provider(c, h, base_url=fake2.url, name="backup", is_local=True)
    m2 = (await discover(c, h, p2["id"]))[0]
    await route(c, h, "highlight", m1["id"], m2["id"])
    fake.default = (503, {}, {})
    r = await ask(c, h)
    assert r.status_code == 200 and r.json()["provider"] == "backup"
    assert [a["code"] for a in r.json()["failed_attempts"]] == ["provider_error"]
    r2 = await ask(c, h)  # primary's breaker is open now: skipped without a single request
    assert (
        r2.json()["provider"] == "backup"
        and r2.json()["failed_attempts"][0]["code"] == "circuit_open"
        and len(fake.chats()) == 3
    )


async def test_a_provider_over_its_own_rate_limit_is_skipped(ctx, fake: FakeLLM, fake2: FakeLLM) -> None:  # type: ignore[no-untyped-def]
    c, h, _ = ctx
    _, m1 = await wire(c, h, fake, name="limited", rate_limit_rpm=1)
    p2 = await add_provider(c, h, base_url=fake2.url, name="open", is_local=True)
    m2 = (await discover(c, h, p2["id"]))[0]
    await route(c, h, "highlight", m1["id"], m2["id"])
    assert (await ask(c, h)).json()["provider"] == "limited"
    r = await ask(c, h)
    assert (
        r.json()["provider"] == "open"
        and r.json()["failed_attempts"][0]["code"] == "local_rate_limit"
        and len(fake.chats()) == 1
    )


# ======================================================================================= structured output
async def test_invalid_json_is_repaired_with_the_error_fed_back(ctx, fake: FakeLLM) -> None:  # type: ignore[no-untyped-def]
    c, h, _ = ctx
    await wire(c, h, fake)
    fake.script.append((200, FakeLLM.chat_body("Sure! Here you go: not json"), {}))
    fake.default = (200, FakeLLM.chat_body('{"title": "A good clip"}'), {})
    r = await ask(c, h, json_mode=True)
    assert r.status_code == 200, r.text
    assert r.json()["repairs"] == 1 and r.json()["text"] == '{"title": "A good clip"}'
    second = fake.chats()[1]["body"]["messages"]
    assert second[-2]["role"] == "assistant" and "not valid" in second[-1]["content"]
    assert len(await usage_rows(c, h)) == 2  # both calls were metered


async def test_repair_gives_up_after_two_tries_and_says_so(ctx, fake: FakeLLM) -> None:  # type: ignore[no-untyped-def]
    c, h, _ = ctx
    await wire(c, h, fake)
    fake.default = (200, FakeLLM.chat_body("never json"), {})
    r = await ask(c, h, json_mode=True)
    assert r.status_code == 502 and len(fake.chats()) == 3  # first answer + two repairs, then stop
    assert r.json()["details"][0]["code"] == "invalid_structured_output"


async def test_models_without_native_schema_support_fall_back_to_prompt_and_validate(ctx, fake: FakeLLM) -> None:  # type: ignore[no-untyped-def]
    c, h, _ = ctx
    await wire(c, h, fake)
    fake.script.append((400, {"error": "response_format json_schema is not supported"}, {}))
    fake.default = (200, FakeLLM.chat_body('{"ok": true}'), {})
    r = await ask(c, h, json_mode=True)
    assert r.status_code == 200 and '"ok"' in r.json()["text"]
    first, second = (x["body"] for x in fake.chats()[:2])
    assert "response_format" in first and "response_format" not in second  # retried the same model without it
    assert any("JSON Schema" in m["content"] for m in second["messages"])
    n = len(fake.chats())
    assert (await ask(c, h, json_mode=True)).status_code == 200
    assert "response_format" not in fake.chats()[n]["body"]  # remembered: the doomed variant is not tried again


# ======================================================================================= cost
async def test_cost_comes_from_user_prices_then_provider_prices_and_is_never_invented(ctx, fake: FakeLLM) -> None:  # type: ignore[no-untyped-def]
    c, h, _ = ctx
    fake.models = [{"id": "priced", "pricing": {"prompt": "0.000001", "completion": "0.000004"}},
                   {"id": "unpriced"}, {"id": "vendor/x:free"}]  # fmt: skip
    fake.default = (200, FakeLLM.chat_body("hi", tin=1000, tout=500), {})
    p = await add_provider(c, h, base_url=fake.url, name="cloud", is_local=False)
    by = {m["model_id"]: m for m in await discover(c, h, p["id"])}
    await set_mode(c, h, "HYBRID")

    async def cost(model: str) -> Decimal | None:
        await route(c, h, "highlight", by[model]["id"])
        v = (await ask(c, h)).json()["cost_est"]
        return None if v is None else Decimal(v)

    assert by["priced"]["price_source"] == "provider" and await cost("priced") == Decimal("0.003")  # 1000*1/M + 500*4/M
    r = await c.put(
        f"{API}/models/{by['priced']['id']}/price", headers=h, json={"input_per_mtok": 2, "output_per_mtok": 6}
    )
    assert r.json()["price_source"] == "user" and await cost("priced") == Decimal("0.005")  # your price wins
    assert (await c.delete(f"{API}/models/{by['priced']['id']}/price", headers=h)).json()["price_source"] == "provider"
    assert await cost("priced") == Decimal("0.003")

    assert by["unpriced"]["price_source"] == "unknown" and await cost("unpriced") is None  # unknown stays unknown
    assert (
        by["vendor/x:free"]["is_free"]
        and by["vendor/x:free"]["price_source"] == "free"
        and await cost("vendor/x:free") == 0
    )

    u = (await c.get(f"{API}/usage", headers=h)).json()
    assert u["calls_with_unknown_cost"] == 1 and Decimal(u["cost_est"]) == Decimal("0.003") * 2 + Decimal("0.005")
    assert (
        await c.put(
            f"{API}/models/{by['priced']['id']}/price", headers=h, json={"input_per_mtok": -1, "output_per_mtok": 1}
        )
    ).status_code == 422


async def test_usage_report_validation_and_audit_trail(ctx, fake: FakeLLM) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text

    c, h, app = ctx
    await wire(c, h, fake, api_key="sk-test-audit-0123456789")
    await ask(c, h)
    assert (await c.get(f"{API}/usage", headers=h, params={"days": 0})).status_code == 422
    assert (await c.get(f"{API}/usage", headers=h, params={"days": 7})).json()["calls"] == 1
    async with app.state.sessionmaker() as s:
        actions = {
            a for (a,) in (await s.execute(text("select action from audit_logs where action like 'ai.%'"))).all()
        }
    assert {"ai.provider_created", "ai.models_discovered", "ai.routes_changed"} <= actions
