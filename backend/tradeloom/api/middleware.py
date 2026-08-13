"""HTTP middleware: request correlation, structured access logs, security headers, rate limits."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from tradeloom.core.config import get_settings
from tradeloom.core.errors import RateLimitedError
from tradeloom.core.logging import get_logger, org_id_ctx, request_id_ctx, user_id_ctx
from tradeloom.core.ratelimit import RateLimit, get_rate_limiter

logger = get_logger("tradeloom.access")

REQUEST_ID_HEADER = "X-Request-ID"

#: Paths excluded from access logging to keep health-check noise out of the log stream.
_QUIET_PATHS = {"/health/live", "/health/ready", "/metrics"}


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, binds logging context, and emits one structured line per request."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        # Only accept a client-supplied id if it looks like one; otherwise it is log-injection.
        request_id = incoming if _is_safe_request_id(incoming) else uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        user_token = user_id_ctx.set(None)
        org_token = org_id_ctx.set(None)
        request.state.request_id = request_id

        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            if request.url.path not in _QUIET_PATHS:
                logger.info(
                    "http_request",
                    method=request.method,
                    path=request.url.path,
                    status=status_code,
                    duration_ms=duration_ms,
                    client=_client_ip(request),
                )
            request_id_ctx.reset(token)
            user_id_ctx.reset(user_token)
            org_id_ctx.reset(org_token)


def _is_safe_request_id(value: str) -> bool:
    return bool(value) and len(value) <= 64 and all(c.isalnum() or c in "-_" for c in value)


def _client_ip(request: Request) -> str | None:
    """Trust ``X-Forwarded-For`` only for its first entry, and only behind our own proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defence-in-depth headers.

    The API serves JSON, so a restrictive CSP costs nothing here; the frontend sets its own.
    HSTS is only sent when the deployment is actually HTTPS, since sending it over plain HTTP on
    localhost would poison the developer's browser for the whole domain.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        settings = get_settings()
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-site")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()"
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
        if settings.cookie_secure:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Global per-client limit. Sensitive endpoints add their own tighter limits as dependencies."""

    def __init__(self, app, *, exempt_paths: set[str] | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self.exempt_paths = exempt_paths or set(_QUIET_PATHS)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        settings = get_settings()
        if not settings.rate_limit_enabled or request.url.path in self.exempt_paths:
            return await call_next(request)

        limit = RateLimit.parse(settings.rate_limit_default)
        key = f"rl:global:{_client_ip(request) or 'unknown'}"
        result = await get_rate_limiter().hit(key, limit)
        if not result.allowed:
            error = RateLimitedError(result.retry_after_seconds)
            return JSONResponse(
                status_code=error.status_code,
                content=error.to_payload(request_id_ctx.get()),
                headers={"Retry-After": str(result.retry_after_seconds)},
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit.limit)
        response.headers["X-RateLimit-Remaining"] = str(result.remaining)
        return response


__all__ = [
    "REQUEST_ID_HEADER",
    "RateLimitMiddleware",
    "RequestContextMiddleware",
    "SecurityHeadersMiddleware",
]
