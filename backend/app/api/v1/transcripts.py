from __future__ import annotations

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.deps import CurrentUser, DbDep, RedisDep, SettingsDep, client_ip, require_role
from app.api.presenters import job_out
from app.db.models import User
from app.errors import AppError
from app.pipeline.asr_options import MODELS
from app.schemas.transcript import (
    AsrModelOut,
    AsrSettingsIn,
    AsrStatusOut,
    EditIn,
    RestoreIn,
    SegmentOut,
    TranscribeIn,
    TranscribeOut,
    TranscriptMeta,
    TranscriptOut,
    WordOut,
)
from app.services import jobs as jobs_svc
from app.services import transcripts as svc
from app.tasks.celery_app import celery_app

router = APIRouter(tags=["transcripts"])
AdminDep = Annotated[User, Depends(require_role("admin"))]
DL_KEY = "asr:dl:{}"


def _meta(t: Any) -> TranscriptMeta:
    return TranscriptMeta.model_validate(t)


@router.post("/sources/{source_id}/transcribe", response_model=TranscribeOut)
async def transcribe(
    source_id: uuid.UUID,
    body: TranscribeIn,
    request: Request,
    response: Response,
    user: CurrentUser,
    session: DbDep,
    redis: RedisDep,
    settings: SettingsDep,
) -> TranscribeOut:
    r = await svc.request_transcription(
        session,
        redis,
        settings,
        user,
        source_id,
        language=body.language,
        model=body.model,
        force=body.force,
        ip=client_ip(request),
    )
    if r.reused:
        return TranscribeOut(reused=True, reason=r.reason, transcript=_meta(r.transcript))
    response.status_code = 202
    assert r.job is not None
    return TranscribeOut(reused=False, reason="", job=job_out(*(await jobs_svc.get_job(session, r.job.id))))


@router.get("/sources/{source_id}/transcript", response_model=TranscriptOut)
async def get_transcript(
    source_id: uuid.UUID,
    user: CurrentUser,
    session: DbDep,
    version: int | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    words: bool = True,
) -> TranscriptOut:
    t, segs = await svc.read(session, user, source_id, version=version, offset=offset, limit=limit, with_words=words)
    return TranscriptOut(
        transcript=_meta(t),
        offset=offset,
        segments=[
            SegmentOut(
                id=s.id,
                idx=s.idx,
                start_ms=s.start_ms,
                end_ms=s.end_ms,
                text=s.text,
                edited=s.edited,
                words=[WordOut.model_validate(w) for w in ws],
            )
            for s, ws in segs
        ],
    )


@router.get("/sources/{source_id}/transcript/versions", response_model=list[TranscriptMeta])
async def list_versions(source_id: uuid.UUID, user: CurrentUser, session: DbDep) -> list[TranscriptMeta]:
    return [_meta(t) for t in await svc.versions(session, user, source_id)]


@router.patch("/sources/{source_id}/transcript", response_model=TranscriptMeta)
async def edit(
    source_id: uuid.UUID, body: EditIn, request: Request, user: CurrentUser, session: DbDep
) -> TranscriptMeta:
    seen: dict[int, str] = {}
    for e in body.edits:
        if e.segment_idx in seen:
            raise AppError("invalid_edits", f"Segment {e.segment_idx} appears twice.", 400)
        seen[e.segment_idx] = e.text
    t = await svc.edit_segments(
        session, user, source_id, base_version=body.base_version, edits=seen, ip=client_ip(request)
    )
    return _meta(t)


@router.post("/sources/{source_id}/transcript/restore", response_model=TranscriptMeta, status_code=201)
async def restore(
    source_id: uuid.UUID, body: RestoreIn, request: Request, user: CurrentUser, session: DbDep
) -> TranscriptMeta:
    return _meta(await svc.restore(session, user, source_id, body.version, client_ip(request)))


@router.get("/sources/{source_id}/transcript/export")
async def export(
    source_id: uuid.UUID, user: CurrentUser, session: DbDep, format: str = "srt", version: int | None = None
) -> Response:
    ex = await svc.export(session, user, source_id, format, version)
    return Response(
        ex.body,
        media_type=ex.media_type,
        headers={"Content-Disposition": f'attachment; filename="transcript.{ex.extension}"'},
    )


# ------------------------------------------------------------------------------------------------ speech settings
async def _heartbeat(redis: Any) -> dict[str, Any] | None:
    raw = await redis.get(svc.ASR_HEARTBEAT)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


@router.get("/asr/status", response_model=AsrStatusOut)
async def asr_status(_: CurrentUser, session: DbDep, redis: RedisDep, settings: SettingsDep) -> AsrStatusOut:
    hb = await _heartbeat(redis)
    have = set((hb or {}).get("downloaded", []))
    d = await svc.asr_settings(session, settings)
    return AsrStatusOut(
        available=hb is not None,
        device=(hb or {}).get("device"),
        compute_type=(hb or {}).get("compute"),
        default_model=d["model"],
        default_language=d["language"],
        models=[
            AsrModelOut(
                name=n,
                mb=int(str(m["mb"])),
                multilingual=bool(m["multilingual"]),
                note=str(m["note"]),
                downloaded=n in have,
            )
            for n, m in MODELS.items()
        ],
    )


@router.put("/asr/settings")
async def put_asr_settings(body: AsrSettingsIn, request: Request, admin: AdminDep, session: DbDep) -> dict[str, Any]:
    return await svc.save_asr_settings(session, admin, body.model, body.language, client_ip(request))


@router.post("/asr/models/{name}/download", status_code=202)
async def download_model(name: str, _: AdminDep, redis: RedisDep) -> dict[str, bool]:
    if name not in MODELS:
        raise AppError("invalid_model", f"Unknown speech model: {name}", 400)
    if not await redis.exists(svc.ASR_HEARTBEAT):
        raise AppError(
            "asr_unavailable", "The transcription service is not running. Start the 'worker-asr' service.", 503
        )
    await redis.set(
        DL_KEY.format(name),
        json.dumps({"status": "Waiting for the transcription service", "percent": 0, "done": False, "error": None}),
        ex=3600,
    )
    celery_app.send_task("asr.download_model", args=[name], queue="asr")
    return {"started": True}


@router.get("/asr/models/{name}/progress")
async def model_progress(name: str, _: CurrentUser, redis: RedisDep) -> dict[str, Any]:
    raw = await redis.get(DL_KEY.format(name))
    return json.loads(raw) if raw else {"status": "", "percent": 0, "done": False, "error": None, "idle": True}
