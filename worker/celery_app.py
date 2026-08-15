"""Celery application.

Configuration choices worth stating:

* ``task_acks_late`` with ``worker_prefetch_multiplier = 1`` — a backtest that dies mid-run is
  redelivered rather than lost, and a worker never hoards queued jobs it cannot start.
* ``task_reject_on_worker_lost`` — a killed worker's job goes back on the queue.
* Explicit queues per job kind, so a 20-minute backtest cannot delay a 2-second import.
* ``result_expires`` is short because :class:`~tradeloom.models.platform.JobRecord` is the durable
  record; the result backend is only used for liveness.
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.signals import setup_logging, task_failure, task_postrun, task_prerun

from tradeloom.core.config import get_settings
from tradeloom.core.logging import configure_logging, get_logger
from tradeloom.core.observability import init_sentry
from worker.runtime import run_async

settings = get_settings()
logger = get_logger("tradeloom.worker")

init_sentry("worker")

celery_app = Celery("tradeloom")

celery_app.conf.update(
    broker_url=settings.celery_broker_url,
    result_backend=settings.celery_result_backend,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=200,
    task_time_limit=settings.celery_task_time_limit,
    task_soft_time_limit=settings.celery_task_soft_time_limit,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
    task_default_queue="default",
    task_routes={
        "tradeloom.backtest.*": {"queue": "backtests"},
        "tradeloom.import.*": {"queue": "imports"},
        "tradeloom.analytics.*": {"queue": "analytics"},
        "tradeloom.maintenance.*": {"queue": "maintenance"},
    },
    beat_schedule={
        "refresh-account-snapshots": {
            "task": "tradeloom.analytics.refresh_snapshots",
            "schedule": 3600.0,
        },
        "cleanup-expired-files": {
            "task": "tradeloom.maintenance.cleanup_expired_files",
            "schedule": 86400.0,
        },
        "purge-expired-sessions": {
            "task": "tradeloom.maintenance.purge_expired_sessions",
            "schedule": 86400.0,
        },
        "process-deletion-requests": {
            "task": "tradeloom.maintenance.process_deletion_requests",
            "schedule": 86400.0,
        },
    },
)

# Tasks live in worker.tasks; autodiscovery keeps the import list in one place.
celery_app.autodiscover_tasks(["worker.tasks"], force=True)


@setup_logging.connect
def _configure_worker_logging(**_: Any) -> None:
    configure_logging(force=True)


@task_prerun.connect
def _log_start(task_id: str | None = None, task: Any = None, **_: Any) -> None:
    logger.info("task_started", task=getattr(task, "name", "?"), task_id=task_id)


@task_postrun.connect
def _log_finish(
    task_id: str | None = None, task: Any = None, state: str | None = None, **_: Any
) -> None:
    logger.info("task_finished", task=getattr(task, "name", "?"), task_id=task_id, state=state)


@task_failure.connect
def _log_failure(
    task_id: str | None = None, exception: BaseException | None = None, sender: Any = None, **_: Any
) -> None:
    # The exception type only — the message may carry user data.
    logger.error(
        "task_failed",
        task=getattr(sender, "name", "?"),
        task_id=task_id,
        error=type(exception).__name__ if exception else "unknown",
    )


__all__ = ["celery_app", "run_async"]
