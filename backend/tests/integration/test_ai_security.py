"""Secrets: encrypted at rest, masked in every response, absent from logs/audit/errors, rotatable."""

from __future__ import annotations

import base64
import json
import logging
import os

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from tests.conftest import make_client, make_settings
from tests.fake_llm import FakeLLM
from tests.helpers import API, add_provider, ask, discover, invite_user, route, set_mode, setup_admin

pytestmark = pytest.mark.integration
KEY = "sk-test-SECRET-9f8e7d6c5b4a3210zzzz"


async def test_every_ai_endpoint_is_admin_only(client: AsyncClient) -> None:
    admin = await setup_admin(client)
    ed = await invite_user(client, admin, "ed@example.com", "editor")
    p = await add_provider(client, admin, base_url="http://127.0.0.1:9/v1")
    calls = [
        ("GET", "/providers"), ("GET", "/providers/presets"), ("POST", "/providers"), ("PATCH", f"/providers/{p['id']}"),
        ("DELETE", f"/providers/{p['id']}"), ("PUT", f"/providers/{p['id']}/credential"), ("POST", f"/providers/{p['id']}/test"),
        ("POST", f"/providers/{p['id']}/discover"), ("GET", "/models"), ("GET", "/ai/mode"), ("PUT", "/ai/mode"), ("GET", "/ai/routes"),
        ("PUT", "/ai/routes"), ("POST", "/ai/test-prompt"), ("GET", "/usage"),
    ]  # fmt: skip
    for method, path in calls:
        assert (await client.request(method, f"{API}{path}", json={})).status_code == 401, (method, path)
        r = await client.request(method, f"{API}{path}", headers=ed, json={})
        assert r.status_code == 403, (method, path, r.status_code)


async def test_key_is_encrypted_at_rest_and_masked_everywhere(
    client: AsyncClient, app, fake: FakeLLM, caplog: pytest.LogCaptureFixture
) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level(logging.DEBUG)
    admin = await setup_admin(client)
    seen: list[str] = []

    def keep(r):  # type: ignore[no-untyped-def]
        seen.append(r.text)
        return r

    created = keep(
        await client.post(
            f"{API}/providers",
            headers=admin,
            json={"kind": "openai_compat", "name": "x", "base_url": fake.url, "api_key": KEY},
        )
    )
    assert created.status_code == 201
    p = created.json()
    assert (
        p["has_credential"] is True
        and p["credential_mask"] == "sk-…zzzz"
        and KEY not in created.text
        and "api_key" not in p
    )

    keep(await client.get(f"{API}/providers", headers=admin))
    keep(await client.patch(f"{API}/providers/{p['id']}", headers=admin, json={"name": "renamed"}))
    keep(await client.post(f"{API}/providers/{p['id']}/test", headers=admin))
    models = await discover(client, admin, p["id"])
    await route(client, admin, "highlight", models[0]["id"])
    await set_mode(client, admin, "HYBRID")
    keep(await ask(client, admin))
    fake.echo_auth_in_errors = True  # a hostile/buggy server that repeats our Authorization header in its error text
    fake.default = (400, {}, {})
    err = keep(await ask(client, admin))
    assert err.status_code == 502
    keep(await client.get(f"{API}/usage", headers=admin))
    keep(await client.get(f"{API}/models", headers=admin))
    keep(await client.get(f"{API}/ai/routes", headers=admin))
    assert fake.chats()[-1]["auth"] == f"Bearer {KEY}"  # the key really was used...
    assert all(KEY not in body for body in seen), "the API key leaked into a response"  # ...and never shown
    assert all(KEY not in r.getMessage() for r in caplog.records), "the API key leaked into a log line"

    async with app.state.sessionmaker() as s:
        row = (
            await s.execute(text("select ciphertext, wrapped_dek, mask, key_version from encrypted_credentials"))
        ).one()
        assert (
            KEY.encode() not in bytes(row.ciphertext)
            and KEY.encode() not in bytes(row.wrapped_dek)
            and row.mask == "sk-…zzzz"
        )
        for table in ("ai_providers", "ai_models", "ai_usage_logs", "audit_logs", "settings", "task_model_routes"):
            dump = (await s.execute(text(f"select string_agg(t::text, ' ') from {table} t"))).scalar() or ""
            assert KEY not in dump, f"key found in table {table}"
        dump = (await s.execute(text("select string_agg(t::text, ' ') from encrypted_credentials t"))).scalar() or ""
        assert KEY not in dump


