"""One Celery task per pipeline stage (SPEC §6.C). Tasks re-read all state from the DB."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from app.pipeline.registry import STAGES
from app.pipeline.runner import execute_stage
from app.tasks.celery_app import celery_app


def _register(stage: str) -> Any:
    @celery_app.task(name=f"pipeline.{stage}", acks_late=True)
    def _task(job_id: str) -> str:
        asyncio.run(execute_stage(uuid.UUID(job_id), stage))
        return job_id  # passed to the next link of the chain

    return _task


TASKS = {name: _register(name) for name in STAGES}
