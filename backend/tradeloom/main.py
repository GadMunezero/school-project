"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from tradeloom import __version__
from tradeloom.api.errors import register_exception_handlers
from tradeloom.api.middleware import (
    RateLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from tradeloom.api.v1.router import api_router
from tradeloom.api.v1.routers import health
from tradeloom.core.config import get_settings
from tradeloom.core.logging import configure_logging, get_logger
from tradeloom.core.observability import init_sentry
from tradeloom.db.session import dispose_engine

logger = get_logger(__name__)

DESCRIPTION = """
Tradeloom API — trading journal, portfolio analytics, strategy management and deterministic
backtesting.

**Conventions**

* Every response is wrapped: `{"data": ...}` on success, `{"error": {...}}` on failure.
* Monetary values are JSON strings to preserve exact decimal precision.
* Authentication uses an HTTP-only session cookie plus an `X-CSRF-Token` header on unsafe methods.
* Long-running work (backtests, imports) returns a job id immediately; poll the job for progress.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    init_sentry("api")
    settings = get_settings()

    problems = settings.validate_for_production()
    if problems:
        # Refuse to serve production traffic with development defaults still in place.
        for problem in problems:
            logger.error("unsafe_production_config", problem=problem)
        raise RuntimeError(
            "Refusing to start in production with an unsafe configuration: " + "; ".join(problems)
        )

    logger.info(
        "application_startup",
        version=__version__,
        environment=settings.environment,
        database="postgresql" if not settings.is_sqlite else "sqlite",
    )
    try:
        yield
    finally:
        await dispose_engine()
        logger.info("application_shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()

    app = FastAPI(
        title="Tradeloom API",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        swagger_ui_parameters={"defaultModelsExpandDepth": -1},
    )

    # Middleware order matters: the outermost is listed last. Request context must wrap
    # everything so even a rate-limit rejection carries a request id.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID"],
        expose_headers=["X-Request-ID", "X-RateLimit-Remaining"],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(api_router, prefix="/api/v1")

    return app


app = create_app()

__all__ = ["app", "create_app"]
