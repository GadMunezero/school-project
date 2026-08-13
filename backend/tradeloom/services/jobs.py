"""Background job lifecycle.

``JobRecord`` is the durable record; Celery's result backend is treated as ephemeral. The UI and
the admin dashboard read this table, so job status survives a broker restart or a result-TTL
expiry.

Idempotency: a caller may supply an ``idempotency_key``. Enqueuing again with the same key
returns the existing job instead of starting duplicate work — which is what makes a retried
webhook or a double-clicked "Run backtest" button harmless.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core.enums import JobStatus
from tradeloom.core.errors import NotFoundError
from tradeloom.core.logging import get_logger
from tradeloom.core.timeutil import utcnow
from tradeloom.models.platform import JobRecord

logger = get_logger(__name__)

#: Queue routing by job kind. Long backtests must not block quick imports.
QUEUE_BY_KIND: dict[str, str] = {
    "backtest.run": "backtests",
    "import.validate": "imports",
    "import.commit": "imports",
    "analytics.snapshot": "analytics",
    "files.cleanup": "maintenance",
    "retention.purge": "maintenance",
    "account.recalculate": "default",
    "export.generate": "default",
}


class JobService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        kind: str,
        organization_id: uuid.UUID | None,
        requested_by_user_id: uuid.UUID | None = None,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        max_attempts: int = 3,
    ) -> JobRecord:
        if idempotency_key:
            existing = await self.by_idempotency_key(idempotency_key)
            if existing is not None:
                logger.info("job_deduplicated", kind=kind, job_id=str(existing.id))
                return existing

        record = JobRecord(
            organization_id=organization_id,
            requested_by_user_id=requested_by_user_id,
            kind=kind,
            status=JobStatus.QUEUED,
            queue=QUEUE_BY_KIND.get(kind, "default"),
            idempotency_key=idempotency_key,
            payload=payload or {},
            max_attempts=max_attempts,
            queued_at=utcnow(),
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def by_idempotency_key(self, key: str) -> JobRecord | None:
        result = await self.session.execute(
            select(JobRecord).where(JobRecord.idempotency_key == key)
        )
        return result.scalar_one_or_none()

    async def get(self, job_id: uuid.UUID) -> JobRecord:
        record = await self.session.get(JobRecord, job_id)
        if record is None:
            raise NotFoundError("Job not found.")
        return record

    async def get_for_organization(
        self, job_id: uuid.UUID, organization_id: uuid.UUID
    ) -> JobRecord:
        record = await self.get(job_id)
        if record.organization_id != organization_id:
            # Cross-tenant job ids are indistinguishable from missing ones.
            raise NotFoundError("Job not found.")
        return record

    async def attach_task(self, job_id: uuid.UUID, celery_task_id: str) -> None:
        record = await self.get(job_id)
        record.celery_task_id = celery_task_id
        await self.session.flush()

    async def mark_running(self, job_id: uuid.UUID) -> JobRecord:
        record = await self.get(job_id)
        record.status = JobStatus.RUNNING
        record.started_at = utcnow()
        record.attempts += 1
        await self.session.flush()
        return record

    async def update_progress(
        self, job_id: uuid.UUID, percent: int, message: str | None = None
    ) -> None:
        record = await self.get(job_id)
        record.progress_percent = max(0, min(100, int(percent)))
        if message:
            record.progress_message = message[:255]
        await self.session.flush()

    async def mark_completed(
        self, job_id: uuid.UUID, result: dict[str, Any] | None = None
    ) -> JobRecord:
        record = await self.get(job_id)
        record.status = JobStatus.COMPLETED
        record.finished_at = utcnow()
        record.progress_percent = 100
        record.result = result or {}
        if record.started_at:
            record.duration_ms = int(
                (record.finished_at - record.started_at).total_seconds() * 1000
            )
        await self.session.flush()
        return record

    async def mark_failed(
        self,
        job_id: uuid.UUID,
        *,
        error_code: str,
        user_message: str,
        detail: dict[str, Any] | None = None,
    ) -> JobRecord:
        """Record a failure.

        ``user_message`` is shown to the user and must not contain internal detail; ``detail``
        holds the diagnostics and is never serialised into an API response.
        """
        record = await self.get(job_id)
        record.status = JobStatus.FAILED
        record.finished_at = utcnow()
        record.error_code = error_code[:64]
        record.error_message = user_message[:500]
        record.error_detail = detail or {}
        if record.started_at:
            record.duration_ms = int(
                (record.finished_at - record.started_at).total_seconds() * 1000
            )
        await self.session.flush()
        logger.warning("job_failed", job_id=str(job_id), kind=record.kind, error_code=error_code)
        return record

    async def mark_cancelled(self, job_id: uuid.UUID) -> JobRecord:
        record = await self.get(job_id)
        record.status = JobStatus.CANCELLED
        record.finished_at = utcnow()
        await self.session.flush()
        return record

    async def can_retry(self, job_id: uuid.UUID) -> bool:
        record = await self.get(job_id)
        return record.attempts < record.max_attempts

    @staticmethod
    def to_dict(record: JobRecord) -> dict[str, Any]:
        """User-safe representation. ``error_detail`` is deliberately excluded."""
        return {
            "id": str(record.id),
            "kind": record.kind,
            "status": record.status.value,
            "queue": record.queue,
            "progress_percent": record.progress_percent,
            "progress_message": record.progress_message,
            "attempts": record.attempts,
            "max_attempts": record.max_attempts,
            "queued_at": record.queued_at.isoformat() if record.queued_at else None,
            "started_at": record.started_at.isoformat() if record.started_at else None,
            "finished_at": record.finished_at.isoformat() if record.finished_at else None,
            "duration_ms": record.duration_ms,
            "error_code": record.error_code,
            "error_message": record.error_message,
            "result": record.result,
        }


__all__ = ["QUEUE_BY_KIND", "JobService"]
