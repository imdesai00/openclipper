from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text, update

from app.db.models import AuditLog, Invitation, UserSession
from tests.conftest import ADMIN, WEB, make_client

pytestmark = pytest.mark.integration
API = "/api/v1"


async def actions(app) -> list[str]:  # type: ignore[no-untyped-def]
    async with app.state.sessionmaker() as s:
        return [r for (r,) in (await s.execute(select(AuditLog.action).order_by(AuditLog.id))).all()]


async def test_setup_flow_and_one_time_only(client: AsyncClient, app) -> None:  # type: ignore[no-untyped-def]
    assert (await client.get(f"{API}/setup/status")).json() == {"setup_required": True}
    r = await client.post(f"{API}/setup/admin", json=ADMIN)
    assert r.status_code == 201
    body = r.json()
    assert body["user"]["role"] == "admin" and body["user"]["email"] == ADMIN["email"]
    assert "password" not in r.text and "password_hash" not in r.text
    assert (await client.get(f"{API}/setup/status")).json() == {"setup_required": False}
    again = await client.post(f"{API}/setup/admin", json={**ADMIN, "email": "evil@example.com"})
    assert again.status_code == 409 and again.json()["code"] == "setup_complete"
    assert "setup.admin_created" in await actions(app)


async def test_concurrent_setup_only_one_wins(app) -> None:  # type: ignore[no-untyped-def]
    async def go(i: int) -> int:
        async with make_client(app) as c:
            r = await c.post(f"{API}/setup/admin", json={**ADMIN, "email": f"a{i}@example.com"})
            return r.status_code

    codes = await asyncio.gather(*(go(i) for i in range(5)))
    assert sorted(codes) == [201, 409, 409, 409, 409]


async def test_setup_validation_envelope(client: AsyncClient) -> None:
    r = await client.post(f"{API}/setup/admin", json={**ADMIN, "password": "short"})
    assert r.status_code == 422
    j = r.json()
    assert j["code"] == "validation_error" and j["request_id"] and isinstance(j["details"], list)
    assert r.headers["x-request-id"] == j["request_id"]


async def test_login_me_and_cookie_flags(client: AsyncClient, admin) -> None:  # type: ignore[no-untyped-def]
    r = await client.post(f"{API}/auth/login", json={"email": "ADMIN@example.com ", "password": ADMIN["password"]})
    assert r.status_code == 200, r.text
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" in cookie
    assert "path=/api/v1/auth" in cookie
    assert r.headers["cache-control"] == "no-store"
    me = await client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert me.status_code == 200 and me.json()["email"] == ADMIN["email"]


async def test_bad_credentials_indistinguishable(client: AsyncClient, admin, app) -> None:  # type: ignore[no-untyped-def]
    wrong_pw = await client.post(f"{API}/auth/login", json={"email": ADMIN["email"], "password": "nope-nope-nope"})
    unknown = await client.post(f"{API}/auth/login", json={"email": "ghost@example.com", "password": "nope-nope-nope"})
    assert wrong_pw.status_code == unknown.status_code == 401
    assert wrong_pw.json()["code"] == unknown.json()["code"] == "invalid_credentials"
    assert wrong_pw.json()["message"] == unknown.json()["message"]
    assert (await actions(app)).count("auth.login_failed") == 2


async def test_unauthenticated_and_garbage_token(client: AsyncClient) -> None:
    assert (await client.get(f"{API}/auth/me")).status_code == 401
    r = await client.get(f"{API}/auth/me", headers={"Authorization": "Bearer garbage"})
    assert r.status_code == 401 and r.json()["code"] == "invalid_token"


async def test_refresh_rotation_and_reuse_detection(client: AsyncClient, admin, app) -> None:  # type: ignore[no-untyped-def]
    login = await client.post(f"{API}/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"]})
    old_cookie = client.cookies.get("oc_refresh", path="/api/v1/auth")
    assert old_cookie

    r1 = await client.post(f"{API}/auth/refresh", headers=WEB)
    assert r1.status_code == 200
    new_cookie = client.cookies.get("oc_refresh", path="/api/v1/auth")
    assert new_cookie and new_cookie != old_cookie

    # Attacker replays the already-rotated token.
    async with make_client(app) as attacker:
        bad = await attacker.post(f"{API}/auth/refresh", headers={**WEB, "Cookie": f"oc_refresh={old_cookie}"})
    assert bad.status_code == 401 and bad.json()["code"] == "refresh_reuse_detected"

    # The legitimate (newest) token is now dead too: whole family revoked.
    r2 = await client.post(f"{API}/auth/refresh", headers=WEB)
    assert r2.status_code == 401
    assert "auth.refresh_reuse_detected" in await actions(app)
    assert login.status_code == 200


