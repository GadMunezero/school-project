"""Structured logging with request correlation and secret redaction."""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar
from typing import Any

import structlog

from tradeloom.core.config import get_settings

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_ctx: ContextVar[str | None] = ContextVar("user_id", default=None)
org_id_ctx: ContextVar[str | None] = ContextVar("org_id", default=None)

#: Keys that must never reach a log sink, regardless of where they appear in the event dict.
REDACTED_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "password_hash",
        "current_password",
        "new_password",
        "token",
        "session_token",
        "refresh_token",
        "access_token",
        "csrf_token",
        "secret",
        "secret_key",
        "client_secret",
        "api_key",
        "authorization",
        "cookie",
        "set-cookie",
        "stripe_secret_key",
        "stripe_webhook_secret",
        "signed_url",
        "presigned_url",
        "s3_secret_access_key",
        "card",
        "cvc",
    }
)

REDACTION_PLACEHOLDER = "[redacted]"


def _redact(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for key in list(event_dict):
        if key.lower() in REDACTED_KEYS:
            event_dict[key] = REDACTION_PLACEHOLDER
        elif isinstance(event_dict[key], dict):
            event_dict[key] = {
                k: (REDACTION_PLACEHOLDER if k.lower() in REDACTED_KEYS else v)
                for k, v in event_dict[key].items()
            }
    return event_dict


def _add_context(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    if (request_id := request_id_ctx.get()) is not None:
        event_dict.setdefault("request_id", request_id)
    if (user_id := user_id_ctx.get()) is not None:
        event_dict.setdefault("user_id", user_id)
    if (org_id := org_id_ctx.get()) is not None:
        event_dict.setdefault("organization_id", org_id)
    return event_dict


_configured = False


def configure_logging(force: bool = False) -> None:
    global _configured
    if _configured and not force:
        return

    settings = get_settings()
    level = getattr(logging, settings.log_level, logging.INFO)

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level, force=True)
    for noisy in ("uvicorn.access", "botocore", "boto3", "urllib3", "s3transfer"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    renderer: Any
    if settings.log_format == "console":
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stdout.isatty())
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_context,
            _redact,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    configure_logging()
    return structlog.get_logger(name)  # type: ignore[no-any-return]


__all__ = [
    "REDACTED_KEYS",
    "configure_logging",
    "get_logger",
    "org_id_ctx",
    "request_id_ctx",
    "user_id_ctx",
]
