"""Pagination primitives shared by every list endpoint.

Two modes:

* **Page/limit** for UI tables that need a total count and jump-to-page.
* **Cursor** for large or append-only collections (candles, audit logs, import rows) where
  ``OFFSET`` degrades badly. Cursors are opaque base64 of the sort key plus the row id, so they
  stay stable while rows are inserted.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 25


@dataclass(slots=True)
class PageParams:
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE
    sort_by: str | None = None
    sort_dir: str = "desc"

    def __post_init__(self) -> None:
        self.page = max(1, int(self.page))
        self.page_size = max(1, min(MAX_PAGE_SIZE, int(self.page_size)))
        self.sort_dir = "asc" if str(self.sort_dir).lower() == "asc" else "desc"

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


@dataclass(slots=True)
class Page(Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int

    @property
    def total_pages(self) -> int:
        if self.page_size <= 0:
            return 0
        return (self.total + self.page_size - 1) // self.page_size

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages

    def meta(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "page_size": self.page_size,
            "total": self.total,
            "total_pages": self.total_pages,
            "has_next": self.has_next,
        }


@dataclass(slots=True)
class CursorPage(Generic[T]):
    items: list[T]
    next_cursor: str | None = None
    has_more: bool = False

    def meta(self) -> dict[str, Any]:
        return {"next_cursor": self.next_cursor, "has_more": self.has_more}


@dataclass(slots=True)
class CursorParams:
    cursor: str | None = None
    limit: int = DEFAULT_PAGE_SIZE
    decoded: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.limit = max(1, min(MAX_PAGE_SIZE, int(self.limit)))
        self.decoded = decode_cursor(self.cursor) if self.cursor else {}


def encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    """Malformed cursors yield ``{}`` — a bad cursor restarts the list rather than 500ing."""
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + padding)
        decoded = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "CursorPage",
    "CursorParams",
    "Page",
    "PageParams",
    "decode_cursor",
    "encode_cursor",
]
