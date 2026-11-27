from __future__ import annotations

from app.config import Settings
from app.db.models import JobStage, ProcessingJob, VideoSource
from app.schemas.media import JobOut, SourceDetail, SourceOut, StageOut
from app.security.media_tokens import media_url


def source_out(src: VideoSource, settings: Settings) -> SourceOut:
    out = SourceOut.model_validate(src)
    if src.thumbnail_asset_id:
        out.thumbnail_url = media_url(src.thumbnail_asset_id, settings)
    return out


def job_out(job: ProcessingJob, stages: list[JobStage]) -> JobOut:
    return JobOut(
        id=job.id,
        project_id=job.project_id,
        source_id=job.source_id,
        kind=job.kind,
        status=job.status,
        error_code=job.error_code,
        error_message=job.error_message,
        started_at=job.started_at,
        finished_at=job.finished_at,
        stages=[StageOut.model_validate(s) for s in stages],
    )


def source_detail(
    src: VideoSource,
    settings: Settings,
    job: JobOut | None,
    transcription_job: JobOut | None = None,
    transcript: dict[str, object] | None = None,
    highlights_job: JobOut | None = None,
    highlights: dict[str, object] | None = None,
) -> SourceDetail:
    d = SourceDetail.model_validate(source_out(src, settings).model_dump())
    d.media_hash, d.job = src.media_hash, job
    d.transcription_job, d.transcript = transcription_job, transcript
    d.highlights_job, d.highlights = highlights_job, highlights
    if src.proxy_asset_id:
        d.proxy_url = media_url(src.proxy_asset_id, settings)
    if src.waveform_asset_id:
        d.waveform_url = media_url(src.waveform_asset_id, settings)
    if src.scenes_asset_id:
        d.scenes_url = media_url(src.scenes_asset_id, settings)
    return d
