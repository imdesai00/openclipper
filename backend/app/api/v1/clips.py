from __future__ import annotations

import urllib.parse
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, DbDep, RedisDep, SettingsDep, StorageDep, client_ip
from app.api.presenters import job_out
from app.config import Settings
from app.db.models import Clip, ClipRender, ClipVersion, MediaAsset, ProcessingJob, User
from app.errors import AppError, not_found
from app.schemas.clip import (
    ClipOut,
    ClipSummary,
    ClipWord,
    CreateClipIn,
    GenerateMetadataIn,
    MetadataAllOut,
    MetadataFieldsIn,
    MetadataOut,
    PreviewCaption,
    PreviewDraftIn,
    PreviewStyleOut,
    RenderIn,
    RenderOut,
    RenderRequestedOut,
    TemplateIn,
    TemplateOut,
    TemplateUpdate,
    VersionIn,
    VersionOut,
)
from app.security.media_tokens import media_url
from app.services import caption_engine as ce
from app.services import clip_metadata as meta_svc
from app.services import clip_metadata_logic as meta_logic
from app.services import clips as svc
from app.services import jobs as jobs_svc
from app.services.access import Need

router = APIRouter(tags=["clips"])


def _version(v: ClipVersion) -> VersionOut:
    return VersionOut(
        id=v.id,
        version=v.version,
        start_ms=v.start_ms,
        end_ms=v.end_ms,
        aspect=v.aspect,
        crop=v.crop,
        background=v.background,
        audio=v.audio,
        caption_template_id=v.caption_template_id,
        caption_overrides=v.caption_overrides,
        transcript_id=v.transcript_id,
        created_at=v.created_at,
    )


async def _render_out(session: AsyncSession, settings: Settings, r: ClipRender, title: str, version: int) -> RenderOut:
    job = await session.get(ProcessingJob, r.job_id) if r.job_id else None
    state = svc.render_state(r, job)
    play = dl = thumb = None
    if r.status == "done" and r.asset_id:
        asset = await session.get(MediaAsset, r.asset_id)
        if asset is not None and asset.deleted_at is None:
            play = media_url(r.asset_id, settings)
            name = urllib.parse.quote(f"{title}-v{version}-{r.kind}.mp4")
            dl = f"{play}&dl=1&name={name}" if r.kind == "final" else None
    if r.thumbnail_asset_id:
        thumb = media_url(r.thumbnail_asset_id, settings)
    return RenderOut(
        id=r.id,
        kind=r.kind,
        version=version,
        state=state,
        error=(job.error_message if job is not None and state == "failed" else None),
        created_at=r.created_at,
        width=r.width,
        height=r.height,
        duration_ms=r.duration_ms,
        size_bytes=r.size_bytes,
        loudness_lufs=r.loudness_lufs,
        qc_status=r.qc_status,
        qc_report=r.qc_report,
        play_url=play,
        download_url=dl,
        thumbnail_url=thumb,
        job=job_out(*(await jobs_svc.get_job(session, job.id)))
        if job is not None and state in ("running", "failed")
        else None,
    )


async def _clip_out(session: AsyncSession, settings: Settings, user: User, clip: Clip) -> ClipOut:
    _, v, _ = await svc._clip_and_version(session, user, clip.id, Need.READ)
    versions = await svc.list_versions(session, user, clip.id)
    nums = {x.id: x.version for x in versions}
    renders = await svc.renders_for(session, list(nums))
    outs = [await _render_out(session, settings, r, clip.title, nums[r.clip_version_id]) for r, _ in renders[:12]]
    style_raw = await svc.style_for(session, v.caption_template_id)
    style = ce.merge_style(style_raw, {k: x for k, x in (v.caption_overrides or {}).items() if k != "enabled"})
    tname = None
    if v.caption_template_id:
        t = next((t for t in await svc.list_templates(session, user) if t.id == v.caption_template_id), None)
        tname = t.name if t else None
    return ClipOut(
        id=clip.id,
        project_id=clip.project_id,
        source_id=clip.source_id,
        candidate_id=clip.candidate_id,
        title=clip.title,
        current_version=clip.current_version,
        version=_version(v),
        style=style.model_dump(),
        template_name=tname,
        renders=outs,
        created_at=clip.created_at,
    )


