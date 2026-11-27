from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.db.models import AuditLog
from tests.helpers import API, invite_user, make_project, setup_admin

pytestmark = pytest.mark.integration


async def _audit(app, action: str) -> int:  # type: ignore[no-untyped-def]
    async with app.state.sessionmaker() as s:
        return len((await s.execute(select(AuditLog).where(AuditLog.action == action))).scalars().all())


async def test_create_list_get_update(client: AsyncClient, app) -> None:  # type: ignore[no-untyped-def]
    admin = await setup_admin(client)
    ed = await invite_user(client, admin, "ed@example.com", "editor")
    pid = await make_project(client, ed, "Ed's show")

    got = (await client.get(f"{API}/projects/{pid}", headers=ed)).json()
    assert got["name"] == "Ed's show" and got["my_role"] == "owner" and got["source_count"] == 0
    upd = await client.patch(f"{API}/projects/{pid}", headers=ed, json={"name": "Renamed", "description": "d"})
    assert upd.json()["name"] == "Renamed" and upd.json()["description"] == "d"
    assert [p["name"] for p in (await client.get(f"{API}/projects", headers=ed)).json()["items"]] == ["Renamed"]
    assert await _audit(app, "project.created") == 1


async def test_validation_and_unauthenticated(client: AsyncClient) -> None:
    admin = await setup_admin(client)
    assert (await client.get(f"{API}/projects")).status_code == 401
    assert (await client.post(f"{API}/projects", headers=admin, json={"name": ""})).status_code == 422
    assert (await client.post(f"{API}/projects", headers=admin, json={"name": "x" * 201})).status_code == 422
    assert (await client.get(f"{API}/projects/not-a-uuid", headers=admin)).status_code == 422


async def test_users_only_see_their_own_projects_and_non_members_get_404(client: AsyncClient) -> None:
    admin = await setup_admin(client)
    a = await invite_user(client, admin, "a@example.com", "editor")
    b = await invite_user(client, admin, "b@example.com", "editor")
    pa = await make_project(client, a, "A's")
    pb = await make_project(client, b, "B's")

    assert [p["name"] for p in (await client.get(f"{API}/projects", headers=a)).json()["items"]] == ["A's"]
    # Another user's project looks exactly like a missing one: no existence leak.
    for call in (
        client.get(f"{API}/projects/{pb}", headers=a),
        client.get(f"{API}/projects/{pb}/sources", headers=a),
        client.patch(f"{API}/projects/{pb}", headers=a, json={"name": "hijack"}),
        client.get(f"{API}/projects/{pb}/members", headers=a),
    ):
        r = await call
        assert r.status_code == 404 and r.json()["code"] == "not_found"
    # Admins see everything.
    names = sorted(p["name"] for p in (await client.get(f"{API}/projects", headers=admin)).json()["items"])
    assert names == ["A's", "B's"]
    assert (await client.get(f"{API}/projects/{pa}", headers=admin)).json()["my_role"] == "owner"


async def test_membership_roles(client: AsyncClient) -> None:
    admin = await setup_admin(client)
    owner = await invite_user(client, admin, "o@example.com", "editor")
    mate = await invite_user(client, admin, "m@example.com", "editor")
    viewer = await invite_user(client, admin, "v@example.com", "viewer")
    pid = await make_project(client, owner, "Shared")

    r = await client.post(
        f"{API}/projects/{pid}/members", headers=owner, json={"email": "M@Example.com", "role": "editor"}
    )
    assert r.status_code == 201 and r.json()["role"] == "editor"
    assert (await client.post(f"{API}/projects/{pid}/members", headers=owner, json={"email": "m@example.com", "role": "viewer"})).status_code == 409  # fmt: skip
    assert (await client.post(f"{API}/projects/{pid}/members", headers=owner, json={"email": "nobody@example.com", "role": "viewer"})).status_code == 404  # fmt: skip
    assert (await client.post(f"{API}/projects/{pid}/members", headers=owner, json={"email": "v@example.com", "role": "viewer"})).status_code == 201  # fmt: skip

    # An editor-member can read but cannot manage members or rename.
    assert (await client.get(f"{API}/projects/{pid}", headers=mate)).json()["my_role"] == "editor"
    assert (await client.patch(f"{API}/projects/{pid}", headers=mate, json={"name": "x"})).status_code == 403
    assert (await client.post(f"{API}/projects/{pid}/members", headers=mate, json={"email": "v@example.com", "role": "editor"})).status_code == 403  # fmt: skip
    # A global viewer is read-only everywhere, and cannot create projects.
    assert (await client.get(f"{API}/projects/{pid}", headers=viewer)).status_code == 200
    assert (await client.post(f"{API}/projects", headers=viewer, json={"name": "nope"})).status_code == 403
    assert len((await client.get(f"{API}/projects/{pid}/members", headers=viewer)).json()) == 3

    owner_id = next(m["user_id"] for m in (await client.get(f"{API}/projects/{pid}/members", headers=owner)).json() if m["role"] == "owner")  # fmt: skip
    assert (await client.delete(f"{API}/projects/{pid}/members/{owner_id}", headers=owner)).status_code == 400
    mate_id = next(m["user_id"] for m in (await client.get(f"{API}/projects/{pid}/members", headers=owner)).json() if m["email"] == "m@example.com")  # fmt: skip
    assert (await client.delete(f"{API}/projects/{pid}/members/{mate_id}", headers=owner)).status_code == 204
    assert (await client.get(f"{API}/projects/{pid}", headers=mate)).status_code == 404  # access ends immediately


