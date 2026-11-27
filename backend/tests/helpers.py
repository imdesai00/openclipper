from __future__ import annotations

import asyncio
import base64
import hashlib
import time
from pathlib import Path
from typing import Any

import httpx

API = "/api/v1"
TUS = {"Tus-Resumable": "1.0.0"}
ADMIN = {"email": "admin@example.com", "display_name": "Admin", "password": "correct horse battery"}


def b64(v: str) -> str:
    return base64.b64encode(v.encode()).decode()


async def setup_admin(c: httpx.AsyncClient) -> dict[str, str]:
    r = await c.post(f"{API}/setup/admin", json=ADMIN)
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def invite_user(c: httpx.AsyncClient, admin: dict[str, str], email: str, role: str = "editor") -> dict[str, str]:
    r = await c.post(f"{API}/users/invite", headers=admin, json={"email": email, "role": role})
    assert r.status_code == 201, r.text
    a = await c.post(f"{API}/auth/accept-invite",
                     json={"token": r.json()["invite_token"], "display_name": email.split("@")[0], "password": "some long password 1"})  # fmt: skip
    assert a.status_code == 201, a.text
    return {"Authorization": f"Bearer {a.json()['access_token']}"}


async def make_project(c: httpx.AsyncClient, h: dict[str, str], name: str = "Podcast") -> str:
    r = await c.post(f"{API}/projects", headers=h, json={"name": name})
    assert r.status_code == 201, r.text
    return str(r.json()["id"])


def tus_metadata(project_id: str, filename: str, attested: bool = True) -> str:
    md = {"project_id": project_id, "filename": filename, "attested": "true" if attested else "false"}
    return ",".join(f"{k} {b64(v)}" for k, v in md.items())


async def tus_create(c: httpx.AsyncClient, h: dict[str, str], project_id: str, filename: str, length: int,
                     attested: bool = True) -> httpx.Response:  # fmt: skip
    return await c.post(f"{API}/uploads", headers={**h, **TUS, "Upload-Length": str(length),
                                                  "Upload-Metadata": tus_metadata(project_id, filename, attested)})  # fmt: skip


async def tus_patch(c: httpx.AsyncClient, h: dict[str, str], location: str, offset: int, data: bytes) -> httpx.Response:
    return await c.patch(location, headers={**h, **TUS, "Upload-Offset": str(offset),
                                           "Content-Type": "application/offset+octet-stream"}, content=data)  # fmt: skip


async def tus_upload(c: httpx.AsyncClient, h: dict[str, str], project_id: str, path: Path, *,
                     filename: str | None = None, chunk: int = 1 << 20) -> str:  # fmt: skip
    """Upload a file in chunks; returns the new source id."""
    data = path.read_bytes()
    r = await tus_create(c, h, project_id, filename or path.name, len(data))
    assert r.status_code == 201, r.text
    loc, off, source_id = r.headers["Location"], 0, None
    while off < len(data):
        p = await tus_patch(c, h, loc, off, data[off : off + chunk])
        assert p.status_code == 204, p.text
        off = int(p.headers["Upload-Offset"])
        source_id = p.headers.get("X-Source-Id") or source_id
    assert source_id, "completion did not return a source id"
    return source_id


async def wait_source(c: httpx.AsyncClient, h: dict[str, str], source_id: str, *, until: tuple[str, ...] = ("ready", "failed"),
                      timeout: float = 120) -> dict[str, Any]:  # fmt: skip
    end = time.monotonic() + timeout
    while True:
        r = await c.get(f"{API}/sources/{source_id}", headers=h)
        assert r.status_code == 200, r.text
        if r.json()["status"] in until:
            return dict(r.json())
        if time.monotonic() > end:
            raise AssertionError(f"source stuck: {r.json()}")
        await asyncio.sleep(0.25)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
