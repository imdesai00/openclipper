"""Transcripts: request, persist, cache, version, edit, export (SPEC §6.D).

A transcript version is immutable. Recognition creates version N; an edit or a restore creates version N+1 and moves the
"current" marker. Nothing here ever calls the speech engine except by creating a job.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.models import (
    ProcessingJob,
    Setting,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
    User,
    VideoSource,
)
from app.errors import AppError, conflict, not_found
from app.ids import uuid7
from app.pipeline.asr_options import MODELS, normalize_language
from app.pipeline.registry import TRANSCRIBE
from app.ports.asr import TranscriptData
from app.services import audit, jobs, sources
from app.services.access import Need
from app.services.transcript_logic import (
    W,
    align_edit,
    compute_cache_key,
    make_cues,
    seconds_to_ms,
    tidy,
    to_srt,
    to_vtt,
)

ASR_HEARTBEAT = "asr:heartbeat"
ENGINE = "faster-whisper"
MAX_EDITS = 200
MAX_SEGMENT_CHARS = 2000


@dataclass
class SegRow:
    start_ms: int
    end_ms: int
    text: str
    words: list[W]
    edited: bool = False
    speaker_id: uuid.UUID | None = None


@dataclass
class Requested:
    reused: bool
    transcript: Transcript | None = None
    job: ProcessingJob | None = None
    reason: str = ""


def _chunks(rows: list[dict[str, Any]], n: int) -> Iterator[list[dict[str, Any]]]:
    for i in range(0, len(rows), n):
        yield rows[i : i + n]


# ------------------------------------------------------------------------------------------------ settings
async def asr_settings(session: AsyncSession, settings: Settings) -> dict[str, Any]:
    """Admin-chosen defaults (kept as data in the settings table), falling back to the environment."""
    row = (
        await session.execute(select(Setting).where(Setting.scope == "global", Setting.key == "asr"))
    ).scalar_one_or_none()
    v = row.value if row else {}
    return {"model": v.get("model") or settings.whisper_model, "language": v.get("language")}


async def save_asr_settings(
    session: AsyncSession, actor: User, model: str, language: str | None, ip: str | None
) -> dict[str, Any]:
    if model not in MODELS:
        raise AppError("invalid_model", f"Unknown speech model: {model}", 400)
    try:
        lang = normalize_language(language)
    except ValueError as exc:
        raise AppError("invalid_language", str(exc), 400) from exc
    row = (
        await session.execute(select(Setting).where(Setting.scope == "global", Setting.key == "asr").with_for_update())
    ).scalar_one_or_none()
    value = {"model": model, "language": lang}
    if row is None:
        session.add(Setting(scope="global", key="asr", value=value))
    else:
        row.value = value
    audit.record(session, "asr.settings_changed", actor_id=actor.id, details=value, ip=ip)
    await session.commit()
    return value


# ------------------------------------------------------------------------------------------------ storing rows
async def _next_version(session: AsyncSession, source_id: uuid.UUID) -> int:
    v = (await session.execute(select(func.max(Transcript.version)).where(Transcript.source_id == source_id))).scalar()
    return int(v or 0) + 1


async def _insert_segments(session: AsyncSession, transcript_id: uuid.UUID, segs: list[SegRow]) -> None:
    seg_rows: list[dict[str, Any]] = []
    word_rows: list[dict[str, Any]] = []
    for i, sg in enumerate(segs):
        sid = uuid7()
        seg_rows.append(
            {
                "id": sid,
                "transcript_id": transcript_id,
                "idx": i,
                "start_ms": sg.start_ms,
                "end_ms": sg.end_ms,
                "text": sg.text,
                "speaker_id": sg.speaker_id,
                "edited": sg.edited,
            }
        )
        for j, w in enumerate(sg.words):
            word_rows.append(
                {
                    "id": uuid7(),
                    "segment_id": sid,
                    "idx": j,
                    "start_ms": w.start_ms,
                    "end_ms": w.end_ms,
                    "text": w.text[:200],
                    "probability": w.probability,
                }
            )
    for chunk in _chunks(seg_rows, 2000):
        await session.execute(insert(TranscriptSegment), chunk)
    for chunk in _chunks(word_rows, 5000):
        await session.execute(insert(TranscriptWord), chunk)


async def _new_version(session: AsyncSession, source_id: uuid.UUID, segs: list[SegRow], **fields: Any) -> Transcript:
    await session.execute(
        update(Transcript).where(Transcript.source_id == source_id, Transcript.is_current).values(is_current=False)
    )
    t = Transcript(
        source_id=source_id,
        version=await _next_version(session, source_id),
        is_current=True,
        segment_count=len(segs),
        word_count=sum(len(s.words) for s in segs),
        **fields,
    )
    session.add(t)
    await session.flush()
    await _insert_segments(session, t.id, segs)
    return t


async def load_segments(
    session: AsyncSession,
    transcript_id: uuid.UUID,
    *,
    offset: int = 0,
    limit: int | None = None,
    with_words: bool = True,
) -> list[tuple[TranscriptSegment, list[TranscriptWord]]]:
    q = (
        select(TranscriptSegment)
        .where(TranscriptSegment.transcript_id == transcript_id)
        .order_by(TranscriptSegment.idx)
        .offset(offset)
    )
    if limit is not None:
        q = q.limit(limit)
    segs = list((await session.execute(q)).scalars())
    words: dict[uuid.UUID, list[TranscriptWord]] = {s.id: [] for s in segs}
    if with_words and segs:
        wq = (
            select(TranscriptWord)
            .where(TranscriptWord.segment_id.in_(list(words)))
            .order_by(TranscriptWord.segment_id, TranscriptWord.idx)
        )
        for w in (await session.execute(wq)).scalars():
            words[w.segment_id].append(w)
    return [(s, words[s.id]) for s in segs]


async def _rows_of(session: AsyncSession, transcript_id: uuid.UUID) -> list[SegRow]:
    return [
        SegRow(
            s.start_ms,
            s.end_ms,
            s.text,
            [W(w.text, w.start_ms, w.end_ms, w.probability) for w in ws],
            s.edited,
            s.speaker_id,
        )
        for s, ws in await load_segments(session, transcript_id)
    ]


async def persist_asr(
    session: AsyncSession, *, source: VideoSource, job: ProcessingJob, data: TranscriptData
) -> Transcript:
    """Called by the pipeline runner with a finished recognition result."""
    p = job.params or {}
    segs: list[SegRow] = []
    for sg in data.segments:
        words = tidy(
            [W(w.text, seconds_to_ms(w.start), seconds_to_ms(w.end), w.probability) for w in sg.words],
            seconds_to_ms(sg.start),
            max(seconds_to_ms(sg.end), seconds_to_ms(sg.words[-1].end)),
        )
        if words:
            segs.append(
                SegRow(
                    min(seconds_to_ms(sg.start), words[0].start_ms),
                    max(seconds_to_ms(sg.end), words[-1].end_ms),
                    sg.text,
                    words,
                )
            )
    return await _new_version(
        session,
        source.id,
        segs,
        kind="asr",
        engine=data.engine,
        model=data.model,
        language=data.language,
        language_probability=data.language_probability,
        requested_language=p.get("language"),
        cache_key=p.get("cache_key"),
        options={k: p.get(k) for k in ("vad", "loudnorm", "denoise")},
        duration_s=data.duration_s,
        created_by=job.requested_by,
        job_id=job.id,
    )


# ------------------------------------------------------------------------------------------------ request / cache
async def current(session: AsyncSession, source_id: uuid.UUID) -> Transcript | None:
    return (
        await session.execute(select(Transcript).where(Transcript.source_id == source_id, Transcript.is_current))
    ).scalar_one_or_none()


async def request_transcription(
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    user: User,
    source_id: uuid.UUID,
    *,
    language: str | None,
    model: str | None,
    force: bool,
    ip: str | None,
) -> Requested:
    src = await sources.get_source(session, user, source_id, Need.WRITE)
    if src.status != "ready":
        raise AppError("source_not_ready", "Wait until this video has finished processing before transcribing it.", 409)
    if src.has_audio is False:
        raise AppError("no_audio", "This file has no sound to transcribe.", 400)
    try:
        lang = normalize_language(language)
    except ValueError as exc:
        raise AppError("invalid_language", str(exc), 400) from exc
    defaults = await asr_settings(session, settings)
    chosen = model or defaults["model"]
    if chosen not in MODELS:
        raise AppError("invalid_model", f"Unknown speech model: {chosen}", 400)
    if lang is not None and not MODELS[chosen]["multilingual"] and lang != "en":
        raise AppError(
            "model_language_mismatch", f"The model '{chosen}' only understands English. Pick a multilingual model.", 400
        )
    if lang is None and not language and defaults["language"]:
        lang = defaults["language"]
    key = compute_cache_key(
        media_hash=src.media_hash or "",
        engine=ENGINE,
        model=chosen,
        language=lang,
        vad=settings.asr_vad,
        loudnorm=settings.asr_loudnorm,
        denoise=settings.asr_denoise,
    )

    if not force:
        cur = await current(session, src.id)
        if cur and cur.cache_key == key:
            return Requested(
                True, transcript=cur, reason="This video already has a transcript made with these settings."
            )
        if src.media_hash:  # the same file was transcribed before, possibly in another project
            donor = (
                await session.execute(
                    select(Transcript)
                    .where(Transcript.cache_key == key, Transcript.kind == "asr")
                    .order_by(Transcript.created_at)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if donor is not None:
                t = await _clone(session, donor, src.id, user.id)
                audit.record(
                    session,
                    "transcript.reused_from_cache",
                    actor_id=user.id,
                    target_type="source",
                    target_id=str(src.id),
                    details={"version": t.version},
                    ip=ip,
                )
                await session.commit()
                return Requested(
                    True, transcript=t, reason="An identical file was transcribed before; its result was reused."
                )

    if not await redis.exists(ASR_HEARTBEAT):
        raise AppError(
            "asr_unavailable", "The transcription service is not running. Start the 'worker-asr' service.", 503
        )
    if await _active_transcription(session, src.id):
        raise conflict("job_active", "This video is already being transcribed.")
    params: dict[str, Any] = {
        "model": chosen,
        "language": lang,
        "vad": settings.asr_vad,
        "loudnorm": settings.asr_loudnorm,
        "denoise": settings.asr_denoise,
        "cache_key": key,
    }
    if force:
        params["force_nonce"] = uuid.uuid4().hex  # a fresh parameter hash, so no earlier stage result is reused
    job = await jobs.create_job(session, src, user.id, kind=TRANSCRIBE, params=params)
    audit.record(
        session,
        "transcript.requested",
        actor_id=user.id,
        target_type="source",
        target_id=str(src.id),
        details={"model": chosen, "language": lang, "force": force},
        ip=ip,
    )
    await session.commit()
    jobs.dispatch(job.id, await jobs.pending_stages(session, job.id), src.kind)
    return Requested(False, job=job)


async def _active_transcription(session: AsyncSession, source_id: uuid.UUID) -> bool:
    q = (
        select(ProcessingJob.id)
        .where(
            ProcessingJob.source_id == source_id,
            ProcessingJob.kind == TRANSCRIBE,
            ProcessingJob.status.in_(("queued", "running")),
        )
        .limit(1)
    )
    return (await session.execute(q)).first() is not None


async def _clone(
    session: AsyncSession,
    donor: Transcript,
    source_id: uuid.UUID,
    user_id: uuid.UUID | None,
    *,
    kind: str | None = None,
    based_on: int | None = None,
) -> Transcript:
    return await _new_version(
        session,
        source_id,
        await _rows_of(session, donor.id),
        kind=kind or donor.kind,
        engine=donor.engine,
        model=donor.model,
        language=donor.language,
        language_probability=donor.language_probability,
        requested_language=donor.requested_language,
        cache_key=donor.cache_key if kind is None else None,
        options=donor.options,
        duration_s=donor.duration_s,
        based_on_version=based_on,
        cloned_from=donor.id,
        created_by=user_id,
    )


# ------------------------------------------------------------------------------------------------ reading
async def _version(session: AsyncSession, source_id: uuid.UUID, version: int | None) -> Transcript:
    q = select(Transcript).where(Transcript.source_id == source_id)
    q = q.where(Transcript.version == version) if version else q.where(Transcript.is_current)
    t = (await session.execute(q)).scalar_one_or_none()
    if t is None:
        raise not_found("Transcript")
    return t


async def read(
    session: AsyncSession,
    user: User,
    source_id: uuid.UUID,
    *,
    version: int | None,
    offset: int,
    limit: int,
    with_words: bool,
) -> tuple[Transcript, list[tuple[TranscriptSegment, list[TranscriptWord]]]]:
    await sources.get_source(session, user, source_id, Need.READ)
    t = await _version(session, source_id, version)
    return t, await load_segments(session, t.id, offset=offset, limit=limit, with_words=with_words)


async def versions(session: AsyncSession, user: User, source_id: uuid.UUID) -> list[Transcript]:
    await sources.get_source(session, user, source_id, Need.READ)
    return list(
        (
            await session.execute(
                select(Transcript).where(Transcript.source_id == source_id).order_by(Transcript.version.desc())
            )
        ).scalars()
    )


# ------------------------------------------------------------------------------------------------ editing
async def edit_segments(
    session: AsyncSession, user: User, source_id: uuid.UUID, *, base_version: int, edits: dict[int, str], ip: str | None
) -> Transcript:
    await sources.get_source(session, user, source_id, Need.WRITE)
    if not edits or len(edits) > MAX_EDITS:
        raise AppError("invalid_edits", f"Send between 1 and {MAX_EDITS} edits at a time.", 400)
    cur = (
        await session.execute(
            select(Transcript).where(Transcript.source_id == source_id, Transcript.is_current).with_for_update()
        )
    ).scalar_one_or_none()
    if cur is None:
        raise not_found("Transcript")
    if cur.version != base_version:  # someone else saved first: never overwrite silently
        raise AppError(
            "version_conflict",
            "The transcript changed since you opened it. Reload and try again.",
            409,
            details={"current_version": cur.version},
        )
    rows = await _rows_of(session, cur.id)
    for idx, text in edits.items():
        if idx < 0 or idx >= len(rows):
            raise AppError("invalid_edits", f"There is no segment {idx}.", 400)
        clean = " ".join(text.split())
        if not clean or len(clean) > MAX_SEGMENT_CHARS:
            raise AppError("invalid_edits", f"Segment {idx}: text must be 1 to {MAX_SEGMENT_CHARS} characters.", 400)
        r = rows[idx]
        if clean == r.text:
            continue
        r.words = align_edit(r.words, clean, r.start_ms, r.end_ms)
        r.text, r.edited = clean, True
    t = await _new_version(
        session,
        source_id,
        rows,
        kind="edit",
        engine=cur.engine,
        model=cur.model,
        language=cur.language,
        language_probability=cur.language_probability,
        requested_language=cur.requested_language,
        cache_key=None,
        options=cur.options,
        duration_s=cur.duration_s,
        based_on_version=cur.version,
        created_by=user.id,
    )
    audit.record(
        session,
        "transcript.edited",
        actor_id=user.id,
        target_type="source",
        target_id=str(source_id),
        details={"version": t.version, "segments": sorted(edits)},
        ip=ip,
    )
    await session.commit()
    return t


async def restore(session: AsyncSession, user: User, source_id: uuid.UUID, version: int, ip: str | None) -> Transcript:
    await sources.get_source(session, user, source_id, Need.WRITE)
    old = await _version(session, source_id, version)
    t = await _clone(session, old, source_id, user.id, kind="restore", based_on=old.version)
    audit.record(
        session,
        "transcript.restored",
        actor_id=user.id,
        target_type="source",
        target_id=str(source_id),
        details={"from": version, "to": t.version},
        ip=ip,
    )
    await session.commit()
    return t


# ------------------------------------------------------------------------------------------------ export
@dataclass
class Exported:
    body: str
    media_type: str
    extension: str
    notes: list[str] = field(default_factory=list)


async def export(session: AsyncSession, user: User, source_id: uuid.UUID, fmt: str, version: int | None) -> Exported:
    await sources.get_source(session, user, source_id, Need.READ)
    t = await _version(session, source_id, version)
    segs = await _rows_of(session, t.id)
    if fmt == "txt":
        return Exported("\n".join(s.text for s in segs) + "\n", "text/plain; charset=utf-8", "txt")
    cues = make_cues([s.words for s in segs])
    if fmt == "srt":
        return Exported(to_srt(cues), "application/x-subrip; charset=utf-8", "srt")
    if fmt == "vtt":
        return Exported(to_vtt(cues), "text/vtt; charset=utf-8", "vtt")
    raise AppError("invalid_format", "Format must be txt, srt or vtt.", 400)
