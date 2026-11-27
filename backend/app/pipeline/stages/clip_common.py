"""Shared by the clip render stages: turn the job's snapshot into a RenderSpec. No stage here reads the database."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import Settings
from app.pipeline.types import StageContext, StageError
from app.services import caption_engine as ce
from app.services import crop_logic as cl
from app.services import render_plan as rp


def render_params(_settings: Settings, job_params: dict[str, Any] | None = None) -> dict[str, Any]:
    """The render hash already covers the version, template, transcript words, output size and engine."""
    jp = job_params or {}
    return {"v": 1, "render": jp.get("render_hash"), "kind": jp.get("kind")}


def style_of(jp: dict[str, Any]) -> ce.CaptionStyle:
    try:
        return ce.validate_style(jp["style"])
    except (KeyError, ValueError) as exc:
        raise StageError("bad_job", "This render is missing a valid caption style.") from exc


def words_of(jp: dict[str, Any]) -> list[tuple[str, int, int]]:
    return [(str(t), int(s), int(e)) for t, s, e in jp.get("words", [])]


def spec_of(
    ctx: StageContext,
    *,
    ass_path: str,
    src_w: int | None,
    src_h: int | None,
    measured: dict[str, float] | None,
) -> rp.RenderSpec:
    jp = ctx.job_params
    try:
        start, end = int(jp["start_ms"]), int(jp["end_ms"])
        aspect = str(jp["aspect"])
        crop = cl.normalize_crop(jp.get("crop"), end - start)
        audio = jp.get("audio") or {}
        return rp.RenderSpec(
            kind=str(jp["kind"]),
            aspect=aspect,
            src_w=src_w,
            src_h=src_h,
            has_video=ctx.source.has_video,
            has_audio=bool(ctx.source.has_audio),
            start_ms=start,
            end_ms=end,
            crop=crop,
            background=dict(jp.get("background") or {"type": "blur"}),
            gain_db=float(audio.get("gain_db", 0.0)),
            mute=bool(audio.get("mute", False)),
            ass_path=ass_path,
            measured=measured,
        )
    except (KeyError, ValueError, cl.CropError) as exc:
        raise StageError("bad_job", f"This render has invalid settings: {exc}") from exc


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}
