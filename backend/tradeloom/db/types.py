"""Portable column types.

Production runs on PostgreSQL. The test suite runs on SQLite so that `pytest` needs no external
services, which means the handful of PG-specific types we rely on need portable fallbacks. Each
type below emits the native PG type when the dialect is `postgresql` and a faithful equivalent
otherwise — the *semantics* (UUID identity, aware datetimes, exact decimals) are identical on both.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CHAR, DateTime, Dialect, Numeric, String, Text, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.types import JSON

from tradeloom.core import money as money_utils


class GUID(TypeDecorator[uuid.UUID]):
    """UUID primary/foreign key. Native ``uuid`` on PostgreSQL, 36-char string elsewhere."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        return value if dialect.name == "postgresql" else str(value)

    def process_result_value(self, value: Any, dialect: Dialect) -> uuid.UUID | None:
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class JSONDict(TypeDecorator[dict]):
    """``JSONB`` on PostgreSQL (indexable), plain ``JSON`` elsewhere."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class TZDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetime.

    SQLite discards tzinfo, which would silently turn aware datetimes into naive ones halfway
    through a round trip. This type normalises to UTC on the way in and re-attaches UTC on the way
    out, so application code only ever sees aware datetimes on either backend.
    """

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"expected datetime, got {type(value).__name__}")
        if value.tzinfo is None:
            raise ValueError("naive datetimes are not accepted; attach a timezone first")
        return value.astimezone(UTC)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class EnumType(TypeDecorator):
    """A ``StrEnum`` stored as VARCHAR and read back as the enum member.

    Declaring a column as plain ``String`` while annotating it ``Mapped[SomeEnum]`` type-checks
    but lies at runtime: writes succeed (``StrEnum`` is a ``str``) while reads return ``str``, so
    ``row.status.value`` blows up far from the cause. This decorator makes the annotation true.

    The emitted DDL is still ``VARCHAR(n)`` — deliberately, rather than a native PostgreSQL
    ``ENUM``. Adding a value to a native enum requires ``ALTER TYPE`` and locks; a VARCHAR column
    plus a validated Python enum gives the same safety with none of the migration pain.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_class: type, length: int = 32) -> None:
        self.enum_class = enum_class
        self.length = length
        super().__init__(length=length)

    def __repr__(self) -> str:
        return f"EnumType({self.enum_class.__name__}, length={self.length})"

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        if isinstance(value, self.enum_class):
            return value.value
        # Accept the raw string too, but validate it so a typo cannot reach the database.
        return self.enum_class(value).value

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        try:
            return self.enum_class(value)
        except ValueError:
            # A value written by an older release that no longer exists in the enum. Returning
            # the raw string keeps the row readable instead of failing the whole query.
            return value


class DecimalType(TypeDecorator[Decimal]):
    """Exact numeric with a fixed scale.

    SQLite has no native NUMERIC, so values are stored as text and parsed back into ``Decimal``.
    That keeps the test suite exact rather than approximately-exact, which matters because the
    financial-math tests assert on precise values.
    """

    impl = Numeric
    cache_ok = True

    def __init__(self, precision: int = 28, scale: int = 10) -> None:
        self.precision = precision
        self.scale = scale
        super().__init__()

    def __repr__(self) -> str:
        # Alembic renders user-defined types with repr(); without this the generated migration
        # would lose the precision/scale arguments.
        return f"DecimalType(precision={self.precision}, scale={self.scale})"

    def load_dialect_impl(self, dialect: Dialect) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Numeric(self.precision, self.scale, asdecimal=True))
        return dialect.type_descriptor(String(48))

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        decimal_value = money_utils.to_decimal(value)
        quantised = decimal_value.quantize(
            Decimal(1).scaleb(-self.scale), rounding=money_utils.ROUNDING
        )
        if dialect.name == "postgresql":
            return quantised
        # Zero-padded fixed-width text keeps SQLite's lexicographic ORDER BY numerically correct
        # for non-negative values, and negatives sort into their own contiguous block.
        return format(quantised, f"0{self.precision + 2}.{self.scale}f")

    def process_result_value(self, value: Any, dialect: Dialect) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, Decimal):
            return value
        return money_utils.to_decimal(value)


#: Canonical column shapes. Using these keeps precision consistent across 40+ tables.
def money_column() -> DecimalType:
    """Monetary amounts: P&L, fees, balances. NUMERIC(28,10)."""
    return DecimalType(28, 10)


def price_column() -> DecimalType:
    """Instrument prices. NUMERIC(28,10) — enough for JPY crosses and satoshi-level crypto."""
    return DecimalType(28, 10)


def quantity_column() -> DecimalType:
    """Share/contract/lot quantities. NUMERIC(28,10) to support fractional crypto sizing."""
    return DecimalType(28, 10)


def percent_column() -> DecimalType:
    """Percentages stored as whole numbers (12.5 == 12.5%). NUMERIC(18,8)."""
    return DecimalType(18, 8)


def ratio_column() -> DecimalType:
    """Unitless ratios: R multiple, profit factor, Sharpe. NUMERIC(18,8)."""
    return DecimalType(18, 8)


ShortText = String(120)
MediumText = String(255)
LongText = Text

__all__ = [
    "GUID",
    "DecimalType",
    "JSONDict",
    "LongText",
    "MediumText",
    "ShortText",
    "TZDateTime",
    "money_column",
    "percent_column",
    "price_column",
    "quantity_column",
    "ratio_column",
]