async def test_refresh_requires_csrf_header_and_cookie(client: AsyncClient, admin) -> None:  # type: ignore[no-untyped-def]
    no_header = await client.post(f"{API}/auth/refresh")
    assert no_header.status_code == 403
    async with make_client(client._transport.app) as fresh:  # type: ignore[attr-defined]
        r = await fresh.post(f"{API}/auth/refresh", headers=WEB)
        assert r.status_code == 401 and r.json()["code"] == "no_session"


async def test_logout_revokes_session(client: AsyncClient, admin) -> None:  # type: ignore[no-untyped-def]
    cookie = client.cookies.get("oc_refresh", path="/api/v1/auth")
    assert (await client.post(f"{API}/auth/logout", headers=WEB)).status_code == 204
    async with make_client(client._transport.app) as other:  # type: ignore[attr-defined]
        r = await other.post(f"{API}/auth/refresh", headers={**WEB, "Cookie": f"oc_refresh={cookie}"})
    assert r.status_code == 401 and r.json()["code"] == "invalid_session"


async def test_expired_refresh_rejected(client: AsyncClient, admin, app) -> None:  # type: ignore[no-untyped-def]
    async with app.state.sessionmaker() as s:
        await s.execute(update(UserSession).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        await s.commit()
    r = await client.post(f"{API}/auth/refresh", headers=WEB)
    assert r.status_code == 401 and r.json()["code"] == "session_expired"


async def test_login_rate_limit(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = await app_factory(LOGIN_RATE_LIMIT_PER_EMAIL=3)
    async with make_client(app) as c:
        codes = [
            (
                await c.post(
                    f"{API}/auth/login",
                    json={"email": "x@example.com", "password": "wrong-wrong-wrong"},
                )
            ).status_code
            for _ in range(5)
        ]
        assert codes == [401, 401, 401, 429, 429]
        r = await c.post(f"{API}/auth/login", json={"email": "x@example.com", "password": "wrong-wrong-wrong"})
        assert r.headers["retry-after"].isdigit() and r.json()["code"] == "rate_limited"


async def test_global_api_rate_limit(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = await app_factory(API_RATE_LIMIT_PER_MINUTE=3)
    async with make_client(app) as c:
        codes = [(await c.get(f"{API}/setup/status")).status_code for _ in range(5)]
        assert codes == [200, 200, 200, 429, 429]
        assert (await c.get(f"{API}/health/live")).status_code == 200  # health exempt


async def test_roles_and_invite_flow(client: AsyncClient, admin, app) -> None:  # type: ignore[no-untyped-def]
    assert (await client.post(f"{API}/users/invite", json={"email": "ed@example.com"})).status_code == 401
    inv = await client.post(f"{API}/users/invite", headers=admin, json={"email": "Ed@Example.com", "role": "editor"})
    assert inv.status_code == 201
    token = inv.json()["invite_token"]
    dup = await client.post(f"{API}/users/invite", headers=admin, json={"email": ADMIN["email"]})
    assert dup.status_code == 409

    async with app.state.sessionmaker() as s:  # only a hash is stored
        stored = (await s.execute(select(Invitation.token_hash))).scalar_one()
    assert token not in stored

    weak = await client.post(
        f"{API}/auth/accept-invite",
        json={"token": token, "display_name": "Ed", "password": "short"},
    )
    assert weak.status_code == 422
    ok = await client.post(
        f"{API}/auth/accept-invite",
        json={"token": token, "display_name": "Ed", "password": "editor password 123"},
    )
    assert ok.status_code == 201 and ok.json()["user"]["role"] == "editor"
    editor = {"Authorization": f"Bearer {ok.json()['access_token']}"}

    assert (
        await client.post(f"{API}/users/invite", headers=editor, json={"email": "x@example.com"})
    ).status_code == 403
    assert (await client.get(f"{API}/users", headers=editor)).status_code == 403
    reuse = await client.post(
        f"{API}/auth/accept-invite",
        json={"token": token, "display_name": "Ed", "password": "editor password 123"},
    )
    assert reuse.status_code == 400 and reuse.json()["code"] == "invalid_invite"


async def test_expired_invite_rejected(client: AsyncClient, admin, app) -> None:  # type: ignore[no-untyped-def]
    inv = await client.post(f"{API}/users/invite", headers=admin, json={"email": "late@example.com"})
    async with app.state.sessionmaker() as s:
        await s.execute(update(Invitation).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
        await s.commit()
    r = await client.post(
        f"{API}/auth/accept-invite",
        json={
            "token": inv.json()["invite_token"],
            "display_name": "L",
            "password": "late password 1234",
        },
    )
    assert r.status_code == 400


async def test_user_management_guards(client: AsyncClient, admin) -> None:  # type: ignore[no-untyped-def]
    users = (await client.get(f"{API}/users", headers=admin)).json()["items"]
    me_id = users[0]["id"]
    r = await client.patch(f"{API}/users/{me_id}", headers=admin, json={"is_active": False})
    assert r.status_code == 400 and r.json()["code"] == "self_lockout"
    r = await client.patch(f"{API}/users/{me_id}", headers=admin, json={"role": "viewer"})
    assert r.json()["code"] == "self_lockout"


async def test_deactivated_user_loses_access_immediately(client: AsyncClient, admin) -> None:  # type: ignore[no-untyped-def]
    inv = await client.post(f"{API}/users/invite", headers=admin, json={"email": "v@example.com", "role": "viewer"})
    acc = await client.post(
        f"{API}/auth/accept-invite",
        json={
            "token": inv.json()["invite_token"],
            "display_name": "V",
            "password": "viewer password 12",
        },
    )
    vid = acc.json()["user"]["id"]
    viewer = {"Authorization": f"Bearer {acc.json()['access_token']}"}
    assert (await client.get(f"{API}/auth/me", headers=viewer)).status_code == 200
    assert (await client.patch(f"{API}/users/{vid}", headers=admin, json={"is_active": False})).status_code == 200
    r = await client.get(f"{API}/auth/me", headers=viewer)
    assert r.status_code == 401 and r.json()["code"] == "account_disabled"
    login = await client.post(f"{API}/auth/login", json={"email": "v@example.com", "password": "viewer password 12"})
    assert login.status_code == 401


async def test_cursor_pagination(client: AsyncClient, admin) -> None:  # type: ignore[no-untyped-def]
    for i in range(2):
        inv = await client.post(f"{API}/users/invite", headers=admin, json={"email": f"u{i}@example.com"})
        await client.post(
            f"{API}/auth/accept-invite",
            json={
                "token": inv.json()["invite_token"],
                "display_name": f"U{i}",
                "password": "some password 1234",
            },
        )
    seen: list[str] = []
    cursor = None
    for _ in range(5):
        r = await client.get(
            f"{API}/users",
            headers=admin,
            params={"limit": 1, **({"cursor": cursor} if cursor else {})},
        )
        page = r.json()
        seen += [u["email"] for u in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 3 and len(set(seen)) == 3
    bad = await client.get(f"{API}/users", headers=admin, params={"cursor": "nope"})
    assert bad.status_code == 400


async def test_security_headers_and_docs_disabled(client: AsyncClient) -> None:
    r = await client.get(f"{API}/setup/status")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert (await client.get("/api/docs")).status_code == 404
    assert (await client.get("/api/openapi.json")).status_code == 404


async def test_docs_can_be_enabled(app_factory) -> None:  # type: ignore[no-untyped-def]
    app = await app_factory(ENABLE_API_DOCS=True)
    async with make_client(app) as c:
        assert (await c.get("/api/openapi.json")).status_code == 200


async def test_secrets_not_in_any_response(client: AsyncClient, admin) -> None:  # type: ignore[no-untyped-def]
    r = await client.get(f"{API}/users", headers=admin)
    assert "hash" not in r.text and "argon2" not in r.text
    async with client._transport.app.state.sessionmaker() as s:  # type: ignore[attr-defined]
        h = (await s.execute(text("select password_hash from users"))).scalar_one()
    assert h.startswith("$argon2id$")