async def test_delete_requires_typed_name_and_owner(client: AsyncClient, app) -> None:  # type: ignore[no-untyped-def]
    admin = await setup_admin(client)
    owner = await invite_user(client, admin, "o@example.com", "editor")
    other = await invite_user(client, admin, "x@example.com", "editor")
    pid = await make_project(client, owner, "My Show")

    assert (await client.request("DELETE", f"{API}/projects/{pid}", headers=other, json={"confirm_name": "My Show"})).status_code == 404  # fmt: skip
    bad = await client.request("DELETE", f"{API}/projects/{pid}", headers=owner, json={"confirm_name": "my show"})
    assert bad.status_code == 400 and bad.json()["code"] == "confirmation_mismatch"
    assert (await client.get(f"{API}/projects/{pid}", headers=owner)).status_code == 200
    ok = await client.request("DELETE", f"{API}/projects/{pid}", headers=owner, json={"confirm_name": "My Show"})
    assert ok.status_code == 204
    assert (await client.get(f"{API}/projects/{pid}", headers=owner)).status_code == 404
    assert await _audit(app, "project.deleted") == 1


async def test_project_cursor_pagination(client: AsyncClient) -> None:
    admin = await setup_admin(client)
    for i in range(5):
        await make_project(client, admin, f"P{i}")
    seen, cursor = [], None
    for _ in range(10):
        page = (await client.get(f"{API}/projects", headers=admin, params={"limit": 2, **({"cursor": cursor} if cursor else {})})).json()  # fmt: skip
        seen += [p["name"] for p in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert sorted(seen) == [f"P{i}" for i in range(5)]


async def test_dashboard_counts_are_real_and_scoped(client: AsyncClient, app) -> None:  # type: ignore[no-untyped-def]
    from app.db.models import MediaAsset, VideoSource

    admin = await setup_admin(client)
    a = await invite_user(client, admin, "a@example.com", "editor")
    b = await invite_user(client, admin, "b@example.com", "editor")
    assert (await client.get(f"{API}/dashboard", headers=a)).json() == {
        "projects": 0, "sources": 0, "sources_ready": 0, "sources_failed": 0, "jobs_running": 0, "storage_bytes": 0,
        "clips": 0, "clips_rendered": 0, "renders_failed_qc": 0, "jobs_failed": 0, "recent_clips": [],
    }  # fmt: skip
    pa, pb = await make_project(client, a, "A"), await make_project(client, b, "B")
    async with app.state.sessionmaker() as s:
        uid = (await client.get(f"{API}/auth/me", headers=a)).json()["id"]
        import uuid

        for pid, status, size in ((pa, "ready", 100), (pa, "failed", 50), (pb, "ready", 999)):
            s.add(
                VideoSource(
                    project_id=uuid.UUID(pid), kind="upload", title="t", status=status, created_by=uuid.UUID(uid)
                )
            )
            s.add(MediaAsset(project_id=uuid.UUID(pid), kind="proxy", storage_backend="local", storage_key=f"{pid}/proxy/{uuid.uuid4().hex}.mp4", size_bytes=size, mime="video/mp4"))  # fmt: skip
        await s.commit()
    mine = (await client.get(f"{API}/dashboard", headers=a)).json()
    assert mine == {
        "projects": 1,
        "sources": 2,
        "sources_ready": 1,
        "sources_failed": 1,
        "jobs_running": 0,
        "storage_bytes": 150,
        "clips": 0,
        "clips_rendered": 0,
        "renders_failed_qc": 0,
        "jobs_failed": 0,
        "recent_clips": [],
    }
    assert (await client.get(f"{API}/dashboard", headers=admin)).json()["storage_bytes"] == 1149  # admin sees all
    assert (await client.get(f"{API}/dashboard")).status_code == 401
