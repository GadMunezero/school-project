"""Import validation and commit tasks.

Both stages are available as background jobs for files too large to process inside a request.
They call exactly the same :class:`~tradeloom.services.imports.pipeline.ImportPipeline` methods
the synchronous endpoints use, so there is one implementation of the rules.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import shared_task

from tradeloom.core.enums import JobStatus, NotificationKind
from tradeloom.core.errors import AppError
from tradeloom.core.logging import get_logger
from tradeloom.db.session import session_scope
from tradeloom.services.files import FileService
from tradeloom.services.imports.pipeline import ImportPipeline
from tradeloom.services.jobs import JobService
from tradeloom.services.notifications import NotificationService
from worker.runtime import run_async

logger = get_logger(__name__)


@shared_task(name="tradeloom.import.validate", bind=True, max_retries=2, acks_late=True)
def validate_import_task(self: Any, import_id: str, job_id: str) -> dict[str, Any]:
    return run_async(_validate(uuid.UUID(import_id), uuid.UUID(job_id)))


async def _validate(import_id: uuid.UUID, job_id: uuid.UUID) -> dict[str, Any]:
    async with session_scope() as session:
        jobs = JobService(session)
        job = await jobs.get(job_id)
        if job.status.is_terminal:
            return {"skipped": True}
        await jobs.mark_running(job_id)

        try:
            pipeline = ImportPipeline(
                session, job.organization_id, actor_user_id=job.requested_by_user_id
            )
            record = await pipeline.get(import_id)
            if record.file_object_id is None:
                raise AppError("The uploaded file is no longer available.")

            data, _ = await FileService(session, job.organization_id).download(
                record.file_object_id
            )
            record = await pipeline.validate(import_id, data)
            await jobs.mark_completed(
                job_id,
                {
                    "valid": record.valid_rows,
                    "invalid": record.invalid_rows,
                    "duplicate": record.duplicate_rows,
                },
            )
            return {"status": JobStatus.COMPLETED.value, "valid_rows": record.valid_rows}
        except AppError as exc:
            await jobs.mark_failed(job_id, error_code=exc.code, user_message=exc.message, detail={})
            return {"status": JobStatus.FAILED.value, "error": exc.code}
        except Exception as exc:
            logger.exception("import_validate_failed", import_id=str(import_id))
            await jobs.mark_failed(
                job_id,
                error_code="import_error",
                user_message="The file could not be validated. The failure has been logged.",
                detail={"type": type(exc).__name__},
            )
            return {"status": JobStatus.FAILED.value, "error": "import_error"}


@shared_task(name="tradeloom.import.commit", bind=True, max_retries=1, acks_late=True)
def commit_import_task(self: Any, import_id: str, job_id: str) -> dict[str, Any]:
    return run_async(_commit(uuid.UUID(import_id), uuid.UUID(job_id)))


async def _commit(import_id: uuid.UUID, job_id: uuid.UUID) -> dict[str, Any]:
    async with session_scope() as session:
        jobs = JobService(session)
        job = await jobs.get(job_id)
        if job.status.is_terminal:
            return {"skipped": True}
        await jobs.mark_running(job_id)

        try:
            pipeline = ImportPipeline(
                session, job.organization_id, actor_user_id=job.requested_by_user_id
            )
            record = await pipeline.commit(import_id)
            await jobs.mark_completed(
                job_id,
                {"imported": record.imported_rows, "trades": record.created_trade_count},
            )

            if job.requested_by_user_id and job.organization_id:
                from tradeloom.repositories.trading import AccountRepository

                account = await AccountRepository(session, job.organization_id).get(
                    record.account_id
                )
                await NotificationService(session).notify(
                    organization_id=job.organization_id,
                    user_id=job.requested_by_user_id,
                    kind=NotificationKind.IMPORT_COMPLETED,
                    data={
                        "imported": record.imported_rows,
                        "total": record.total_rows,
                        "account_name": account.name if account else "your account",
                    },
                    link="/imports",
                )
            return {"status": JobStatus.COMPLETED.value, "imported": record.imported_rows}
        except AppError as exc:
            await jobs.mark_failed(job_id, error_code=exc.code, user_message=exc.message, detail={})
            await _notify_failure(session, job, exc.message)
            return {"status": JobStatus.FAILED.value, "error": exc.code}
        except Exception as exc:
            logger.exception("import_commit_failed", import_id=str(import_id))
            message = "The import could not be completed. No trades were created."
            await jobs.mark_failed(
                job_id,
                error_code="import_error",
                user_message=message,
                detail={"type": type(exc).__name__},
            )
            await _notify_failure(session, job, message)
            return {"status": JobStatus.FAILED.value, "error": "import_error"}


async def _notify_failure(session: Any, job: Any, reason: str) -> None:
    if not (job.requested_by_user_id and job.organization_id):
        return
    await NotificationService(session).notify(
        organization_id=job.organization_id,
        user_id=job.requested_by_user_id,
        kind=NotificationKind.IMPORT_FAILED,
        data={"filename": job.payload.get("filename", "your file"), "reason": reason},
        link="/imports",
    )


__all__ = ["commit_import_task", "validate_import_task"]