@router.post("/sources/{source_id}/clips", response_model=ClipOut, status_code=201)
async def create_clip(
    source_id: uuid.UUID, body: CreateClipIn, request: Request, user: CurrentUser, session: DbDep, settings: SettingsDep
) -> ClipOut:
    clip = await svc.create_clip(session, user, source_id, body.model_dump(), client_ip(request))
    return await _clip_out(session, settings, user, clip)


@router.post("/highlights/{candidate_id}/clip", response_model=ClipOut)
async def clip_from_highlight(
    candidate_id: uuid.UUID,
    request: Request,
    response: Response,
    user: CurrentUser,
    session: DbDep,
    settings: SettingsDep,
) -> ClipOut:
    clip, created = await svc.create_from_highlight(session, user, candidate_id, client_ip(request))
    response.status_code = 201 if created else 200
    return await _clip_out(session, settings, user, clip)


@router.get("/sources/{source_id}/clips", response_model=list[ClipSummary])
async def list_clips(
    source_id: uuid.UUID, user: CurrentUser, session: DbDep, settings: SettingsDep
) -> list[ClipSummary]:
    out: list[ClipSummary] = []
    for c in await svc.list_clips(session, user, source_id):
        _, v, _ = await svc._clip_and_version(session, user, c.id, Need.READ)
        versions = await svc.list_versions(session, user, c.id)
        rs = await svc.renders_for(session, [x.id for x in versions])
        best = next((r for r, _ in rs if r.kind == "final" and r.status == "done"), None)
        state = best.qc_status if best else (rs[0][0].status if rs else None)
        out.append(
            ClipSummary(
                id=c.id,
                source_id=c.source_id,
                title=c.title,
                current_version=c.current_version,
                start_ms=v.start_ms,
                end_ms=v.end_ms,
                aspect=v.aspect,
                created_at=c.created_at,
                candidate_id=c.candidate_id,
                best_render_state=state,
                thumbnail_url=media_url(best.thumbnail_asset_id, settings)
                if best and best.thumbnail_asset_id
                else None,
            )
        )
    return out


@router.get("/clips/{clip_id}", response_model=ClipOut)
async def get_clip(clip_id: uuid.UUID, user: CurrentUser, session: DbDep, settings: SettingsDep) -> ClipOut:
    clip = await session.get(Clip, clip_id)
    if clip is None:
        raise not_found("Clip")
    return await _clip_out(session, settings, user, clip)


@router.delete("/clips/{clip_id}", status_code=204)
async def delete_clip(
    clip_id: uuid.UUID, request: Request, user: CurrentUser, session: DbDep, storage: StorageDep
) -> Response:
    await svc.delete_clip(session, storage, user, clip_id, client_ip(request))
    return Response(status_code=204)


@router.get("/clips/{clip_id}/versions", response_model=list[VersionOut])
async def list_versions(clip_id: uuid.UUID, user: CurrentUser, session: DbDep) -> list[VersionOut]:
    return [_version(v) for v in await svc.list_versions(session, user, clip_id)]


@router.post("/clips/{clip_id}/versions", response_model=ClipOut, status_code=201)
async def save_version(
    clip_id: uuid.UUID, body: VersionIn, request: Request, user: CurrentUser, session: DbDep, settings: SettingsDep
) -> ClipOut:
    await svc.save_version(
        session, user, clip_id, body.base_version, body.model_dump(exclude={"base_version"}), client_ip(request)
    )
    clip = await session.get(Clip, clip_id)
    assert clip is not None
    return await _clip_out(session, settings, user, clip)