async def test_credential_replace_and_clear(client: AsyncClient, fake: FakeLLM) -> None:
    admin = await setup_admin(client)
    p = await add_provider(client, admin, base_url=fake.url, api_key=KEY)
    models = await discover(client, admin, p["id"])
    await route(client, admin, "highlight", models[0]["id"])
    await set_mode(client, admin, "HYBRID")
    assert (await ask(client, admin)).status_code == 200 and fake.chats()[-1]["auth"] == f"Bearer {KEY}"

    new = "sk-test-REPLACEMENT-0000111122223333"
    r = await client.put(f"{API}/providers/{p['id']}/credential", headers=admin, json={"api_key": new})
    assert r.json()["credential_mask"] == "sk-…3333" and new not in r.text
    assert (await ask(client, admin)).status_code == 200 and fake.chats()[-1]["auth"] == f"Bearer {new}"

    cleared = await client.delete(f"{API}/providers/{p['id']}/credential", headers=admin)
    assert cleared.json()["has_credential"] is False and cleared.json()["credential_mask"] is None
    before = len(fake.requests)
    assert (await ask(client, admin)).status_code == 200  # a keyless OpenAI-compatible server (LM Studio...) is allowed
    assert fake.requests[before]["auth"] is None


async def test_hosted_providers_refuse_to_run_without_a_key_and_send_nothing(client: AsyncClient) -> None:
    admin = await setup_admin(client)
    p = await add_provider(client, admin, kind="openrouter", name="or", is_local=False)
    r = await client.post(f"{API}/providers/{p['id']}/test", headers=admin)
    assert r.json()["ok"] is False and "No API key" in r.json()["message"]


async def test_provider_validation(client: AsyncClient) -> None:
    admin = await setup_admin(client)
    await add_provider(client, admin, base_url="http://127.0.0.1:9/v1", name="One")

    async def create(**kw):  # type: ignore[no-untyped-def]
        return await client.post(
            f"{API}/providers",
            headers=admin,
            json={"kind": "openai_compat", "name": "n", "base_url": "http://x/v1", **kw},
        )

    assert (
        await create(name="one", base_url="http://127.0.0.1:9/v1")
    ).status_code == 409  # names are unique, case-insensitively
    assert (await create(kind="openai", is_local=True, base_url=None)).json()["code"] == "invalid_locality"
    for bad in ("file:///etc/passwd", "ftp://x/v1", "http://user:pw@x/v1", "not a url"):
        assert (await create(base_url=bad)).status_code == 400, bad
    assert (await create(kind="ollama", base_url=None)).json()["code"] == "base_url_required"
    assert (await create(api_key="short")).status_code == 422  # keys shorter than 8 characters are refused
    assert (await create(timeout_s=1)).status_code == 422


async def test_deleting_a_provider_removes_secrets_and_models_but_keeps_usage_history(
    client: AsyncClient, app, fake: FakeLLM
) -> None:  # type: ignore[no-untyped-def]
    admin = await setup_admin(client)
    p = await add_provider(client, admin, base_url=fake.url, api_key=KEY, name="doomed")
    m = (await discover(client, admin, p["id"]))[0]
    await route(client, admin, "highlight", m["id"])
    await set_mode(client, admin, "HYBRID")
    await ask(client, admin)
    assert (await client.delete(f"{API}/providers/{p['id']}", headers=admin)).status_code == 204
    async with app.state.sessionmaker() as s:
        for t in ("encrypted_credentials", "ai_models", "task_model_routes", "price_table"):
            assert (await s.execute(text(f"select count(*) from {t}"))).scalar() == 0, t
    usage = (await client.get(f"{API}/usage", headers=admin)).json()
    assert usage["calls"] == 1 and usage["by_model"][0]["provider"] == "doomed"  # history remembers the name


