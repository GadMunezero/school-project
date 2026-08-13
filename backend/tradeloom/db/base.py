"""Declarative base and shared model mixins."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from tradeloom.db.types import GUID, TZDateTime


def _utcnow() -> datetime:
    """Timezone-aware now. Defined here to keep ``db`` free of a ``core`` import cycle."""

    return datetime.now(tz=UTC)


#: Explicit naming convention so Alembic autogenerate produces stable, human-readable names and
#: constraint drops are reversible.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def to_dict(self, exclude: set[str] | None = None) -> dict[str, Any]:
        excluded = exclude or set()
        return {
            column.name: getattr(self, column.name)
            for column in self.__table__.columns
            if column.name not in excluded
        }

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        identifier = getattr(self, "id", None)
        return f"<{type(self).__name__} id={identifier}>"


class UUIDPrimaryKeyMixin:
    """UUIDv4 primary key generated in Python.

    Client-side generation lets services build object graphs (an import with its rows, a backtest
    with its trades) before a flush, and removes a round trip per insert.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        GUID(), primary_key=True, default=uuid.uuid4, nullable=False
    )


class TimestampMixin:
    """Creation and update timestamps.

    Both a Python-side ``default`` and a ``server_default`` are declared, deliberately:

    * the **server default** covers rows written by SQL that bypasses the ORM (migrations, bulk
      loads, psql);
    * the **Python default** means the ORM knows the value immediately after an insert. Without
      it, reading ``obj.created_at`` right after a commit triggers a lazy refresh — which under
      asyncio raises ``MissingGreenlet`` rather than quietly doing IO.

    ``onupdate`` uses the Python callable for the same reason.
    """

    created_at: Mapped[datetime] = mapped_column(
        TZDateTime(),
        nullable=False,
        default=_utcnow,
        server_default=func.now(),
        index=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime(),
        nullable=False,
        default=_utcnow,
        onupdate=_utcnow,
        server_default=func.now(),
    )


class SoftDeleteMixin:
    """Soft deletion for entities a user may want to recover, and for entities referenced by
    historical records (deleting a strategy must not orphan the trades that used it)."""

    deleted_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True, index=True)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


__all__ = [
    "NAMING_CONVENTION",
    "Base",
    "SoftDeleteMixin",
    "TimestampMixin",
    "UUIDPrimaryKeyMixin",
]
