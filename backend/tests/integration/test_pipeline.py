"""End-to-end through the real API, real tus uploads, a real Celery worker, real ffmpeg. No mocks."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.adapters.media.ffprobe import probe
from app.config import get_settings
from app.db.models import AssetLease, JobStage, MediaAsset, SourcePermission
from tests.helpers import (
    API,
    invite_user,
    make_project,
    setup_admin,
    sha256,
    tus_create,
    tus_patch,
    tus_upload,
    wait_source,
)

pytestmark = pytest.mark.integration


@pytest.fixture
async def env(live, media_worker):  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(base_url=live.url, timeout=60) as c:
        admin = await setup_admin(c)
        pid = await make_project(c, admin)
        yield c, admin, pid, live


async def _asset(live, asset_id) -> MediaAsset:  # type: ignore[no-untyped-def]
    async with live.app.state.sessionmaker() as s:
        a = await s.get(MediaAsset, asset_id)
        assert a
        return a


async def _stored_path(live, source: dict, field: str) -> Path:  # type: ignore[no-untyped-def]
    async with live.app.state.sessionmaker() as s:
        from app.db.models import VideoSource

        src = await s.get(VideoSource, __import__("uuid").UUID(source["id"]))
        a = await s.get(MediaAsset, getattr(src, field))
        return Path(get_settings().storage_local_root) / a.storage_key  # type: ignore[union-attr]


async def test_upload_produces_valid_proxy_waveform_thumbnail(env, small_mp4: Path) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await tus_upload(c, h, pid, small_mp4, chunk=64 * 1024)
    src = await wait_source(c, h, sid)
    assert src["status"] == "ready", src

    # Facts extracted from the file, not guessed.
    assert (src["width"], src["height"], src["video_codec"], src["audio_codec"]) == (320, 240, "h264", "aac")
    assert 2.5 < src["duration_s"] < 3.5 and src["has_audio"] is True
    assert src["media_hash"] == sha256(small_mp4) and src["size_bytes"] == small_mp4.stat().st_size

    job = src["job"]
    assert job["status"] == "succeeded"
    assert [s["stage"] for s in job["stages"]] == ["validate", "proxy", "waveform", "thumbnail"]
    assert {s["status"] for s in job["stages"]} == {"succeeded"} and all(s["progress"] == 1.0 for s in job["stages"])

    proxy = probe(await _stored_path(live, src, "proxy_asset_id"))
    assert (proxy.video_codec, proxy.audio_codec, proxy.height) == ("h264", "aac", 240)  # never upscaled
    assert abs(proxy.duration_s - src["duration_s"]) < 0.3

    wf = json.loads((await _stored_path(live, src, "waveform_asset_id")).read_text())
    assert wf["has_audio"] and 1500 < len(wf["peaks"]) <= 2000 and max(hi for _, hi in wf["peaks"]) > 8
    assert (await _stored_path(live, src, "thumbnail_asset_id")).read_bytes()[:3] == b"\xff\xd8\xff"  # JPEG

    async with live.app.state.sessionmaker() as s:  # attestation was recorded
        perm = (await s.execute(select(SourcePermission))).scalar_one()
        assert "permission" in perm.statement_text and str(perm.source_id) == sid
        assert (await s.execute(select(AssetLease))).first() is None  # leases are released after the job


async def test_hd_source_is_downscaled_to_720_with_even_width_and_silent_waveform(env, media_dir: Path) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    src = await wait_source(c, h, await tus_upload(c, h, pid, media_dir / "hd_silent.mp4"))
    assert src["status"] == "ready" and src["has_audio"] is False
    proxy = probe(await _stored_path(live, src, "proxy_asset_id"))
    assert proxy.height == 720 and proxy.width == 1280 and proxy.width % 2 == 0 and proxy.audio_codec is None
    wf = json.loads((await _stored_path(live, src, "waveform_asset_id")).read_text())
    assert wf["has_audio"] is False and wf["peaks"] == []


async def test_proxy_is_streamable_with_range_requests_and_signed_urls(env, small_mp4: Path) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    src = await wait_source(c, h, await tus_upload(c, h, pid, small_mp4))
    url = src["proxy_url"]
    full = await c.get(url)
    assert full.status_code == 200 and full.headers["content-type"] == "video/mp4"
    assert full.headers["accept-ranges"] == "bytes" and int(full.headers["content-length"]) == len(full.content)
    assert full.content[4:8] == b"ftyp"

    part = await c.get(url, headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and part.content == full.content[:100]
    assert part.headers["content-range"] == f"bytes 0-99/{len(full.content)}"
    tail = await c.get(url, headers={"Range": "bytes=-50"})
    assert tail.status_code == 206 and tail.content == full.content[-50:]
    assert (await c.get(url, headers={"Range": f"bytes={len(full.content) + 5}-"})).status_code == 416
    head = await c.head(url)
    assert head.status_code == 200 and head.headers["content-length"] == str(len(full.content)) and head.content == b""

    path, token = url.split("?t=")
    assert (await c.get(path)).status_code == 422  # token is required
    assert (await c.get(path, params={"t": token + "x"})).status_code == 401
    other = src["thumbnail_url"].split("?t=")[0]
    assert (await c.get(other, params={"t": token})).status_code == 401  # token is bound to one asset

    async with live.app.state.sessionmaker() as s:  # originals are never exposed to browsers
        orig = (await s.execute(select(MediaAsset).where(MediaAsset.kind == "original"))).scalar_one()
    from app.security.media_tokens import sign_asset

    tok, _ = sign_asset(orig.id, get_settings())
    assert (await c.get(f"{API}/media/{orig.id}", params={"t": tok})).status_code == 403


async def test_garbage_and_hostile_files_fail_visibly_without_side_effects(
    env, media_dir: Path, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    src = await wait_source(c, h, await tus_upload(c, h, pid, media_dir / "not_video.mp4"))
    assert src["status"] == "failed" and src["error_code"] == "not_media" and src["error_message"], src
    assert src["job"]["status"] == "failed" and src["job"]["stages"][0]["status"] == "failed"
    assert src["job"]["stages"][1]["status"] == "pending"  # later stages never ran
    assert src["proxy_url"] is None
    evil = tmp_path / "evil.mp4"
    evil.write_text("#EXTM3U\n#EXTINF:5,\nhttp://127.0.0.1:1/x\n#EXT-X-ENDLIST\n")
    src = await wait_source(c, h, await tus_upload(c, h, pid, evil))
    assert src["status"] == "failed" and src["error_code"] in {"not_media", "unsupported_container"}


async def test_upload_rules_are_enforced(env, small_mp4: Path) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    r = await tus_create(c, h, pid, "ok.mp4", 100, attested=False)
    assert r.status_code == 400 and r.json()["code"] == "attestation_required"
    assert (await tus_create(c, h, pid, "run.exe", 100)).status_code == 415
    assert (await tus_create(c, h, pid, "ok.mp4", 0)).status_code == 400
    assert (await tus_create(c, h, pid, "ok.mp4", 10**15)).status_code == 413
    ok = await tus_create(c, h, pid, "../../etc/passwd.mp4", 10)
    assert ok.status_code == 201  # traversal in the name is neutralized, never used as a path
    async with live.app.state.sessionmaker() as s:
        from app.db.models import Upload

        up = (await s.execute(select(Upload))).scalar_one()
        assert up.filename == "passwd.mp4" and "/" not in up.staging_name

    no_version = await c.post(f"{API}/uploads", headers={**h, "Upload-Length": "10"})
    assert no_version.status_code == 412
    opts = await c.options(f"{API}/uploads")
    assert (
        opts.status_code == 204
        and "creation" in opts.headers["tus-extension"]
        and opts.headers["tus-version"] == "1.0.0"
    )
    assert (await c.post(f"{API}/uploads", headers={"Tus-Resumable": "1.0.0", "Upload-Length": "1"})).status_code == 401


async def test_upload_authorization(env) -> None:  # type: ignore[no-untyped-def]
    c, admin, pid, live = env
    ed = await invite_user(c, admin, "ed@example.com", "editor")
    viewer = await invite_user(c, admin, "v@example.com", "viewer")
    assert (await tus_create(c, ed, pid, "a.mp4", 10)).status_code == 404  # not a member
    assert (await tus_create(c, viewer, pid, "a.mp4", 10)).status_code == 404
    own = await make_project(c, ed, "Ed's")
    loc = (await tus_create(c, ed, own, "a.mp4", 10)).headers["Location"]
    assert (await tus_patch(c, admin, loc, 0, b"x" * 10)).status_code == 404  # uploads belong to their creator


async def test_tus_protocol_offsets_errors_and_termination(env) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    r = await tus_create(c, h, pid, "a.mp4", 1000)
    loc = r.headers["Location"]
    assert r.headers["Upload-Offset"] == "0" and "Upload-Expires" in r.headers
    head = await c.head(loc, headers={**h, "Tus-Resumable": "1.0.0"})
    assert head.headers["Upload-Offset"] == "0" and head.headers["Upload-Length"] == "1000" and head.headers["cache-control"] == "no-store"  # fmt: skip

    assert (await tus_patch(c, h, loc, 0, b"a" * 400)).headers["Upload-Offset"] == "400"
    wrong = await tus_patch(c, h, loc, 100, b"b" * 10)
    assert wrong.status_code == 409 and wrong.json()["code"] == "offset_mismatch"
    over = await tus_patch(c, h, loc, 400, b"c" * 700)
    assert over.status_code == 413
    ct = await c.patch(loc, headers={**h, "Tus-Resumable": "1.0.0", "Upload-Offset": "400", "Content-Type": "text/plain"}, content=b"x")  # fmt: skip
    assert ct.status_code == 415
    assert (await c.head(loc, headers={**h, "Tus-Resumable": "1.0.0"})).headers["Upload-Offset"] == "400"  # unchanged

    staged = list(Path(get_settings().upload_staging_dir).iterdir())
    assert len(staged) == 1
    assert (await c.delete(loc, headers={**h, "Tus-Resumable": "1.0.0"})).status_code == 204
    assert list(Path(get_settings().upload_staging_dir).iterdir()) == []
    assert (await c.head(loc, headers={**h, "Tus-Resumable": "1.0.0"})).status_code == 404


async def test_upload_resumes_after_a_dropped_connection(env, small_mp4: Path) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    data = small_mp4.read_bytes()
    loc = (await tus_create(c, h, pid, "resume.mp4", len(data))).headers["Location"]

    async def dies_midway():  # type: ignore[no-untyped-def]
        yield data[:5000]
        await asyncio.sleep(0.2)
        raise ConnectionResetError("simulated network drop")

    with pytest.raises(Exception):  # noqa: B017, PT011
        await c.patch(loc, headers={**h, "Tus-Resumable": "1.0.0", "Upload-Offset": "0",
                                    "Content-Type": "application/offset+octet-stream"}, content=dies_midway())  # fmt: skip
    offset = 0
    for _ in range(40):  # the server notices the dropped connection and commits what it received
        head = await c.head(loc, headers={**h, "Tus-Resumable": "1.0.0"})
        offset = int(head.headers["Upload-Offset"])
        if offset:
            break
        await asyncio.sleep(0.1)
    assert 0 < offset <= 5000
    done = await tus_patch(c, h, loc, offset, data[offset:])
    assert done.status_code == 204 and done.headers["X-Source-Id"]
    src = await wait_source(c, h, done.headers["X-Source-Id"])
    assert src["status"] == "ready" and src["media_hash"] == sha256(small_mp4)  # byte-identical after resume


async def test_concurrent_patch_on_one_upload_is_locked(env, small_mp4: Path) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    data = small_mp4.read_bytes()
    loc = (await tus_create(c, h, pid, "lock.mp4", len(data))).headers["Location"]
    gate = asyncio.Event()

    async def slow():  # type: ignore[no-untyped-def]
        yield data[:100]
        await gate.wait()
        yield data[100:200]

    first = asyncio.create_task(c.patch(loc, headers={**h, "Tus-Resumable": "1.0.0", "Upload-Offset": "0",
                                                      "Content-Type": "application/offset+octet-stream"}, content=slow()))  # fmt: skip
    await asyncio.sleep(0.5)
    second = await tus_patch(c, h, loc, 0, data[:50])
    assert second.status_code == 423 and second.json()["code"] == "upload_locked"
    gate.set()
    assert (await first).status_code == 204


async def test_reprocess_reuses_checkpointed_stages(env, small_mp4: Path) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await tus_upload(c, h, pid, small_mp4)
    first = await wait_source(c, h, sid)
    async with live.app.state.sessionmaker() as s:
        before = {a.id for a in (await s.execute(select(MediaAsset))).scalars()}
    r = await c.post(f"{API}/sources/{sid}/reprocess", headers=h)
    assert r.status_code == 202
    for _ in range(100):
        job = (await c.get(f"{API}/jobs/{r.json()['id']}", headers=h)).json()
        if job["status"] in ("succeeded", "failed"):
            break
        await asyncio.sleep(0.2)
    assert job["status"] == "succeeded"
    assert {s["status"] for s in job["stages"]} == {"skipped"}  # nothing was recomputed
    async with live.app.state.sessionmaker() as s:
        assert {a.id for a in (await s.execute(select(MediaAsset))).scalars()} == before  # no new files
        reused = (await s.execute(select(JobStage).where(JobStage.reused_from.is_not(None)))).scalars().all()
        assert len(reused) == 4
    assert (await c.get(f"{API}/sources/{sid}", headers=h)).json()["proxy_url"] and first["status"] == "ready"
    # A second concurrent reprocess is refused while one runs.
    again = await c.post(f"{API}/sources/{sid}/reprocess", headers=h)
    assert again.status_code in (202, 409)


async def test_failed_stage_retry_skips_finished_stages(env, small_mp4: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    monkeypatch.setenv("PROXY_PRESET", "definitely-not-a-preset")  # makes ffmpeg fail for real
    get_settings.cache_clear()
    try:
        sid = await tus_upload(c, h, pid, small_mp4)
        src = await wait_source(c, h, sid)
        assert src["status"] == "failed" and src["error_code"] == "ffmpeg_failed"
        stages = {s["stage"]: s for s in src["job"]["stages"]}
        assert stages["validate"]["status"] == "succeeded" and stages["proxy"]["status"] == "failed"
        assert stages["waveform"]["status"] == "pending"
        job_id = src["job"]["id"]

        monkeypatch.setenv("PROXY_PRESET", "veryfast")  # the "fix"
        get_settings.cache_clear()
        assert (await c.post(f"{API}/jobs/{job_id}/retry", headers=h)).status_code == 202
        src = await wait_source(c, h, sid)
        assert src["status"] == "ready" and src["error_code"] is None
        stages = {s["stage"]: s for s in src["job"]["stages"]}
        assert stages["validate"]["attempts"] == 1  # not re-run
        assert stages["proxy"]["attempts"] == 2 and stages["proxy"]["status"] == "succeeded"
        assert (await c.post(f"{API}/jobs/{job_id}/retry", headers=h)).status_code == 409  # only failed jobs retry
    finally:
        get_settings.cache_clear()


async def test_cancel_stops_a_running_encode(env, media_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    monkeypatch.setenv("PROXY_PRESET", "veryslow")
    get_settings.cache_clear()
    try:
        sid = await tus_upload(c, h, pid, media_dir / "long_hd.mp4", chunk=4 << 20)
        job_id = None
        for _ in range(200):  # wait until the proxy stage is really encoding
            src = (await c.get(f"{API}/sources/{sid}", headers=h)).json()
            st = {s["stage"]: s for s in (src["job"] or {"stages": []})["stages"]}
            if st.get("proxy", {}).get("status") == "running":
                job_id = src["job"]["id"]
                break
            await asyncio.sleep(0.1)
        assert job_id, "proxy stage never started"
        assert (await c.post(f"{API}/jobs/{job_id}/cancel", headers=h)).status_code == 202
        src = await wait_source(c, h, sid, timeout=30)
        assert src["status"] == "failed" and src["error_code"] == "cancelled"
        assert src["job"]["status"] == "cancelled" and src["proxy_url"] is None
        async with live.app.state.sessionmaker() as s:
            assert (await s.execute(select(MediaAsset).where(MediaAsset.kind == "proxy"))).first() is None
        assert (await c.post(f"{API}/jobs/{job_id}/cancel", headers=h)).status_code == 409
    finally:
        get_settings.cache_clear()


async def test_stuck_job_with_dead_worker_is_requeued_and_finishes(env, small_mp4: Path) -> None:  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from app.db.models import ProcessingJob
    from app.services.recovery import requeue_stuck_jobs

    c, h, pid, live = env
    sid = await tus_upload(c, h, pid, small_mp4)
    src = await wait_source(c, h, sid)
    job_id = src["job"]["id"]
    # Simulate the state a crashed worker leaves behind: running, with a long-dead heartbeat.
    async with live.app.state.sessionmaker() as s:
        await s.execute(update(JobStage).where(JobStage.stage == "waveform").values(status="running"))
        await s.execute(update(ProcessingJob).values(status="running", heartbeat_at=datetime.now(UTC) - timedelta(minutes=10)))  # fmt: skip
        await s.commit()
        assert await requeue_stuck_jobs(s) == 1
    for _ in range(100):
        job = (await c.get(f"{API}/jobs/{job_id}", headers=h)).json()
        if job["status"] == "succeeded":
            break
        await asyncio.sleep(0.2)
    assert job["status"] == "succeeded"
    assert {s["stage"]: s["status"] for s in job["stages"]}["waveform"] in ("succeeded", "skipped")


async def test_delete_source_removes_files_and_blocks_while_running(env, small_mp4: Path) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await tus_upload(c, h, pid, small_mp4)
    await wait_source(c, h, sid)
    root = Path(get_settings().storage_local_root)
    assert len([p for p in root.rglob("*") if p.is_file()]) == 4  # original, proxy, waveform, thumbnail
    assert (await c.delete(f"{API}/sources/{sid}", headers=h)).status_code == 204
    assert [p for p in root.rglob("*") if p.is_file()] == []
    assert (await c.get(f"{API}/sources/{sid}", headers=h)).status_code == 404
    async with live.app.state.sessionmaker() as s:
        assert (await s.execute(select(MediaAsset))).first() is None


async def test_delete_project_removes_everything_it_owned(env, small_mp4: Path) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    await wait_source(c, h, await tus_upload(c, h, pid, small_mp4))
    r = await c.request("DELETE", f"{API}/projects/{pid}", headers=h, json={"confirm_name": "Podcast"})
    assert r.status_code == 204
    assert [p for p in Path(get_settings().storage_local_root).rglob("*") if p.is_file()] == []


async def test_websocket_streams_progress_only_to_people_with_access(env, small_mp4: Path) -> None:  # type: ignore[no-untyped-def]
    import websockets

    c, admin, _, live = env
    ed = await invite_user(c, admin, "ed@example.com", "editor")
    outsider = await invite_user(c, admin, "out@example.com", "editor")
    pid = await make_project(c, ed, "Ed's")
    ws_url = live.url.replace("http", "ws") + "/ws/jobs"

    async def ticket(hh: dict[str, str]) -> str:
        return str((await c.post(f"{API}/ws/ticket", headers=hh)).json()["ticket"])

    async with (
        websockets.connect(f"{ws_url}?ticket={await ticket(ed)}") as mine,
        websockets.connect(f"{ws_url}?ticket={await ticket(outsider)}") as theirs,
    ):
        await asyncio.sleep(0.3)
        sid = await tus_upload(c, ed, pid, small_mp4)
        got: list[dict] = []  # type: ignore[type-arg]
        async with asyncio.timeout(60):
            while True:
                msg = json.loads(await mine.recv())
                got.append(msg)
                if msg.get("job_status") == "succeeded":
                    break
        assert {m["source_id"] for m in got} == {sid} and {m["project_id"] for m in got} == {pid}
        assert {"validate", "proxy", "waveform", "thumbnail"} <= {m["stage"] for m in got}
        assert got[-1]["source_status"] == "ready"
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(1.0):
                await theirs.recv()  # the outsider receives nothing about someone else's project

    # Tickets are single-use and tied to a user; garbage is refused.
    t = await ticket(ed)
    async with websockets.connect(f"{ws_url}?ticket={t}") as ok:
        await ok.send("ping")
        assert json.loads(await ok.recv()) == {"type": "pong"}
    for bad in (t, "nonsense", ""):
        with pytest.raises((websockets.exceptions.ConnectionClosed, websockets.exceptions.InvalidStatus)):
            async with websockets.connect(f"{ws_url}?ticket={bad}") as w:
                await w.recv()


def test_ffmpeg_is_never_given_a_shell() -> None:
    """Static guard: media code must not use shell=True or os.system."""
    import re

    bad = []
    for p in Path("app").rglob("*.py"):
        text = p.read_text()
        if re.search(r"shell\s*=\s*True|os\.system\(|os\.popen\(", text):
            bad.append(str(p))
    assert bad == []
    assert subprocess.run(["ffmpeg", "-version"], capture_output=True).returncode == 0  # noqa: S603, S607