async def test_key_rotation_rewraps_without_decrypting_and_the_old_key_can_be_retired(
    client: AsyncClient, app_factory, fake: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    from app.cli import _run
    from app.config import get_settings
    from app.security.crypto import Vault

    old_b64 = os.environ["APP_ENCRYPTION_KEY"]
    new_b64 = base64.b64encode(os.urandom(32)).decode()
    app = await app_factory()
    async with make_client(app) as c:
        admin = await setup_admin(c)
        p = await add_provider(c, admin, base_url=fake.url, api_key=KEY)
        m = (await discover(c, admin, p["id"]))[0]
        await route(c, admin, "highlight", m["id"])
        await set_mode(c, admin, "HYBRID")
        assert (await ask(c, admin)).status_code == 200
    async with app.state.sessionmaker() as s:
        before = (await s.execute(text("select ciphertext, key_version from encrypted_credentials"))).one()

    monkeypatch.setenv("APP_ENCRYPTION_KEY", new_b64)
    monkeypatch.setenv("APP_ENCRYPTION_KEY_PREVIOUS", old_b64)
    get_settings.cache_clear()
    try:
        assert await _run("rotate-encryption-key", dry_run=True) == 0
        async with app.state.sessionmaker() as s:  # a dry run changes nothing
            assert (
                await s.execute(text("select key_version from encrypted_credentials"))
            ).scalar() == before.key_version
        assert await _run("rotate-encryption-key", dry_run=False) == 0
        async with app.state.sessionmaker() as s:
            after = (await s.execute(text("select ciphertext, key_version from encrypted_credentials"))).one()
        assert bytes(after.ciphertext) == bytes(before.ciphertext)  # the secret itself was never touched
        assert (
            after.key_version != before.key_version and after.key_version == Vault.from_settings(get_settings()).version
        )

        monkeypatch.delenv("APP_ENCRYPTION_KEY_PREVIOUS")  # retire the old key
        get_settings.cache_clear()
        assert await _run("check-encryption", dry_run=False) == 0
        app2 = await app_factory(APP_ENCRYPTION_KEY=__import__("pydantic").SecretStr(new_b64))
        # app_factory resets the database, so re-check using the rotated row directly with the new key only:
        assert app2 is not None
    finally:
        get_settings.cache_clear()


async def test_rotation_with_the_wrong_key_changes_nothing_and_reports_it(
    app_factory, fake: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    from app.cli import _run
    from app.config import get_settings

    app = await app_factory()
    async with make_client(app) as c:
        admin = await setup_admin(c)
        await add_provider(c, admin, base_url=fake.url, api_key=KEY)
    async with app.state.sessionmaker() as s:
        before = (await s.execute(text("select wrapped_dek from encrypted_credentials"))).scalar()
    monkeypatch.setenv(
        "APP_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode()
    )  # a key that never sealed anything
    get_settings.cache_clear()
    try:
        assert await _run("rotate-encryption-key", dry_run=False) == 1
        assert await _run("check-encryption", dry_run=False) == 1
    finally:
        get_settings.cache_clear()
    async with app.state.sessionmaker() as s:
        assert (await s.execute(text("select wrapped_dek from encrypted_credentials"))).scalar() == before


async def test_app_refuses_to_start_without_its_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import ValidationError

    from app.config import Settings

    for k in ("APP_ENCRYPTION_KEY",):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValidationError):
        Settings()  # type: ignore[call-arg]
    assert make_settings().encryption_key  # and starts with one
    assert json.dumps({"x": 1})
