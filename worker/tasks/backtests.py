"""Backtest execution task.

Idempotent by design: a run already in a terminal state is skipped rather than re-executed, so a
redelivered message after a worker crash cannot double-write results.

Progress is written to the job record (and mirrored onto the run) at intervals rather than every
bar — the UI polls at human speed and per-bar writes would dominate the run's cost.
"""

from __future__ import annotations

import uuid
from typing import Any

from celery import shared_task

from tradeloom.core.enums import JobStatus, NotificationKind
from tradeloom.core.errors import AppError
from tradeloom.core.logging import get_logger
from tradeloom.db.session import session_scope
from tradeloom.services.backtests import BacktestService
from tradeloom.services.jobs import JobService
from tradeloom.services.notifications import NotificationService
from worker.runtime import run_async

logger = get_logger(__name__)

#: Write progress at most this often (as a percentage step).
PROGRESS_STEP = 5


@shared_task(
    name="tradeloom.backtest.run",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def run_backtest_task(self: Any, run_id: str, job_id: str) -> dict[str, Any]:
    return run_async(_execute(uuid.UUID(run_id), uuid.UUID(job_id)))


async def _execute(run_id: uuid.UUID, job_id: uuid.UUID) -> dict[str, Any]:
    async with session_scope() as session:
        jobs = JobService(session)
        job = await jobs.get(job_id)

        if job.status.is_terminal:
            logger.info("backtest_job_already_finished", job_id=str(job_id))
            return {"status": job.status.value, "skipped": True}

        service = BacktestService(
            session,
            job.organization_id,  # type: ignore[arg-type]
            actor_user_id=job.requested_by_user_id,
        )
        run = await service.get_run(run_id)
        if run.status.is_terminal:
            return {"status": run.status.value, "skipped": True}

        await jobs.mark_running(job_id)
        await session.commit()

    last_reported = {"percent": -1}

    def progress(current: int, total: int) -> None:
        if total <= 0:
            return
        percent = int(current * 100 / total)
        if percent - last_reported["percent"] < PROGRESS_STEP:
            return
        last_reported["percent"] = percent
        try:
            run_async(_write_progress(job_id, run_id, percent))
        except Exception:  # pragma: no cover - progress must never fail the run
            logger.warning("backtest_progress_write_failed", job_id=str(job_id))

    try:
        async with session_scope() as session:
            job = await JobService(session).get(job_id)
            service = BacktestService(session, job.organization_id)  # type: ignore[arg-type]
            run = await service.execute(run_id, progress=None)

            metrics = run.metrics or {}
            await JobService(session).mark_completed(
                job_id,
                {
                    "run_id": str(run_id),
                    "trades": run.trade_count,
                    "bars": run.bars_processed,
                    "duration_ms": run.duration_ms,
                },
            )

            if job.requested_by_user_id and job.organization_id:
                backtest = await service.get(run.backtest_id)
                await NotificationService(session).notify(
                    organization_id=job.organization_id,
                    user_id=job.requested_by_user_id,
                    kind=NotificationKind.BACKTEST_COMPLETED,
                    data={
                        "backtest_name": backtest.name,
                        "trade_count": run.trade_count,
                        "total_return": f"{metrics.get('total_return_percent', '0')}%",
                    },
                    link=f"/backtester/{run.backtest_id}?run={run_id}",
                )
            return {"status": JobStatus.COMPLETED.value, "run_id": str(run_id)}

    except AppError as exc:
        await _fail(
            job_id, run_id, code=exc.code, message=exc.message, detail={"type": type(exc).__name__}
        )
        return {"status": JobStatus.FAILED.value, "error": exc.code}
    except Exception as exc:
        logger.exception("backtest_task_failed", run_id=str(run_id))
        await _fail(
            job_id,
            run_id,
            code="engine_error",
            # Deliberately generic: an internal message could leak paths or data.
            message="The backtest could not be completed. The failure has been logged.",
            detail={"type": type(exc).__name__},
        )
        return {"status": JobStatus.FAILED.value, "error": "engine_error"}


async def _write_progress(job_id: uuid.UUID, run_id: uuid.UUID, percent: int) -> None:
    async with session_scope() as session:
        await JobService(session).update_progress(job_id, percent, f"Processing bars… {percent}%")
        service = BacktestService(session, (await JobService(session).get(job_id)).organization_id)  # type: ignore[arg-type]
        run = await service.get_run(run_id)
        run.progress_percent = percent


async def _fail(
    job_id: uuid.UUID, run_id: uuid.UUID, *, code: str, message: str, detail: dict[str, Any]
) -> None:
    async with session_scope() as session:
        jobs = JobService(session)
        job = await jobs.get(job_id)
        await jobs.mark_failed(job_id, error_code=code, user_message=message, detail=detail)

        if job.organization_id:
            service = BacktestService(session, job.organization_id)
            run = await service.mark_failed(run_id, code=code, message=message)
            if job.requested_by_user_id and run is not None:
                backtest = await service.get(run.backtest_id)
                await NotificationService(session).notify(
                    organization_id=job.organization_id,
                    user_id=job.requested_by_user_id,
                    kind=NotificationKind.BACKTEST_FAILED,
                    data={"backtest_name": backtest.name, "reason": message},
                    link=f"/backtester/{run.backtest_id}",
                )


__all__ = ["run_backtest_task"]
