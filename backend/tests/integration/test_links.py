"""Link sources: direct URLs end to end (real HTTP server + worker), SSRF refusal, YouTube gating."""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.db.models import MediaAsset, SourcePermission, VideoSource
from app.services.ingest_settings import HEARTBEAT_KEY
from tests.helpers import API, invite_user, make_project, setup_admin, wait_source

pytestmark = pytest.mark.integration
VID = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


@pytest.fixture
def file_server(small_mp4: Path) -> Iterator[str]:
    video = small_mp4.read_bytes()

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send(self, status: int, ctype: str, body: bytes, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/talk.mp4":
                self._send(200, "video/mp4", video)
            elif self.path == "/redirect":
                self._send(302, "text/plain", b"", {"Location": "/talk.mp4"})
            elif self.path == "/disposition":
                self._send(
                    200,
                    "application/octet-stream",
                    video,
                    {"Content-Disposition": 'attachment; filename="Interview Final.mp4"'},
                )
            elif self.path == "/page.html":
                self._send(200, "text/html", b"<html>not a video</html>")
            elif self.path == "/stream":  # no Content-Length: must still be capped while streaming
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                for _ in range(200):
                    self.wfile.write(b"%x\r\n" % 4096 + b"x" * 4096 + b"\r\n")
                self.wfile.write(b"0\r\n\r\n")
            else:
                self._send(404, "text/plain", b"nope")

        def log_message(self, *a: object) -> None:
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def allow_private(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DIRECT_URL_ALLOW_PRIVATE", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _link(c: httpx.AsyncClient, h: dict[str, str], pid: str, url: str, attested: bool = True) -> httpx.Response:
    return await c.post(f"{API}/projects/{pid}/sources/link", headers=h, json={"url": url, "attested": attested})


async def test_direct_url_downloads_validates_and_processes(
    live_factory, media_worker, file_server, allow_private
) -> None:  # type: ignore[no-untyped-def]
    live = await live_factory(DIRECT_URL_ALLOW_PRIVATE=True)
    async with httpx.AsyncClient(base_url=live.url, timeout=60) as c:
        h = await setup_admin(c)
        pid = await make_project(c, h)
        r = await _link(c, h, pid, f"{file_server}/redirect")  # a redirect hop is followed (and re-checked)
        assert r.status_code == 202 and r.json()["kind"] == "direct_url"
        src = await wait_source(c, h, r.json()["id"])
        assert src["status"] == "ready", src
        assert [s["stage"] for s in src["job"]["stages"]] == ["download", "validate", "proxy", "waveform", "thumbnail"]
        assert src["original_filename"] == "talk.mp4" and src["proxy_url"] and src["height"] == 240
        async with live.app.state.sessionmaker() as s:
            assert (await s.execute(select(SourcePermission))).scalar_one().source_id is not None
            assert {a.kind for a in (await s.execute(select(MediaAsset))).scalars()} == {"original", "proxy", "waveform", "thumbnail"}  # fmt: skip

        d = await wait_source(c, h, (await _link(c, h, pid, f"{file_server}/disposition")).json()["id"])
        assert d["status"] == "ready" and d["original_filename"] == "Interview Final.mp4"


@pytest.mark.parametrize(
    ("path", "code"),
    [("/page.html", "bad_content_type"), ("/missing.mp4", "http_error")],
)
async def test_direct_url_failures_are_reported_not_hidden(
    live_factory, media_worker, file_server, allow_private, path: str, code: str
) -> None:  # type: ignore[no-untyped-def]
    live = await live_factory(DIRECT_URL_ALLOW_PRIVATE=True)
    async with httpx.AsyncClient(base_url=live.url, timeout=60) as c:
        h = await setup_admin(c)
        pid = await make_project(c, h)
        src = await wait_source(c, h, (await _link(c, h, pid, file_server + path)).json()["id"])
        assert src["status"] == "failed" and src["error_code"] == code and src["error_message"]
        assert src["job"]["stages"][0]["stage"] == "download" and src["job"]["stages"][0]["status"] == "failed"


async def test_download_size_limit_applies_to_declared_and_streamed_bodies(
    live_factory, media_worker, file_server, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("DIRECT_URL_ALLOW_PRIVATE", "true")
    monkeypatch.setenv("DIRECT_URL_MAX_BYTES", "10000")
    get_settings.cache_clear()
    try:
        live = await live_factory(DIRECT_URL_ALLOW_PRIVATE=True)
        async with httpx.AsyncClient(base_url=live.url, timeout=60) as c:
            h = await setup_admin(c)
            pid = await make_project(c, h)
            for path in ("/talk.mp4", "/stream"):  # declared Content-Length / no Content-Length
                src = await wait_source(c, h, (await _link(c, h, pid, file_server + path)).json()["id"])
                assert src["status"] == "failed" and src["error_code"] == "too_large", (path, src)
    finally:
        get_settings.cache_clear()


async def test_unsafe_links_are_refused_at_the_door(app, client, file_server) -> None:  # type: ignore[no-untyped-def]
    h = await setup_admin(client)
    pid = await make_project(client, h)
    for url, code in [
        (f"{file_server}/talk.mp4", "blocked_address"),  # loopback, because private targets are off by default
        ("http://169.254.169.254/latest/meta-data/", "blocked_address"),
        ("http://10.0.0.1/a.mp4", "blocked_address"),
        ("http://[::1]/a.mp4", "blocked_address"),
        ("file:///etc/passwd", "invalid_scheme"),
        ("ftp://8.8.8.8/a.mp4", "invalid_scheme"),
        ("http://user:pw@8.8.8.8/a.mp4", "credentials_in_url"),
    ]:
        r = await _link(client, h, pid, url)
        assert r.status_code == 400 and r.json()["code"] == code, (url, r.text)
    async with app.state.sessionmaker() as s:
        assert (await s.execute(select(VideoSource))).first() is None  # nothing was created


async def test_attestation_is_mandatory_for_links(client) -> None:  # type: ignore[no-untyped-def]
    h = await setup_admin(client)
    pid = await make_project(client, h)
    r = await _link(client, h, pid, "http://8.8.8.8/a.mp4", attested=False)
    assert r.status_code == 400 and r.json()["code"] == "attestation_required"
    r = await _link(client, h, pid, VID, attested=False)
    assert r.status_code == 400 and r.json()["code"] == "attestation_required"


async def test_youtube_links_are_off_by_default_and_gated(app, client) -> None:  # type: ignore[no-untyped-def]
    admin = await setup_admin(client)
    ed = await invite_user(client, admin, "ed@example.com", "editor")
    pid = await make_project(client, ed, "Ed")

    feats = (await client.get(f"{API}/features", headers=ed)).json()
    assert feats["ingest_enabled"] is False and feats["ingest_available"] is False

    r = await _link(client, ed, pid, VID)
    assert r.status_code == 403 and r.json()["code"] == "ingest_disabled"
    assert (
        await client.put(f"{API}/settings/ingest", headers=ed, json={"enabled": True, "daily_limit": 5})
    ).status_code == 403

    on = await client.put(f"{API}/settings/ingest", headers=admin, json={"enabled": True, "daily_limit": 2})
    assert on.status_code == 200 and on.json()["ingest_enabled"] is True and on.json()["ingest_available"] is False
    r = await _link(client, ed, pid, VID)
    assert r.status_code == 503 and r.json()["code"] == "ingest_unavailable"  # switch on, but no worker is running

    await app.state.redis.set(HEARTBEAT_KEY, "2099.1.1", ex=60)  # what the ingest worker does while alive
    assert (await client.get(f"{API}/features", headers=ed)).json()["ingest_available"] is True
    for bad in ("https://www.youtube.com/@somechannel", "https://www.youtube.com/playlist?list=PLabc"):
        r = await _link(client, ed, pid, bad)
        assert r.status_code == 400 and r.json()["code"] == "unsupported_link", bad

    ok = await _link(client, ed, pid, VID + "&list=PLxyz&t=5")
    assert ok.status_code == 202 and ok.json()["kind"] == "ytdlp"
    assert ok.json()["origin_url"] == VID  # normalized: playlist and time parameters dropped
    queued = await client.get(f"{API}/sources/{ok.json()['id']}", headers=ed)
    assert queued.json()["status"] == "pending" and queued.json()["job"]["stages"][0]["stage"] == "download"
    assert await app.state.redis.llen("ingest") == 1  # routed to the isolated ingest queue, not the media workers

    assert (await _link(client, ed, pid, VID.replace("dQw", "aaa"))).status_code == 202
    third = await _link(client, ed, pid, VID.replace("dQw", "bbb"))
    assert third.status_code == 429 and third.json()["code"] == "daily_limit_reached"

    job_id = queued.json()["job"]["id"]
    assert (await client.post(f"{API}/jobs/{job_id}/cancel", headers=ed)).status_code == 202
    assert (await client.get(f"{API}/jobs/{job_id}", headers=ed)).json()["status"] == "cancelled"
