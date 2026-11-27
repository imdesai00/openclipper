"""Clips and renders through the real stack: real Celery worker, real ffmpeg + libass, real Postgres.

Nothing here trusts the request: every claim is checked on the file that came out (ffprobe, a loudness measurement,
and actual pixels of actual frames)."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.db.models import ClipRender, MediaAsset, VideoSource
from app.ports.asr import SegmentData, TranscriptData, WordData
from app.services import caption_engine as ce
from app.services import render_plan as rp
from app.services import transcripts
from tests.helpers import API, invite_user, make_project, setup_admin, tus_upload, wait_source

pytestmark = pytest.mark.integration

SENTENCES = [
    "Welcome back to the show everybody today.",
    "The first surprising thing is that sleep matters.",
    "Researchers followed ten thousand people for years.",
    "Those who slept badly had far worse health.",
    "Now let me tell you a story about grandmother.",
    "She never owned an alarm clock and woke early.",
]
WORD_S = 0.4
DURATION_S = 20


def spoken() -> list[tuple[float, float, str, list[tuple[float, float, str]]]]:
    out, t = [], 1.0
    for s in SENTENCES:
        words, start = [], t
        for w in s.split():
            words.append((t, t + WORD_S, w))
            t += WORD_S
        out.append((start, t, s, words))
        t += 0.4
    return out


def run(cmd: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(cmd, check=True, capture_output=True)


# ------------------------------------------------------------------------------------------------ media fixtures
@pytest.fixture(scope="session")
def red_blue(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """1280x720: the LEFT half is solid red, the RIGHT half solid blue, with a quiet tone (-30 dB) for 20 s."""
    out = tmp_path_factory.mktemp("clips") / "redblue.mp4"
    f = ["-f", "lavfi", "-i"]
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *f, f"color=c=red:s=640x720:r=25:d={DURATION_S}",
         *f, f"color=c=blue:s=640x720:r=25:d={DURATION_S}", *f, f"sine=frequency=440:sample_rate=48000:duration={DURATION_S}",
         "-filter_complex", "[0][1]hstack=inputs=2[v];[2]volume=-30dB[a]", "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)])  # fmt: skip
    return out


@pytest.fixture(scope="session")
def black_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("clipsb") / "black.mp4"
    f = ["-f", "lavfi", "-i"]
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *f, f"color=c=black:s=640x360:r=25:d={DURATION_S}",
         *f, f"sine=frequency=300:duration={DURATION_S}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-shortest", str(out)])  # fmt: skip
    return out


@pytest.fixture(scope="session")
def podcast(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("clipsa") / "podcast.m4a"
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
         f"sine=frequency=330:sample_rate=48000:duration={DURATION_S}", "-af", "volume=-20dB", "-c:a", "aac", str(out)])  # fmt: skip
    return out


# ------------------------------------------------------------------------------------------------ helpers
@pytest.fixture
async def env(live, media_worker):  # type: ignore[no-untyped-def]
    async with httpx.AsyncClient(base_url=live.url, timeout=300) as c:
        admin = await setup_admin(c)
        pid = await make_project(c, admin)
        yield c, admin, pid, live


async def ready_source(c, h, pid, live, path: Path, *, transcript: bool = True) -> str:  # type: ignore[no-untyped-def]
    sid = await tus_upload(c, h, pid, path)
    src = await wait_source(c, h, sid)
    assert src["status"] == "ready", src
    if transcript:
        from app.services.jobs import create_job

        data = TranscriptData(
            "en", 0.99, float(DURATION_S), "test", "synthetic",
            [SegmentData(s, e, t, [WordData(a, b, w) for a, b, w in ws]) for s, e, t, ws in spoken()],
        )  # fmt: skip
        async with live.app.state.sessionmaker() as s:
            row = await s.get(VideoSource, uuid.UUID(sid))
            assert row
            job = await create_job(s, row, None, kind="transcribe_source", params={})
            await transcripts.persist_asr(s, source=row, job=job, data=data)
            await s.commit()
    return sid


async def new_clip(c, h, sid: str, start: int = 2000, end: int = 8000, **kw: Any) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    r = await c.post(f"{API}/sources/{sid}/clips", headers=h, json={"start_ms": start, "end_ms": end, **kw})
    assert r.status_code == 201, r.text
    return dict(r.json())


async def save(c, h, clip: dict[str, Any], **fields: Any) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    r = await c.post(
        f"{API}/clips/{clip['id']}/versions", headers=h, json={"base_version": clip["current_version"], **fields}
    )
    assert r.status_code == 201, r.text
    return dict(r.json())


async def render(c, h, clip_id: str, kind: str = "final", timeout: float = 240, **kw: Any) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    r = await c.post(f"{API}/clips/{clip_id}/render", headers=h, json={"kind": kind, **kw})
    assert r.status_code in (200, 202), r.text
    out = r.json()
    end = time.monotonic() + timeout
    while out["render"]["state"] not in ("done", "failed", "cancelled"):
        assert time.monotonic() < end, f"render did not finish: {out}"
        await asyncio.sleep(0.5)
        clip = (await c.get(f"{API}/clips/{clip_id}", headers=h)).json()
        out["render"] = next(x for x in clip["renders"] if x["id"] == out["render"]["id"])
    return dict(out)


async def fetch(c, url: str, dest: Path) -> Path:  # type: ignore[no-untyped-def]
    r = await c.get(url)
    assert r.status_code == 200, r.text[:200]
    dest.write_bytes(r.content)
    return dest


def probe(path: Path) -> dict[str, Any]:
    out = run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]).stdout
    return dict(json.loads(out))


def frame(path: Path, t: float, w: int, h: int) -> np.ndarray:
    raw = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]).stdout  # fmt: skip
    return np.frombuffer(raw, dtype=np.uint8).reshape(h, w, 3)


def loudness(path: Path) -> float:
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                         capture_output=True, text=True, check=True).stderr  # fmt: skip
    v = rp.parse_integrated_lufs(err)
    assert v is not None
    return v


def caption_pixels(img: np.ndarray) -> int:
    """Bright or yellow pixels in the lower 40% of the frame: where white and highlighted caption text lives."""
    low = img[int(img.shape[0] * 0.6) :].astype(int)
    white = (low[..., 0] > 200) & (low[..., 1] > 200) & (low[..., 2] > 200)
    yellow = (low[..., 0] > 200) & (low[..., 1] > 170) & (low[..., 2] < 90)
    return int((white | yellow).sum())


# ------------------------------------------------------------------------------------------------ tests
async def test_a_final_render_is_a_real_9x16_mp4_with_captions_normalised_sound_and_a_passing_report(
    env, red_blue, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await ready_source(c, h, pid, live, red_blue)
    clip = await new_clip(c, h, sid, 2000, 8000)
    assert (
        clip["version"]["aspect"] == "9:16"
        and clip["template_name"] == "Bold highlight"
        and clip["version"]["transcript_id"]
    )

    done = await render(c, h, clip["id"], "final")
    r = done["render"]
    assert r["state"] == "done" and done["reused"] is False, r
    assert (r["width"], r["height"]) == (1080, 1920) and 5.8 < r["duration_ms"] / 1000 < 6.2
    assert r["qc_status"] == "pass", r["qc_report"]
    checks = {x["check"]: x for x in r["qc_report"]["checks"]}
    assert {
        "video_stream",
        "audio_stream",
        "dimensions",
        "duration",
        "av_sync",
        "loudness",
        "caption_bounds",
        "caption_sync",
        "black_frames",
        "silence",
    } <= set(checks)

    mp4 = await fetch(c, r["download_url"], tmp_path / "final.mp4")  # the file a person would download
    info = probe(mp4)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert (v["codec_name"], v["width"], v["height"], v["pix_fmt"]) == ("h264", 1080, 1920, "yuv420p") and a[
        "codec_name"
    ] == "aac"
    assert (
        abs(float(info["format"]["duration"]) - 6.0) < 0.2 and abs(float(v["duration"]) - float(a["duration"])) < 0.15
    )
    assert abs(loudness(mp4) - (-14.0)) < 1.5  # the source was quiet (about -30): this is the loudness stage's work
    assert (
        r["loudness_lufs"] is not None and abs(r["loudness_lufs"] - loudness(mp4)) < 0.5
    )  # what the report says is what is there

    # captions are burned in: the second sentence is spoken at 5 s of the source = 3 s into the clip
    with_caps = caption_pixels(frame(mp4, 3.0, 1080, 1920))
    assert with_caps > 400, with_caps
    plain = await save(c, h, clip, caption_overrides={"enabled": False})
    done2 = await render(c, h, clip["id"], "final")
    mp4b = await fetch(c, done2["render"]["download_url"], tmp_path / "nocaps.mp4")
    assert caption_pixels(frame(mp4b, 3.0, 1080, 1920)) < with_caps // 20  # only the captions differ
    assert plain["current_version"] == 2

    # the download link names the file and serves it as an attachment; thumbnails and previews are real images / video
    dl = await c.get(r["download_url"], headers={"Range": "bytes=0-99"})
    assert (
        dl.status_code == 206
        and "attachment" in dl.headers["content-disposition"]
        and ".mp4" in dl.headers["content-disposition"]
    )
    thumb = await c.get(r["thumbnail_url"])
    assert thumb.status_code == 200 and thumb.content[:3] == b"\xff\xd8\xff"
    stages = [s["stage"] for s in (await c.get(f"{API}/sources/{sid}", headers=h)).json()["job"]["stages"]]
    assert "validate" in stages  # the render jobs never replaced the source's own processing record

    async with live.app.state.sessionmaker() as s:  # the originals are never exposed through the media endpoint
        orig = (await s.execute(select(MediaAsset).where(MediaAsset.kind == "original"))).scalars().first()
    from app.security.media_tokens import sign_asset

    tok, _ = sign_asset(orig.id, get_settings())  # type: ignore[union-attr]
    assert (await c.get(f"{API}/media/{orig.id}", params={"t": tok})).status_code == 403  # type: ignore[union-attr]


async def test_the_same_render_is_reused_and_a_changed_one_is_not(env, red_blue) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await ready_source(c, h, pid, live, red_blue)
    clip = await new_clip(c, h, sid, 2000, 5000)
    first = await render(c, h, clip["id"], "preview")
    assert first["render"]["state"] == "done" and (first["render"]["width"], first["render"]["height"]) == (360, 640)
    again = await c.post(f"{API}/clips/{clip['id']}/render", headers=h, json={"kind": "preview"})
    assert (
        again.status_code == 200
        and again.json()["reused"] is True
        and again.json()["render"]["id"] == first["render"]["id"]
    )
    async with live.app.state.sessionmaker() as s:
        assert (await s.execute(select(func.count()).select_from(ClipRender))).scalar_one() == 1
    forced = await c.post(f"{API}/clips/{clip['id']}/render", headers=h, json={"kind": "preview", "force": True})
    assert forced.status_code == 202 and forced.json()["render"]["id"] != first["render"]["id"]
    await asyncio.sleep(0)
    await save(c, h, clip, audio={"gain_db": 3})  # a different sound is a different render
    other = await c.post(f"{API}/clips/{clip['id']}/render", headers=h, json={"kind": "preview"})
    assert other.status_code in (202, 409)  # 409 only if the forced render above is still running for the older version


async def test_pan_keyframes_really_move_the_picture(env, red_blue, tmp_path) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await ready_source(c, h, pid, live, red_blue)
    clip = await new_clip(c, h, sid, 2000, 8000)
    await save(c, h, clip, caption_overrides={"enabled": False}, crop={"mode": "fill", "zoom": 1.0, "keyframes": [
        {"t_ms": 0, "cx": 0.05, "cy": 0.5}, {"t_ms": 6000, "cx": 0.95, "cy": 0.5}]})  # fmt: skip
    r = (await render(c, h, clip["id"], "preview"))["render"]
    assert r["state"] == "done", r
    mp4 = await fetch(c, r["play_url"], tmp_path / "pan.mp4")
    w, hh = 360, 640
    first, mid, last = (
        frame(mp4, 0.2, w, hh).astype(int),
        frame(mp4, 3.0, w, hh).astype(int),
        frame(mp4, 5.7, w, hh).astype(int),
    )
    red = lambda f: float((f[..., 0] > 150).mean())  # noqa: E731
    blue = lambda f: float((f[..., 2] > 150).mean())  # noqa: E731
    assert red(first) > 0.9 and blue(first) < 0.1  # the window starts over the red half
    assert blue(last) > 0.9 and red(last) < 0.1  # and ends over the blue half
    assert 0.1 < red(mid) < 0.9 and 0.1 < blue(mid) < 0.9  # in between it is moving across the boundary


async def test_zoom_shows_a_smaller_part_of_the_picture(env, red_blue, tmp_path) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await ready_source(c, h, pid, live, red_blue)
    clip = await new_clip(c, h, sid, 2000, 4000)
    # the full-height 9:16 window is 404 px wide and centred on the red/blue edge; at 4x it is 100 px: still on the edge
    await save(
        c,
        h,
        clip,
        caption_overrides={"enabled": False},
        crop={"mode": "fill", "zoom": 1.0, "keyframes": [{"t_ms": 0, "cx": 0.5, "cy": 0.5}]},
    )
    wide = (await render(c, h, clip["id"], "preview"))["render"]
    c2 = (await c.get(f"{API}/clips/{clip['id']}", headers=h)).json()
    await save(c, h, c2, crop={"mode": "fill", "zoom": 4.0, "keyframes": [{"t_ms": 0, "cx": 0.3, "cy": 0.5}]})
    tight = (await render(c, h, clip["id"], "preview"))["render"]
    f_wide = frame(await fetch(c, wide["play_url"], tmp_path / "wide.mp4"), 0.5, 360, 640).astype(int)
    f_tight = frame(await fetch(c, tight["play_url"], tmp_path / "tight.mp4"), 0.5, 360, 640).astype(int)
    assert 0.2 < float((f_wide[..., 0] > 150).mean()) < 0.8  # the wide window straddles both colours
    assert float((f_tight[..., 0] > 150).mean()) > 0.97  # the tight window sits inside the red half


async def test_fit_mode_keeps_the_whole_picture_on_a_blurred_or_solid_background(env, red_blue, tmp_path) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await ready_source(c, h, pid, live, red_blue)
    clip = await new_clip(c, h, sid, 2000, 4000)
    v2 = await save(
        c,
        h,
        clip,
        caption_overrides={"enabled": False},
        crop={"mode": "fit", "zoom": 1.0, "keyframes": []},
        background={"type": "solid", "color": "#00FF00"},
    )
    solid = (await render(c, h, clip["id"], "preview"))["render"]
    img = frame(await fetch(c, solid["play_url"], tmp_path / "solid.mp4"), 0.5, 360, 640).astype(int)
    assert img[5, 180, 1] > 200 and img[5, 180, 0] < 60 and img[634, 180, 1] > 200  # green bars above and below
    centre = img[320, :, :]  # the full 16:9 picture sits in the middle: red on the left, blue on the right
    assert centre[20, 0] > 150 and centre[340, 2] > 150
    v3 = await save(c, h, v2, background={"type": "blur", "color": "#000000"})
    blur = (await render(c, h, clip["id"], "preview"))["render"]
    img2 = frame(await fetch(c, blur["play_url"], tmp_path / "blur.mp4"), 0.5, 360, 640).astype(int)
    assert not (img2[5, 180, 1] > 200 and img2[5, 180, 0] < 60)  # no green: the bars are a blurred copy of the picture
    assert v3["current_version"] == 3


async def test_an_audio_only_source_is_rendered_as_a_waveform_with_captions(env, podcast, tmp_path) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await ready_source(c, h, pid, live, podcast)
    clip = await new_clip(c, h, sid, 2000, 8000)
    r = (await render(c, h, clip["id"], "final"))["render"]
    assert r["state"] == "done" and r["qc_status"] == "pass", r["qc_report"]  # no picture to be "black"
    mp4 = await fetch(c, r["download_url"], tmp_path / "pod.mp4")
    info = probe(mp4)
    assert next(s for s in info["streams"] if s["codec_type"] == "video")["height"] == 1920
    img = frame(mp4, 3.0, 1080, 1920).astype(int)
    bg = img[20, 20]
    assert abs(bg[0] - 0x1B) < 12 and abs(bg[1] - 0x1B) < 12 and abs(bg[2] - 0x2F) < 12  # the solid background colour
    band = img[900:1020]  # the waveform band is around the middle
    assert int(((band[..., 0] > 200) & (band[..., 1] > 200) & (band[..., 2] > 200)).sum()) > 500  # white waveform
    assert caption_pixels(img) > 300  # and the captions are on top
    assert abs(loudness(mp4) - (-14.0)) < 1.5


async def test_quality_checks_catch_a_clip_that_is_black(env, black_clip) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await ready_source(c, h, pid, live, black_clip)
    clip = await new_clip(c, h, sid, 2000, 8000)
    r = (await render(c, h, clip["id"], "final"))["render"]
    assert r["state"] == "done" and r["qc_status"] == "fail", r["qc_report"]
    bad = [x for x in r["qc_report"]["checks"] if x["severity"] == "fail"]
    assert [x["check"] for x in bad] == ["black_frames"] and "black" in bad[0]["message"]
    assert r["download_url"]  # the file exists and can be fetched; the report says what is wrong with it


async def test_the_clip_a_muted_render_is_silent_on_purpose(env, red_blue, tmp_path) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await ready_source(c, h, pid, live, red_blue)
    clip = await new_clip(c, h, sid, 2000, 5000)
    await save(c, h, clip, audio={"gain_db": 0, "mute": True})
    r = (await render(c, h, clip["id"], "final"))["render"]
    assert r["qc_status"] == "pass", r["qc_report"]  # silence is what was asked for, so it is not a failure
    mp4 = await fetch(c, r["download_url"], tmp_path / "mute.mp4")
    assert loudness(mp4) < -60  # truly silent, but the file still has an audio track
    assert any(s["codec_type"] == "audio" for s in probe(mp4)["streams"])


async def test_versions_are_immutable_conflicts_are_refused_and_bad_values_explain_themselves(env, red_blue) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await ready_source(c, h, pid, live, red_blue)
    clip = await new_clip(c, h, sid, 2000, 8000)
    v2 = await save(c, h, clip, aspect="1:1", title="Renamed")
    assert v2["current_version"] == 2 and v2["version"]["aspect"] == "1:1" and v2["title"] == "Renamed"
    versions = (await c.get(f"{API}/clips/{clip['id']}/versions", headers=h)).json()
    assert [(v["version"], v["aspect"]) for v in versions] == [(2, "1:1"), (1, "9:16")]  # version 1 is untouched

    stale = await c.post(f"{API}/clips/{clip['id']}/versions", headers=h, json={"base_version": 1, "aspect": "16:9"})
    assert stale.status_code == 409 and stale.json()["code"] == "version_conflict"

    def bad(**f: Any) -> Any:
        return c.post(f"{API}/clips/{clip['id']}/versions", headers=h, json={"base_version": 2, **f})

    for fields, code in [
        ({"start_ms": 7000, "end_ms": 7500}, "invalid_range"), ({"start_ms": 1000, "end_ms": 99_000_000}, "invalid_range"),
        ({"crop": {"mode": "stretch"}}, "invalid_crop"), ({"crop": {"zoom": 9}}, "invalid_crop"),
        ({"background": {"type": "solid", "color": "red"}}, "invalid_background"), ({"audio": {"gain_db": 99}}, "invalid_audio"),
        ({"caption_overrides": {"font_family": "Comic Sans"}}, "invalid_style"), ({"caption_overrides": {"typo_key": 1}}, "invalid_style"),
        ({"caption_template_id": str(uuid.uuid4())}, "invalid_template"),
    ]:  # fmt: skip
        r = await bad(**fields)
        assert r.status_code == 400 and r.json()["code"] == code, (fields, r.text)
    assert (await bad(aspect="4:3")).status_code == 422
    assert (await c.get(f"{API}/clips/{clip['id']}", headers=h)).json()["current_version"] == 2  # nothing half-saved


async def test_templates_the_clip_overrides_and_authorisation(env, red_blue) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await ready_source(c, h, pid, live, red_blue)
    tpls = (await c.get(f"{API}/templates", headers=h)).json()
    assert {t["name"] for t in tpls} >= set(ce.BUILTIN_TEMPLATES) and all(t["is_builtin"] for t in tpls)
    builtin = tpls[0]
    assert (await c.put(f"{API}/templates/{builtin['id']}", headers=h, json={"name": "x"})).status_code == 409
    assert (await c.delete(f"{API}/templates/{builtin['id']}", headers=h)).status_code == 409
    mine = await c.post(
        f"{API}/templates",
        headers=h,
        json={"name": "Mine", "style": {"font_family": "DejaVu Sans", "color": "#00FF00", "animation": "none"}},
    )
    assert mine.status_code == 201 and mine.json()["style"]["font_family"] == "DejaVu Sans"
    assert (
        await c.post(f"{API}/templates", headers=h, json={"name": "Bad", "style": {"font_family": "Papyrus"}})
    ).status_code == 400
    clip = await new_clip(c, h, sid, 2000, 6000)
    v = await save(
        c, h, clip, caption_template_id=mine.json()["id"], caption_overrides={"color": "#FF0000", "position": "top"}
    )
    assert (
        v["style"]["font_family"] == "DejaVu Sans"
        and v["style"]["color"] == "#FF0000"
        and v["style"]["position"] == "top"
    )
    # the editor draws captions from the same compiler the render uses
    ps = (
        await c.get(f"{API}/clips/{clip['id']}/preview-style", headers=h, params={"width": 360, "height": 640})
    ).json()
    assert (
        ps["css"]["text"]["color"] == "#FF0000"
        and ps["css"]["container"]["top"] == "77px"
        and ps["cues"]
        and ps["fonts"] == list(ce.FONTS)
    )
    assert (await c.delete(f"{API}/templates/{mine.json()['id']}", headers=h)).status_code == 204
    after = (await c.get(f"{API}/clips/{clip['id']}", headers=h)).json()
    assert (
        after["version"]["caption_template_id"] is None and after["style"]["font_family"] == "Liberation Sans"
    )  # fell back, no crash

    other = await invite_user(c, h, "other@example.com")
    assert (await c.get(f"{API}/clips/{clip['id']}", headers=other)).status_code in (403, 404)
    assert (await c.post(f"{API}/clips/{clip['id']}/render", headers=other, json={})).status_code in (403, 404)
    assert (
        await c.post(f"{API}/clips/{clip['id']}/versions", headers=other, json={"base_version": 2})
    ).status_code in (403, 404)
    assert (await c.get(f"{API}/clips/{clip['id']}/render")).status_code in (401, 405)


async def test_caption_exports_follow_the_words_in_the_clip(env, red_blue) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await ready_source(c, h, pid, live, red_blue)
    clip = await new_clip(c, h, sid, 4000, 8000)
    srt = await c.get(f"{API}/clips/{clip['id']}/captions", headers=h, params={"format": "srt"})
    vtt = await c.get(f"{API}/clips/{clip['id']}/captions", headers=h, params={"format": "vtt"})
    assert (
        srt.status_code == 200
        and srt.text.startswith("1\n00:00:0")
        and "-->" in srt.text
        and vtt.text.startswith("WEBVTT")
    )
    text = " ".join(srt.text.split())
    assert "surprising" in text and "Welcome" not in text  # only the words inside 4 s to 8 s
    first = srt.text.splitlines()[1]
    assert first.startswith("00:00:00,")  # times are relative to the clip, not to the source
    assert (await c.get(f"{API}/clips/{clip['id']}/captions", headers=h, params={"format": "doc"})).status_code == 400


async def test_a_highlight_opens_as_one_clip_and_deleting_it_removes_its_files(env, red_blue) -> None:  # type: ignore[no-untyped-def]
    c, h, pid, live = env
    sid = await ready_source(c, h, pid, live, red_blue)
    from app.db.models import HighlightCandidate, HighlightRun

    async with live.app.state.sessionmaker() as s:
        t = await transcripts.current(s, uuid.UUID(sid))
        run = HighlightRun(source_id=uuid.UUID(sid), transcript_id=t.id, params_hash="x", params={}, stats={})  # type: ignore[union-attr]
        s.add(run)
        await s.flush()
        cand = HighlightCandidate(run_id=run.id, rank=1, start_seg=1, start_word=0, end_seg=2, end_word=3, start_ms=3800, end_ms=9000,
                                  category="other", hook="h", title="A good moment", explanation="e", llm_score=80, standalone_score=70,
                                  confidence=60, final_score=75.0, score_parts={}, provider_name="p", model_name="m")  # fmt: skip
        s.add(cand)
        await s.commit()
        cid = str(cand.id)
    r1 = await c.post(f"{API}/highlights/{cid}/clip", headers=h)
    r2 = await c.post(f"{API}/highlights/{cid}/clip", headers=h)
    assert r1.status_code == 201 and r2.status_code == 200 and r1.json()["id"] == r2.json()["id"]  # no duplicates
    assert r1.json()["title"] == "A good moment" and (
        r1.json()["version"]["start_ms"],
        r1.json()["version"]["end_ms"],
    ) == (3800, 9000)
    clip = r1.json()
    done = await render(c, h, clip["id"], "final")
    assert done["render"]["state"] == "done"
    async with live.app.state.sessionmaker() as s:
        keys = [
            a.storage_key
            for a in (
                await s.execute(
                    select(MediaAsset).where(
                        MediaAsset.kind.in_(("clip_render", "thumbnail", "subtitle", "clip_analysis"))
                    )
                )
            ).scalars()
        ]
    root = Path(get_settings().storage_local_root)
    assert keys and all((root / k).exists() for k in keys)
    assert (await c.delete(f"{API}/clips/{clip['id']}", headers=h)).status_code == 204
    assert (await c.get(f"{API}/clips/{clip['id']}", headers=h)).status_code == 404
    survivors = [k for k in keys if (root / k).exists() and "clip" in k]
    async with live.app.state.sessionmaker() as s:
        left = (
            await s.execute(
                select(func.count())
                .select_from(MediaAsset)
                .where(MediaAsset.kind.in_(("clip_render", "subtitle", "clip_analysis")))
            )
        ).scalar_one()
    assert left == 0 and not [k for k in keys if (root / k).exists() and k.split("/")[-1].endswith((".ass", ".mp4"))], (
        survivors
    )


# ------------------------------------------------------------------------------------------------ the caption size factors
CHROMIUM_INK_W_200PX = {
    "Liberation Sans": 551,
    "Liberation Serif": 617,
    "DejaVu Sans": 634,
    "DejaVu Sans Mono": 456,
}  # bold "HHHH"


@pytest.mark.skipif(not shutil.which("fc-match"), reason="SKIPPED: fontconfig is not installed")
@pytest.mark.parametrize("font", ce.FONTS)
def test_the_libass_size_factor_still_matches_this_machines_libass(font: str, tmp_path: Path) -> None:
    """ASS_SIZE_FACTOR was measured once against Chromium. This re-measures libass here, so a different libass or font
    version cannot silently make the render's captions a different size from the editor's preview."""
    match = subprocess.run(["fc-match", font], capture_output=True, text=True).stdout
    if font.split()[0] not in match:
        pytest.skip(f"SKIPPED: font {font} is not installed here (fc-match gave {match.strip()!r})")
    ass = tmp_path / "t.ass"
    ass.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 600\nWrapStyle: 2\n[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
        "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},200,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:02.00,Default,,0,0,0,,HHHH\n"
    )
    raw = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=1080x600:d=1", "-vf",
               f"ass={ass}", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"]).stdout  # fmt: skip
    img = np.frombuffer(raw, dtype=np.uint8).reshape(600, 1080)
    xs = np.where((img > 128).any(axis=0))[0]
    ink = int(xs[-1] - xs[0] + 1)
    measured_factor = CHROMIUM_INK_W_200PX[font] / ink
    assert abs(measured_factor - ce.ASS_SIZE_FACTOR[font]) / ce.ASS_SIZE_FACTOR[font] < 0.03, (font, measured_factor)
