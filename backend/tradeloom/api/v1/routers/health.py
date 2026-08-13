"""Liveness and readiness probes.

*Liveness* answers "is this process running" and never touches a dependency — a database blip
must not cause the orchestrator to kill healthy API pods.

*Readiness* answers "should this instance receive traffic" and does check the database and Redis.
Neither endpoint reveals versions of internal components, connection strings, or error detail.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from tradeloom import __version__
from tradeloom.core.config import get_settings
from tradeloom.core.logging import get_logger
from tradeloom.core.timeutil import utcnow
from tradeloom.db.session import get_engine
from tradeloom.schemas.common import HealthStatus

logger = get_logger(__name__)
router = APIRouter(tags=["health"])

_CHECK_TIMEOUT_SECONDS = 3.0


@router.get("/health/live", response_model=HealthStatus, summary="Liveness probe")
async def liveness() -> HealthStatus:
    settings = get_settings()
    return HealthStatus(
        status="ok",
        version=__version__,
        environment=settings.environment,
        checks={},
        timestamp=utcnow(),
    )


async def _check_database() -> tuple[bool, str]:
    try:
        async with asyncio.timeout(_CHECK_TIMEOUT_SECONDS):
            engine = get_engine()
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        return True, "ok"
    except TimeoutError:
        return False, "timeout"
    except Exception as exc:
        logger.warning("readiness_database_failed", error=type(exc).__name__)
        return False, "unavailable"


async def _check_redis() -> tuple[bool, str]:
    settings = get_settings()
    if settings.is_test:
        return True, "skipped"
    try:
        import redis.asyncio as redis_asyncio

        async with asyncio.timeout(_CHECK_TIMEOUT_SECONDS):
            client = redis_asyncio.from_url(settings.redis_url)
            try:
                await client.ping()
            finally:
                await client.aclose()
        return True, "ok"
    except TimeoutError:
        return False, "timeout"
    except Exception as exc:
        logger.warning("readiness_redis_failed", error=type(exc).__name__)
        return False, "unavailable"


@router.get("/health/ready", response_model=HealthStatus, summary="Readiness probe")
async def readiness(response: Response) -> HealthStatus:
    settings = get_settings()
    db_ok, db_detail = await _check_database()
    redis_ok, redis_detail = await _check_redis()

    checks: dict[str, Any] = {
        "database": {"ok": db_ok, "detail": db_detail},
        "redis": {"ok": redis_ok, "detail": redis_detail},
    }
    # Redis backs rate limiting and queues; without it the API can still serve reads, so it is
    # reported as degraded rather than failing readiness outright.
    ready = db_ok
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return HealthStatus(
        status="ok" if ready and redis_ok else ("degraded" if ready else "unavailable"),
        version=__version__,
        environment=settings.environment,
        checks=checks,
        timestamp=utcnow(),
    )


__all__ = ["router"]
