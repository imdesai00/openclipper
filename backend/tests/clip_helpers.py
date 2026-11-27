"""Helpers shared by the clip tests: a synthetic transcript, ffmpeg/ffprobe wrappers, and calls into the API."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from app.db.models import VideoSource
from app.ports.asr import SegmentData, TranscriptData, WordData
from app.services import render_plan as rp
from app.services import transcripts
from tests.helpers import API, tus_upload, wait_source

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
