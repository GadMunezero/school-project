"""Shared response shapes.

Every endpoint returns one of three envelopes (see ``docs/API.md``)::

    {"data": {...}}                      single resource
    {"data": [...], "meta": {...}}       collection
    {"error": {"code": ..., ...}}        failure

Decimals serialise as JSON *strings*. A float round trip silently loses precision on values like
``0.1``, and the frontend must never re-derive money from a lossy number — it displays what the
backend computed.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from tradeloom.core.timeutil import iso

T = TypeVar("T")


class TradeloomModel(BaseModel):
    """Base for every schema: strict-ish parsing, ORM friendly, Decimal-safe serialisation."""

    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        str_strip_whitespace=True,
        extra="forbid",
        ser_json_timedelta="float",
    )

    @field_serializer("*", when_used="json", check_fields=False)
    def _serialise(self, value: Any) -> Any:
        """Serialise the types the API is opinionated about.

        The wildcard claims *every* field, which means Pydantic's own encoders no longer run for
        any of them. Decimals were handled here from the start; datetimes were not, so they fell
        through to the generic encoder and went out as Unix timestamps. Most routers had quietly
        worked around it by calling ``isoformat()`` themselves, which hid the problem everywhere
        except the schemas that did not — a backtest's drawdown episodes reached the browser as
        integers, and the results page crashed trying to read a date out of one.
        """
        if isinstance(value, Decimal):
            return format(value.normalize(), "f")
        if isinstance(value, datetime):
            return iso(value)
        return value


class PageMeta(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int
    has_next: bool


class CursorMeta(BaseModel):
    next_cursor: str | None = None
    has_more: bool = False


class DataResponse(BaseModel, Generic[T]):
    data: T


class ListResponse(BaseModel, Generic[T]):
    data: list[T]
    meta: PageMeta


class CursorListResponse(BaseModel, Generic[T]):
    data: list[T]
    meta: CursorMeta


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class MessageResponse(BaseModel):
    """For operations whose only meaningful result is 'it worked'."""

    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class BulkResult(BaseModel):
    requested: int
    succeeded: int
    failed: int = 0
    #: Per-id failure reasons, so a partial bulk operation is explainable rather than mysterious.
    errors: list[dict[str, Any]] = Field(default_factory=list)


class SortParams(BaseModel):
    sort_by: str | None = None
    sort_dir: str = "desc"


class TimeRange(BaseModel):
    start: datetime | None = None
    end: datetime | None = None


class HealthStatus(BaseModel):
    status: str
    version: str
    environment: str
    checks: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime


__all__ = [
    "BulkResult",
    "CursorListResponse",
    "CursorMeta",
    "DataResponse",
    "ErrorDetail",
    "ErrorResponse",
    "HealthStatus",
    "ListResponse",
    "MessageResponse",
    "PageMeta",
    "SortParams",
    "TimeRange",
    "TradeloomModel",
]