@router.post("/clips/{clip_id}/render", response_model=RenderRequestedOut)
async def render(
    clip_id: uuid.UUID,
    body: RenderIn,
    request: Request,
    response: Response,
    user: CurrentUser,
    session: DbDep,
    settings: SettingsDep,
) -> RenderRequestedOut:
    r = await svc.request_render(
        session, user, settings, clip_id, version=body.version, kind=body.kind, force=body.force, ip=client_ip(request)
    )
    _, v, _ = await svc._clip_and_version(session, user, clip_id, Need.READ, body.version)
    clip = await session.get(Clip, clip_id)
    assert clip is not None
    out = await _render_out(session, settings, r.render, clip.title, v.version)
    if r.job is not None:
        response.status_code = 202
    return RenderRequestedOut(reused=r.job is None, render=out)


@router.get("/clips/{clip_id}/captions")
async def captions(
    clip_id: uuid.UUID, user: CurrentUser, session: DbDep, format: str = "srt", version: int | None = None
) -> Response:
    body, ext = await svc.export_captions(session, user, clip_id, format, version)
    media = "application/x-subrip; charset=utf-8" if ext == "srt" else "text/vtt; charset=utf-8"
    return PlainTextResponse(
        body, media_type=media, headers={"Content-Disposition": f'attachment; filename="captions.{ext}"'}
    )


async def _preview(
    session: AsyncSession,
    user: User,
    clip_id: uuid.UUID,
    version: int | None,
    width: int,
    height: int,
    draft: PreviewDraftIn | None,
) -> PreviewStyleOut:
    _, v, _ = await svc._clip_and_version(session, user, clip_id, Need.READ, version)
    template_id = draft.caption_template_id if draft and draft.caption_template_id else v.caption_template_id
    overrides = dict(
        draft.caption_overrides if draft and draft.caption_overrides is not None else (v.caption_overrides or {})
    )
    start = draft.start_ms if draft and draft.start_ms is not None else v.start_ms
    end = draft.end_ms if draft and draft.end_ms is not None else v.end_ms
    try:
        style = ce.merge_style(
            await svc.style_for(session, template_id), {k: x for k, x in overrides.items() if k != "enabled"}
        )
    except ValueError as exc:
        raise AppError("invalid_style", svc._short(exc), 400) from exc
    words = await svc._words_in_range(session, v.transcript_id, start, end)
    cues = ce.build_cues([(str(t), int(s), int(e)) for t, s, e in words], start, end, style)
    enabled = overrides.get("enabled", True)
    return PreviewStyleOut(
        style=style.model_dump(),
        css=ce.compile_css(style, width, height),
        cues=[
            PreviewCaption(
                start_ms=c.start_ms,
                end_ms=c.end_ms,
                lines=[[{"text": w.text, "start_ms": w.start_ms, "end_ms": w.end_ms} for w in ln] for ln in c.lines],
            )
            for c in (cues if enabled else [])
        ],
        output_width=width,
        output_height=height,
        fonts=list(ce.FONTS),
    )


@router.get("/clips/{clip_id}/preview-style", response_model=PreviewStyleOut)
async def preview_style(
    clip_id: uuid.UUID,
    user: CurrentUser,
    session: DbDep,
    width: Annotated[int, Query(ge=120, le=4000)] = 1080,
    height: Annotated[int, Query(ge=120, le=4000)] = 1920,
    version: int | None = None,
) -> PreviewStyleOut:
    """The caption look and the cues of a SAVED version, from the same code the render uses."""
    return await _preview(session, user, clip_id, version, width, height, None)


@router.post("/clips/{clip_id}/preview-style", response_model=PreviewStyleOut)
async def preview_style_draft(
    clip_id: uuid.UUID, body: PreviewDraftIn, user: CurrentUser, session: DbDep
) -> PreviewStyleOut:
    """Same, for settings the editor has not saved yet. Nothing is stored."""
    return await _preview(session, user, clip_id, None, body.width, body.height, body)


