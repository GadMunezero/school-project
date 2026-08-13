"""Async bridge for Celery tasks.

Separate from :mod:`worker.celery_app` on purpose: the Celery app autodiscovers task modules at
import time, and those modules need this helper. Keeping it here breaks what would otherwise be a
circular import between the app and its own tasks.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Coroutine
from typing import Any, TypeVar

from tradeloom.core.logging import get_logger

logger = get_logger("tradeloom.worker")

T = TypeVar("T")


def run_async(coro: Coroutine[Any, Any, T] | Awaitable[T]) -> T:
    """Run an async service call from a synchronous Celery task.

    Each call gets a fresh event loop and disposes the database engine afterwards: a SQLAlchemy
    async connection pool is bound to the loop that created it, so reusing one across loops
    produces intermittent "attached to a different loop" failures under load.
    """
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)  # type: ignore[arg-type]
    finally:
        try:
            from tradeloom.db.session import dispose_engine

            loop.run_until_complete(dispose_engine())
        except Exception:  # pragma: no cover - best-effort cleanup
            logger.warning("worker_engine_dispose_failed")
        asyncio.set_event_loop(None)
        loop.close()


__all__ = ["run_async"]
