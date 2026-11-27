from __future__ import annotations

import uuid

from celery import chain

from app.tasks.celery_app import celery_app


def dispatch_job(job_id: uuid.UUID, stages: list[tuple[str, str]]) -> None:
    """Enqueue `stages` ([(stage, queue)]) as a chain. Each link is a separate task on its own queue."""
    if not stages:
        return
    sigs = []
    for i, (stage, queue) in enumerate(stages):
        sig = celery_app.signature(f"pipeline.{stage}", args=(str(job_id),) if i == 0 else (), queue=queue)
        sigs.append(sig)
    chain(*sigs).apply_async()
