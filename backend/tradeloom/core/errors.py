"""Application error taxonomy.

Every failure the API returns is an :class:`AppError`. The HTTP layer turns it into the standard
envelope described in ``docs/API.md``::

    {"error": {"code": "not_found", "message": "...", "details": {...}, "request_id": "..."}}

Two rules that the tests enforce:

* ``message`` is safe to show a user. Internal detail goes in ``log_context`` which is logged but
  never serialised into a response.
* Cross-tenant access returns :class:`NotFoundError`, not :class:`ForbiddenError` — telling a
  caller that a resource "exists but is not yours" is itself a leak.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for every expected error condition."""

    status_code: int = 400
    code: str = "bad_request"
    default_message: str = "The request could not be processed."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
        log_context: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = details or {}
        self.log_context = log_context or {}
        super().__init__(self.message)

    def to_payload(self, request_id: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        if request_id:
            payload["request_id"] = request_id
        return {"error": payload}


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"
    default_message = "The submitted data is invalid."


class AuthenticationError(AppError):
    status_code = 401
    code = "not_authenticated"
    default_message = "Authentication is required."


class InvalidCredentialsError(AuthenticationError):
    code = "invalid_credentials"
    default_message = "Email or password is incorrect."


class SessionExpiredError(AuthenticationError):
    code = "session_expired"
    default_message = "Your session has expired. Please sign in again."


class EmailNotVerifiedError(AppError):
    status_code = 403
    code = "email_not_verified"
    default_message = "Verify your email address to continue."


class ForbiddenError(AppError):
    status_code = 403
    code = "forbidden"
    default_message = "You do not have permission to perform this action."


class CsrfError(AppError):
    status_code = 403
    code = "csrf_failed"
    default_message = "The request could not be verified. Refresh the page and try again."


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    default_message = "The requested resource does not exist."


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    default_message = "The request conflicts with the current state of the resource."


class RateLimitedError(AppError):
    status_code = 429
    code = "rate_limited"
    default_message = "Too many requests. Please slow down."

    def __init__(self, retry_after_seconds: int, message: str | None = None) -> None:
        super().__init__(message, details={"retry_after_seconds": retry_after_seconds})
        self.retry_after_seconds = retry_after_seconds


class EntitlementError(AppError):
    status_code = 402
    code = "plan_limit_reached"
    default_message = "Your plan does not include this feature."

    def __init__(
        self,
        message: str | None = None,
        *,
        feature: str | None = None,
        limit: int | None = None,
        current: int | None = None,
        required_plan: str | None = None,
    ) -> None:
        details: dict[str, Any] = {}
        if feature:
            details["feature"] = feature
        if limit is not None:
            details["limit"] = limit
        if current is not None:
            details["current"] = current
        if required_plan:
            details["required_plan"] = required_plan
        super().__init__(message, details=details)


class UnprocessableStateError(AppError):
    status_code = 409
    code = "invalid_state"
    default_message = "The resource is not in a state that allows this operation."


class ExternalServiceError(AppError):
    status_code = 502
    code = "upstream_unavailable"
    default_message = "An upstream service is unavailable. Please try again shortly."


class StorageUnavailableError(ExternalServiceError):
    code = "storage_unavailable"
    default_message = "File storage is unavailable. Please try again shortly."


class InternalError(AppError):
    status_code = 500
    code = "internal_error"
    default_message = "Something went wrong on our side. The incident has been logged."


__all__ = [
    "AppError",
    "AuthenticationError",
    "ConflictError",
    "CsrfError",
    "EmailNotVerifiedError",
    "EntitlementError",
    "ExternalServiceError",
    "ForbiddenError",
    "InternalError",
    "InvalidCredentialsError",
    "NotFoundError",
    "RateLimitedError",
    "SessionExpiredError",
    "StorageUnavailableError",
    "UnprocessableStateError",
    "ValidationError",
]
