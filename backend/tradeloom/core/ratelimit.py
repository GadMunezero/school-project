"""Fixed-window rate limiting.

Two backends behind one interface: Redis (shared across API processes) and an in-process
dictionary used when Redis is unavailable or in tests. The in-memory backend is explicitly *not*
a distributed limiter — it degrades to per-process limits, which is the correct failure mode for
a limiter (still limiting, just less strictly) rather than failing open entirely.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol

from tradeloom.core.logging import get_logger

logger = get_logger(__name__)

_RATE_PATTERN = re.compile(r"^(\d+)\s*/\s*(second|minute|hour|day)$", re.IGNORECASE)
_PERIOD_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


@dataclass(frozen=True, slots=True)
class RateLimit:
    limit: int
    window_seconds: int

    @classmethod
    def parse(cls, spec: str) -> RateLimit:
        match = _RATE_PATTERN.match(spec.strip())
        if not match:
            raise ValueError(f"invalid rate limit spec: {spec!r} (expected e.g. '10/minute')")
        return cls(
            limit=int(match.group(1)), window_seconds=_PERIOD_SECONDS[match.group(2).lower()]
        )


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    remaining: int
    retry_after_seconds: int


class RateLimiterBackend(Protocol):
    async def hit(self, key: str, limit: RateLimit) -> RateLimitResult: ...
    async def reset(self, key: str) -> None: ...


class InMemoryRateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, tuple[int, float]] = defaultdict(lambda: (0, 0.0))

    async def hit(self, key: str, limit: RateLimit) -> RateLimitResult:
        now = time.monotonic()
        count, window_start = self._buckets[key]
        if now - window_start >= limit.window_seconds:
            count, window_start = 0, now
        count += 1
        self._buckets[key] = (count, window_start)
        remaining = max(0, limit.limit - count)
        if count > limit.limit:
            retry_after = int(limit.window_seconds - (now - window_start)) + 1
            return RateLimitResult(False, 0, max(1, retry_after))
        return RateLimitResult(True, remaining, 0)

    async def reset(self, key: str) -> None:
        self._buckets.pop(key, None)


class RedisRateLimiter:
    """Fixed window with INCR + EXPIRE.

    A Redis outage must not take the API down, so errors fall back to the in-memory limiter.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client = None
        self._fallback = InMemoryRateLimiter()

    async def _get_client(self):  # type: ignore[no-untyped-def]
        if self._client is None:
            import redis.asyncio as redis_asyncio

            self._client = redis_asyncio.from_url(
                self._redis_url, encoding="utf-8", decode_responses=True
            )
        return self._client

    async def hit(self, key: str, limit: RateLimit) -> RateLimitResult:
        try:
            client = await self._get_client()
            pipe = client.pipeline()
            pipe.incr(key, 1)
            pipe.ttl(key)
            count, ttl = await pipe.execute()
            if ttl is None or ttl < 0:
                await client.expire(key, limit.window_seconds)
                ttl = limit.window_seconds
            if int(count) > limit.limit:
                return RateLimitResult(False, 0, max(1, int(ttl)))
            return RateLimitResult(True, max(0, limit.limit - int(count)), 0)
        except Exception as exc:  # pragma: no cover - exercised only on a Redis outage
            logger.warning("rate_limiter_fallback", error=type(exc).__name__)
            return await self._fallback.hit(key, limit)

    async def reset(self, key: str) -> None:
        try:
            client = await self._get_client()
            await client.delete(key)
        except Exception:  # pragma: no cover
            await self._fallback.reset(key)


_limiter: RateLimiterBackend | None = None


def get_rate_limiter() -> RateLimiterBackend:
    global _limiter
    if _limiter is None:
        from tradeloom.core.config import get_settings

        settings = get_settings()
        if settings.is_test or not settings.rate_limit_enabled:
            _limiter = InMemoryRateLimiter()
        else:
            _limiter = RedisRateLimiter(settings.redis_url)
    return _limiter


def set_rate_limiter(limiter: RateLimiterBackend | None) -> None:
    global _limiter
    _limiter = limiter


__all__ = [
    "InMemoryRateLimiter",
    "RateLimit",
    "RateLimitResult",
    "RateLimiterBackend",
    "RedisRateLimiter",
    "get_rate_limiter",
    "set_rate_limiter",
]
