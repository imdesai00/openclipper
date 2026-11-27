"""Clips: create, version, render (SPEC sections 6.F, 6.G). A clip is a name plus a history of immutable versions.

Editing never touches a transcript or a highlight run, and every save is a new version. A render belongs to one version
and is identified by a hash of everything that changes how it looks or sounds, so asking again for the same thing is
answered from the stored file instead of re-encoding it."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    CaptionTemplate,
    Clip,
    ClipRender,
    ClipVersion,
    HighlightCandidate,
    HighlightRun,
    JobStage,
    MediaAsset,
    ProcessingJob,
    User,
    VideoSource,
)
from app.errors import AppError, conflict, not_found
from app.ids import uuid7
from app.pipeline.registry import RENDER_CLIP, RENDER_PREVIEW
from app.pipeline.types import RenderDone
from app.ports.storage import StorageBackend
from app.services import audit, jobs, sources
from app.services import caption_engine as ce
from app.services import crop_logic as cl
from app.services import highlights as hl_svc
from app.services import render_plan as rp
from app.services.access import Need, require_project
from app.services.transcripts import current as current_transcript

log = logging.getLogger(__name__)
ENGINE_VERSION = 1  # bump when rendering changes in a way that should not reuse old files
DEFAULT_TEMPLATE = "Bold highlight"
MIN_CLIP_MS = 1_000
MAX_CLIP_MS = 600_000
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


# ------------------------------------------------------------------------------------------------ templates
async def ensure_templates(session: AsyncSession) -> dict[str, CaptionTemplate]:
    """The built-in templates always exist; created on first use, never overwritten."""
    rows = {
        t.name: t
        for t in (await session.execute(select(CaptionTemplate).where(CaptionTemplate.is_builtin.is_(True)))).scalars()
    }
    added = False
    for name, over in ce.BUILTIN_TEMPLATES.items():
        if name not in rows:
            t = CaptionTemplate(
                id=uuid7(),
                name=name,
                schema_version=ce.SCHEMA_VERSION,
                style=ce.merge_style({}, over).model_dump(),
                is_builtin=True,
            )
            session.add(t)
            rows[name] = t
            added = True
    if added:
        await session.commit()
    return rows


async def list_templates(session: AsyncSession, user: User) -> list[CaptionTemplate]:
    builtin = await ensure_templates(session)
    own = list(
        (
            await session.execute(
                select(CaptionTemplate)
                .where(CaptionTemplate.is_builtin.is_(False), CaptionTemplate.owner_id == user.id)
                .order_by(CaptionTemplate.name)
            )
        ).scalars()
    )
    return [*builtin.values(), *own]


def _valid_style(raw: dict[str, Any]) -> dict[str, Any]:
    try:
        return ce.validate_style(raw).model_dump()
    except ValueError as exc:
        raise AppError("invalid_style", _short(exc), 400) from exc


def _short(exc: Exception) -> str:
    return str(exc).splitlines()[0][:300] if str(exc) else exc.__class__.__name__


async def create_template(
    session: AsyncSession, user: User, name: str, style: dict[str, Any], ip: str | None
) -> CaptionTemplate:
    t = CaptionTemplate(
        id=uuid7(),
        name=name.strip()[:100] or "Untitled",
        schema_version=ce.SCHEMA_VERSION,
        style=_valid_style(style),
        is_builtin=False,
        owner_id=user.id,
    )
    session.add(t)
    audit.record(session, "template.created", actor_id=user.id, target_type="template", target_id=str(t.id), ip=ip)
    await session.commit()
    return t


async def _own_template(session: AsyncSession, user: User, template_id: uuid.UUID) -> CaptionTemplate:
    t = await session.get(CaptionTemplate, template_id)
    if t is None or (not t.is_builtin and t.owner_id != user.id):
        raise not_found("Template")
    if t.is_builtin:
        raise AppError("builtin_template", "Built-in templates cannot be changed. Make a copy instead.", 409)
    return t


async def update_template(
    session: AsyncSession, user: User, template_id: uuid.UUID, name: str | None, style: dict[str, Any] | None
) -> CaptionTemplate:
    t = await _own_template(session, user, template_id)
    if name is not None:
        t.name = name.strip()[:100] or t.name
    if style is not None:
        t.style = _valid_style(style)
    await session.commit()
    return t


async def delete_template(session: AsyncSession, user: User, template_id: uuid.UUID) -> None:
    t = await _own_template(session, user, template_id)
    await session.delete(t)  # versions that used it fall back to the default look
    await session.commit()


async def style_for(session: AsyncSession, template_id: uuid.UUID | None) -> dict[str, Any]:
    if template_id:
        t = await session.get(CaptionTemplate, template_id)
        if t is not None:
            return dict(t.style)
    return ce.CaptionStyle().model_dump()


# ----------------------------------------------------------------------------------- validation of a version's fields
@dataclass
class Fields:
    start_ms: int
    end_ms: int
    aspect: str
    crop: dict[str, Any]
    background: dict[str, Any]
    audio: dict[str, Any]
    caption_overrides: dict[str, Any]
    caption_template_id: uuid.UUID | None


def _background(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    kind = raw.get("type", "blur")
    if kind not in ("blur", "solid"):
        raise AppError("invalid_background", "Background must be blur or solid.", 400)
    color = raw.get("color", "#000000")
    if not isinstance(color, str) or not _HEX.match(color):
        raise AppError("invalid_background", "Background colour must look like #RRGGBB.", 400)
    return {"type": kind, "color": color.upper()}


def _audio(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw or {}
    try:
        gain = float(raw.get("gain_db", 0.0))
    except (TypeError, ValueError) as exc:
        raise AppError("invalid_audio", "Gain must be a number of decibels.", 400) from exc
    if not -24.0 <= gain <= 24.0:
        raise AppError("invalid_audio", "Gain must be between -24 and +24 dB.", 400)
    return {"gain_db": round(gain, 1), "mute": bool(raw.get("mute", False))}


async def normalise(session: AsyncSession, src: VideoSource, body: dict[str, Any], base: ClipVersion | None) -> Fields:
    """Validate everything the editor can send, so a bad value is a 400 with a reason and never a failed render."""

    def pick(key: str, default: Any) -> Any:
        return body[key] if body.get(key) is not None else (getattr(base, key) if base is not None else default)

    start, end = int(pick("start_ms", 0)), int(pick("end_ms", 0))
    duration_ms = round((src.duration_s or 0) * 1000)
    if not 0 <= start < end or (duration_ms and end > duration_ms + 50):
        raise AppError("invalid_range", "The clip must start and end inside the video.", 400)
    if not MIN_CLIP_MS <= end - start <= MAX_CLIP_MS:
        raise AppError("invalid_range", "A clip must be between 1 second and 10 minutes long.", 400)
    aspect = str(pick("aspect", "9:16"))
    if aspect not in cl.ASPECTS:
        raise AppError("invalid_aspect", "Shape must be 9:16, 1:1 or 16:9.", 400)
    try:
        crop = cl.normalize_crop(pick("crop", {}), end - start).as_dict()
    except cl.CropError as exc:
        raise AppError("invalid_crop", str(exc), 400) from exc
    template_id = pick("caption_template_id", None)
    template_id = uuid.UUID(str(template_id)) if template_id else None
    if template_id is not None and await session.get(CaptionTemplate, template_id) is None:
        raise AppError("invalid_template", "That caption template does not exist.", 400)
    overrides = dict(pick("caption_overrides", {}) or {})
    style_over = {k: v for k, v in overrides.items() if k != "enabled"}
    try:
        ce.merge_style(await style_for(session, template_id), style_over)
    except ValueError as exc:
        raise AppError("invalid_style", _short(exc), 400) from exc
    if "enabled" in overrides:
        overrides["enabled"] = bool(overrides["enabled"])
    return Fields(
        start, end, aspect, crop, _background(pick("background", {})), _audio(pick("audio", {})), overrides, template_id
    )


# ------------------------------------------------------------------------------------------------ clips and versions
async def _clip_and_version(
    session: AsyncSession, user: User, clip_id: uuid.UUID, need: Need, version: int | None = None
) -> tuple[Clip, ClipVersion, VideoSource]:
    clip = await session.get(Clip, clip_id)
    if clip is None:
        raise not_found("Clip")
    await require_project(session, user, clip.project_id, need)
    v = (
        await session.execute(
            select(ClipVersion).where(
                ClipVersion.clip_id == clip.id, ClipVersion.version == (version or clip.current_version)
            )
        )
    ).scalar_one_or_none()
    src = await session.get(VideoSource, clip.source_id)
    if v is None or src is None:
        raise not_found("Clip version")
    return clip, v, src


async def create_clip(
    session: AsyncSession,
    user: User,
    source_id: uuid.UUID,
    body: dict[str, Any],
    ip: str | None,
    *,
    candidate: HighlightCandidate | None = None,
) -> Clip:
    src = await sources.get_source(session, user, source_id, Need.WRITE)
    if src.status != "ready":
        raise AppError("source_not_ready", "Wait until this video has finished processing.", 409)
    templates = await ensure_templates(session)
    if candidate is not None:
        body = {**body, "start_ms": candidate.start_ms, "end_ms": candidate.end_ms}
    if not body.get("caption_template_id"):
        body = {**body, "caption_template_id": str(templates[DEFAULT_TEMPLATE].id)}
    f = await normalise(session, src, body, None)
    t = await current_transcript(session, src.id)
    title = (body.get("title") or (candidate.title if candidate else "") or src.title)[:200]
    clip = Clip(
        id=uuid7(),
        project_id=src.project_id,
        source_id=src.id,
        candidate_id=candidate.id if candidate else None,
        title=title,
        current_version=1,
        created_by=user.id,
    )
    session.add(clip)
    await session.flush()
    session.add(_version_row(clip.id, 1, f, t.id if t else None, user.id))
    audit.record(
        session,
        "clip.created",
        actor_id=user.id,
        target_type="clip",
        target_id=str(clip.id),
        details={"source": str(src.id), "from_highlight": candidate is not None},
        ip=ip,
    )
    await session.commit()
    return clip


def _version_row(
    clip_id: uuid.UUID, n: int, f: Fields, transcript_id: uuid.UUID | None, user_id: uuid.UUID
) -> ClipVersion:
    return ClipVersion(
        id=uuid7(),
        clip_id=clip_id,
        version=n,
        start_ms=f.start_ms,
        end_ms=f.end_ms,
        aspect=f.aspect,
        crop=f.crop,
        background=f.background,
        caption_template_id=f.caption_template_id,
        caption_overrides=f.caption_overrides,
        audio=f.audio,
        transcript_id=transcript_id,
        created_by=user_id,
    )


async def create_from_highlight(
    session: AsyncSession, user: User, candidate_id: uuid.UUID, ip: str | None
) -> tuple[Clip, bool]:
    """Open a suggested highlight as a clip. Asking twice returns the same clip, so nobody ends up with duplicates."""
    cand = await session.get(HighlightCandidate, candidate_id)
    run = await session.get(HighlightRun, cand.run_id) if cand else None
    if cand is None or run is None:
        raise not_found("Highlight")
    existing = (
        await session.execute(select(Clip).where(Clip.candidate_id == cand.id).order_by(Clip.created_at).limit(1))
    ).scalar_one_or_none()
    if existing is not None:
        await require_project(session, user, existing.project_id, Need.WRITE)
        return existing, False
    return await create_clip(session, user, run.source_id, {}, ip, candidate=cand), True


async def save_version(
    session: AsyncSession, user: User, clip_id: uuid.UUID, base_version: int, body: dict[str, Any], ip: str | None
) -> ClipVersion:
    clip = (await session.execute(select(Clip).where(Clip.id == clip_id).with_for_update())).scalar_one_or_none()
    if clip is None:
        raise not_found("Clip")
    await require_project(session, user, clip.project_id, Need.WRITE)
    if clip.current_version != base_version:  # someone saved first: never overwrite silently
        raise AppError(
            "version_conflict",
            "This clip was changed since you opened it. Reload and try again.",
            409,
            details={"current_version": clip.current_version},
        )
    _, base, src = await _clip_and_version(session, user, clip_id, Need.WRITE)
    f = await normalise(session, src, body, base)
    transcript_id = base.transcript_id
    if body.get("refresh_transcript"):  # pick up transcript edits made since this clip was last saved
        t = await current_transcript(session, src.id)
        transcript_id = t.id if t else None
    n = clip.current_version + 1
    row = _version_row(clip.id, n, f, transcript_id, user.id)
    session.add(row)
    clip.current_version = n
    if body.get("title"):
        clip.title = str(body["title"])[:200]
    audit.record(
        session,
        "clip.version_saved",
        actor_id=user.id,
        target_type="clip",
        target_id=str(clip.id),
        details={"version": n},
        ip=ip,
    )
    await session.commit()
    return row


async def list_versions(session: AsyncSession, user: User, clip_id: uuid.UUID) -> list[ClipVersion]:
    await _clip_and_version(session, user, clip_id, Need.READ)
    return list(
        (
            await session.execute(
                select(ClipVersion).where(ClipVersion.clip_id == clip_id).order_by(ClipVersion.version.desc())
            )
        ).scalars()
    )


async def list_clips(session: AsyncSession, user: User, source_id: uuid.UUID) -> list[Clip]:
    await sources.get_source(session, user, source_id, Need.READ)
    return list(
        (
            await session.execute(select(Clip).where(Clip.source_id == source_id).order_by(Clip.created_at.desc()))
        ).scalars()
    )


async def delete_clip(
    session: AsyncSession, storage: StorageBackend, user: User, clip_id: uuid.UUID, ip: str | None
) -> None:
    clip, _, _ = await _clip_and_version(session, user, clip_id, Need.WRITE)
    vids = list((await session.execute(select(ClipVersion.id).where(ClipVersion.clip_id == clip.id))).scalars())
    renders = (
        list((await session.execute(select(ClipRender).where(ClipRender.clip_version_id.in_(vids)))).scalars())
        if vids
        else []
    )
    for r in renders:
        if r.job_id and await _job_active(session, r.job_id):
            raise conflict("job_active", "A render is still running for this clip. Cancel it first.")
    ids = {i for r in renders for i in (r.asset_id, r.thumbnail_asset_id, r.subtitle_asset_id) if i}
    job_ids = [r.job_id for r in renders if r.job_id]
    if job_ids:
        ids |= {
            i
            for i in (
                await session.execute(
                    select(JobStage.output_asset_id).where(
                        JobStage.job_id.in_(job_ids), JobStage.output_asset_id.is_not(None)
                    )
                )
            ).scalars()
            if i
        }
    assets = list((await session.execute(select(MediaAsset).where(MediaAsset.id.in_(ids)))).scalars()) if ids else []
    keys = [a.storage_key for a in assets]
    audit.record(
        session,
        "clip.deleted",
        actor_id=user.id,
        target_type="clip",
        target_id=str(clip.id),
        details={"title": clip.title, "files": len(keys)},
        ip=ip,
    )
    for a in assets:
        await session.delete(a)
    await session.delete(clip)
    await session.commit()
    for k in keys:
        try:
            storage.delete(k)
        except Exception:  # a leftover file is a storage problem, not a reason to keep the clip
            log.exception("could not delete stored object", extra={"extra_fields": {"key": k}})


async def _job_active(session: AsyncSession, job_id: uuid.UUID) -> bool:
    job = await session.get(ProcessingJob, job_id)
    return bool(job and job.status in ("queued", "running"))


# ------------------------------------------------------------------------------------------------ render
async def _words_in_range(
    session: AsyncSession, transcript_id: uuid.UUID | None, start: int, end: int
) -> list[list[Any]]:
    if transcript_id is None:
        return []
    t = await hl_svc.load_view(session, transcript_id)
    return [[w.text, w.start_ms, w.end_ms] for w in t.words if w.end_ms >= start - 200 and w.start_ms <= end + 200]


def render_hash_of(params: dict[str, Any], media_hash: str | None) -> str:
    keys = ("kind", "start_ms", "end_ms", "aspect", "crop", "background", "audio", "style", "words", "captions_enabled")
    blob = json.dumps(
        {"v": ENGINE_VERSION, "media": media_hash, **{k: params[k] for k in keys}}, sort_keys=True, default=str
    )
    return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class RenderRequested:
    render: ClipRender
    job: ProcessingJob | None  # None when an identical finished render was reused


async def request_render(
    session: AsyncSession,
    user: User,
    settings: Settings,
    clip_id: uuid.UUID,
    *,
    version: int | None,
    kind: str,
    force: bool,
    ip: str | None,
) -> RenderRequested:
    if kind not in ("preview", "final"):
        raise AppError("invalid_kind", "Render kind must be preview or final.", 400)
    clip, v, src = await _clip_and_version(session, user, clip_id, Need.WRITE, version)
    if src.status != "ready":
        raise AppError("source_not_ready", "The source video is not ready.", 409)
    overrides = dict(v.caption_overrides or {})
    style = ce.merge_style(
        await style_for(session, v.caption_template_id), {k: x for k, x in overrides.items() if k != "enabled"}
    )
    words = await _words_in_range(session, v.transcript_id, v.start_ms, v.end_ms)
    params: dict[str, Any] = {
        "kind": kind,
        "clip_id": str(clip.id),
        "version_id": str(v.id),
        "title": clip.title,
        "start_ms": v.start_ms,
        "end_ms": v.end_ms,
        "aspect": v.aspect,
        "crop": v.crop,
        "background": v.background,
        "audio": v.audio,
        "style": style.model_dump(),
        "words": words,
        "captions_enabled": bool(words) and overrides.get("enabled", True),
    }
    rhash = render_hash_of(params, src.media_hash or str(src.id))
    prior = (
        await session.execute(
            select(ClipRender)
            .where(ClipRender.clip_version_id == v.id, ClipRender.kind == kind, ClipRender.render_hash == rhash)
            .order_by(ClipRender.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if prior is not None and prior.status == "done" and prior.asset_id and not force:
        asset = await session.get(MediaAsset, prior.asset_id)
        if asset is not None and asset.deleted_at is None:
            return RenderRequested(prior, None)
    if prior is not None and prior.job_id and await _job_active(session, prior.job_id):
        raise conflict("job_active", "This clip is already being rendered with these settings.")
    render = ClipRender(
        id=uuid7(), clip_version_id=v.id, kind=kind, status="pending", render_hash=rhash, requested_by=user.id
    )
    session.add(render)
    await session.flush()
    params |= {
        "render_id": str(render.id),
        "render_hash": rhash if not force else f"{rhash}:{uuid.uuid4().hex}",  # force skips stage checkpoints too
    }
    job = await jobs.create_job(
        session, src, user.id, kind=RENDER_CLIP if kind == "final" else RENDER_PREVIEW, params=params
    )
    render.job_id = job.id
    audit.record(
        session,
        "clip.render_requested",
        actor_id=user.id,
        target_type="clip",
        target_id=str(clip.id),
        details={"kind": kind, "version": v.version},
        ip=ip,
    )
    await session.commit()
    jobs.dispatch(job.id, await jobs.pending_stages(session, job.id), src.kind)
    return RenderRequested(render, job)


async def persist_render(session: AsyncSession, *, job: ProcessingJob, data: RenderDone) -> None:
    """Called by the runner when the last render stage finishes: attach the files and the quality report."""
    render = await session.get(ClipRender, uuid.UUID(data.render_id))
    if render is None:
        return  # the clip was deleted while it rendered
    out = {
        js.stage: js.output_asset_id
        for js in (await session.execute(select(JobStage).where(JobStage.job_id == job.id))).scalars()
    }
    render.asset_id = out.get("clip_render")
    render.thumbnail_asset_id = out.get("clip_thumbnail")
    render.subtitle_asset_id = out.get("clip_subtitles")
    render.width, render.height, render.duration_ms, render.size_bytes = (
        data.width,
        data.height,
        data.duration_ms,
        data.size_bytes,
    )
    render.loudness_lufs = data.loudness_lufs
    render.qc_status, render.qc_report = data.qc_status, data.qc_report
    render.status = "done"
    await session.flush()


async def renders_for(
    session: AsyncSession, version_ids: list[uuid.UUID]
) -> list[tuple[ClipRender, ProcessingJob | None]]:
    if not version_ids:
        return []
    rows = list(
        (
            await session.execute(
                select(ClipRender)
                .where(ClipRender.clip_version_id.in_(version_ids))
                .order_by(ClipRender.created_at.desc())
            )
        ).scalars()
    )
    out: list[tuple[ClipRender, ProcessingJob | None]] = []
    for r in rows:
        out.append((r, await session.get(ProcessingJob, r.job_id) if r.job_id else None))
    return out


def render_state(r: ClipRender, job: ProcessingJob | None) -> str:
    if r.status == "done":
        return "done"
    if job is None:
        return "pending"
    return {"queued": "running", "running": "running", "failed": "failed", "cancelled": "cancelled"}.get(
        job.status, "pending"
    )


async def get_render(session: AsyncSession, user: User, clip_id: uuid.UUID, render_id: uuid.UUID) -> ClipRender:
    clip, _, _ = await _clip_and_version(session, user, clip_id, Need.READ)
    r = await session.get(ClipRender, render_id)
    v = await session.get(ClipVersion, r.clip_version_id) if r else None
    if r is None or v is None or v.clip_id != clip.id:
        raise not_found("Render")
    return r


# ------------------------------------------------------------------------------------------------ captions export
async def export_captions(
    session: AsyncSession, user: User, clip_id: uuid.UUID, fmt: str, version: int | None
) -> tuple[str, str]:
    from app.services.transcript_logic import Cue as TCue
    from app.services.transcript_logic import to_srt, to_vtt

    if fmt not in ("srt", "vtt"):
        raise AppError("invalid_format", "Format must be srt or vtt.", 400)
    _, v, _ = await _clip_and_version(session, user, clip_id, Need.READ, version)
    style = ce.merge_style(
        await style_for(session, v.caption_template_id),
        {k: x for k, x in (v.caption_overrides or {}).items() if k != "enabled"},
    )
    words = await _words_in_range(session, v.transcript_id, v.start_ms, v.end_ms)
    cues = ce.build_cues([(str(t), int(s), int(e)) for t, s, e in words], v.start_ms, v.end_ms, style)
    plain = [TCue(c.start_ms, c.end_ms, "\n".join(" ".join(w.text for w in ln) for ln in c.lines)) for c in cues]
    return (to_srt(plain) if fmt == "srt" else to_vtt(plain)), fmt


def output_size_of(kind: str, aspect: str) -> tuple[int, int]:
    return rp.output_size(kind, aspect)