@router.get("/clips/{clip_id}/words", response_model=list[ClipWord])
async def clip_words(
    clip_id: uuid.UUID,
    user: CurrentUser,
    session: DbDep,
    pad_ms: Annotated[int, Query(ge=0, le=60_000)] = 8000,
    version: int | None = None,
) -> list[ClipWord]:
    """The transcript words in the clip's range plus some context either side, for the editor's transcript panel."""
    _, v, _ = await svc._clip_and_version(session, user, clip_id, Need.READ, version)
    if v.transcript_id is None:
        return []
    words = await svc._words_in_range(session, v.transcript_id, v.start_ms - pad_ms, v.end_ms + pad_ms)
    return [
        ClipWord(
            text=str(t), start_ms=int(s), end_ms=int(e), inside=int(s) >= v.start_ms - 40 and int(e) <= v.end_ms + 40
        )
        for t, s, e in words
    ]


# ------------------------------------------------------------------------------------------------ templates
def _tpl(t: Any) -> TemplateOut:
    return TemplateOut(id=t.id, name=t.name, is_builtin=t.is_builtin, style=t.style)


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(user: CurrentUser, session: DbDep) -> list[TemplateOut]:
    return [_tpl(t) for t in await svc.list_templates(session, user)]


@router.post("/templates", response_model=TemplateOut, status_code=201)
async def create_template(body: TemplateIn, request: Request, user: CurrentUser, session: DbDep) -> TemplateOut:
    return _tpl(await svc.create_template(session, user, body.name, body.style, client_ip(request)))


@router.put("/templates/{template_id}", response_model=TemplateOut)
async def update_template(
    template_id: uuid.UUID, body: TemplateUpdate, user: CurrentUser, session: DbDep
) -> TemplateOut:
    return _tpl(await svc.update_template(session, user, template_id, body.name, body.style))


@router.delete("/templates/{template_id}", status_code=204)
async def delete_template(template_id: uuid.UUID, user: CurrentUser, session: DbDep) -> Response:
    await svc.delete_template(session, user, template_id)
    return Response(status_code=204)


# ------------------------------------------------------------------------------------------------ metadata
@router.get("/clips/{clip_id}/metadata", response_model=MetadataAllOut)
async def get_metadata(clip_id: uuid.UUID, user: CurrentUser, session: DbDep) -> MetadataAllOut:
    rows = await meta_svc.get_all(session, user, clip_id)
    job = await meta_svc.latest_job(session, clip_id)
    return MetadataAllOut(
        platforms={p: MetadataOut(**{k: v for k, v in r.items() if k not in ("updated_at",)}) for p, r in rows.items()},
        limits=meta_logic.LIMITS,
        soft_hashtag_limits=meta_logic.SOFT_HASHTAGS,
        job=job_out(*(await jobs_svc.get_job(session, job.id))) if job is not None else None,
    )


@router.put("/clips/{clip_id}/metadata/{platform}", response_model=MetadataOut)
async def put_metadata(
    clip_id: uuid.UUID, platform: str, body: MetadataFieldsIn, request: Request, user: CurrentUser, session: DbDep
) -> MetadataOut:
    r = await meta_svc.update(session, user, clip_id, platform, body.model_dump(exclude_none=True), client_ip(request))
    return MetadataOut(**{k: v for k, v in r.items() if k != "updated_at"})


@router.post("/clips/{clip_id}/metadata/generate", status_code=202)
async def generate_metadata(
    clip_id: uuid.UUID, body: GenerateMetadataIn, request: Request, user: CurrentUser, session: DbDep, redis: RedisDep
) -> dict[str, Any]:
    job = await meta_svc.request_generation(
        session, redis, user, clip_id, list(body.platforms), body.overwrite, client_ip(request)
    )
    return {"job": job_out(*(await jobs_svc.get_job(session, job.id))).model_dump(mode="json")}
