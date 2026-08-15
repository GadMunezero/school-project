"""Async engine and session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from tradeloom.core.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _engine_kwargs() -> dict[str, Any]:
    settings = get_settings()
    kwargs: dict[str, Any] = {"echo": settings.database_echo, "future": True}
    if settings.is_sqlite:
        # A shared in-memory/one-file SQLite database cannot use a real connection pool safely
        # across asyncio tasks; NullPool with aiosqlite's own serialisation is the correct pairing.
        kwargs["poolclass"] = NullPool
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_size"] = settings.database_pool_size
        kwargs["max_overflow"] = settings.database_max_overflow
        kwargs["pool_pre_ping"] = True
        kwargs["pool_recycle"] = 1800
    return kwargs


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(get_settings().database_url, **_engine_kwargs())
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
            class_=AsyncSession,
        )
    return _session_factory


def set_session_factory(factory: async_sessionmaker[AsyncSession] | None) -> None:
    """Used by the test harness to bind sessions to a transactional fixture."""
    global _session_factory
    _session_factory = factory


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Transactional scope for background jobs, CLI commands and seeders.

    Commits on success, rolls back on any exception, and always closes.
    """
    session = get_session_factory()()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency.

    The route handler runs inside the session but does *not* commit — services commit their own
    unit of work. Any exception escaping the handler rolls the transaction back.
    """
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


__all__ = [
    "dispose_engine",
    "get_db_session",
    "get_engine",
    "get_session_factory",
    "session_scope",
    "set_session_factory",
]
