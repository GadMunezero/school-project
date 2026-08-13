"""Exception handlers producing the single error envelope described in ``docs/API.md``."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from tradeloom.core.errors import AppError, ConflictError, InternalError, RateLimitedError
from tradeloom.core.logging import get_logger, request_id_ctx

logger = get_logger(__name__)

_STATUS_CODES = {
    400: "bad_request",
    401: "not_authenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "upstream_unavailable",
    503: "service_unavailable",
}


def _response(error: AppError, extra_headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content=error.to_payload(request_id_ctx.get()),
        headers=extra_headers,
    )


def _format_validation_errors(exc: RequestValidationError | PydanticValidationError) -> list[dict]:
    """Flatten pydantic errors into ``{field, code, message}`` the UI can attach to inputs."""
    formatted: list[dict] = []
    for error in exc.errors():
        location = [str(part) for part in error.get("loc", []) if part not in ("body", "query")]
        formatted.append(
            {
                "field": ".".join(location) or "_",
                "code": error.get("type", "invalid"),
                "message": error.get("msg", "Invalid value"),
            }
        )
    return formatted


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        if exc.log_context:
            logger.warning("app_error", code=exc.code, **exc.log_context)
        headers = None
        if isinstance(exc, RateLimitedError):
            headers = {"Retry-After": str(exc.retry_after_seconds)}
        return _response(exc, headers)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        error = AppError.__new__(AppError)
        AppError.__init__(
            error,
            "Some fields need attention.",
            details={"fields": _format_validation_errors(exc)},
        )
        error.status_code = 422
        error.code = "validation_error"
        return _response(error)

    @app.exception_handler(PydanticValidationError)
    async def _pydantic_validation(request: Request, exc: PydanticValidationError) -> JSONResponse:
        error = AppError.__new__(AppError)
        AppError.__init__(
            error,
            "Some fields need attention.",
            details={"fields": _format_validation_errors(exc)},
        )
        error.status_code = 422
        error.code = "validation_error"
        return _response(error)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        error = AppError.__new__(AppError)
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
        AppError.__init__(error, detail)
        error.status_code = exc.status_code
        error.code = _STATUS_CODES.get(exc.status_code, "error")
        return _response(error)

    @app.exception_handler(IntegrityError)
    async def _integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        # Constraint names leak schema details, so the user gets a generic message while the
        # actual constraint goes to the log for the on-call engineer.
        logger.warning("integrity_error", detail=str(exc.orig)[:300])
        return _response(ConflictError("That change conflicts with an existing record."))

    @app.exception_handler(SQLAlchemyError)
    async def _database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("database_error", path=request.url.path)
        return _response(InternalError())

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_exception", path=request.url.path, method=request.method)
        return _response(InternalError())


__all__ = ["register_exception_handlers"]
