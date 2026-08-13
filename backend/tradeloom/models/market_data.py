"""Market data sources and OHLCV candles.

Candles are stored one row per (source, instrument, timeframe, opened_at). ``opened_at`` is the
bar's *opening* timestamp in UTC — the convention used consistently by the engine, the charts and
the importers. A bar labelled 09:30 on a 5m series covers [09:30, 09:35).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from tradeloom.core.enums import Timeframe
from tradeloom.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from tradeloom.db.types import GUID, EnumType, JSONDict, TZDateTime, price_column, quantity_column


class MarketDataSource(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A registered provider of candles.

    ``is_realtime`` is only ever true for a provider that actually streams live prices. The UI
    reads this flag directly; nothing is labelled real-time on the strength of a hard-coded string.
    """

    __tablename__ = "market_data_sources"
    __table_args__ = (UniqueConstraint("key", name="uq_market_data_sources_key"),)

    key: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    provider_type: Mapped[str] = mapped_column(String(40), nullable=False, default="static")
    is_realtime: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Free-form provenance: generation parameters, vendor id, licence terms, last sync.
    metadata_json: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    last_synced_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)


class MarketData(Base, UUIDPrimaryKeyMixin):
    """One OHLCV bar.

    No ``updated_at``: candles are immutable. A correction replaces the row through the ingestion
    service, which records the change on the source instead.
    """

    __tablename__ = "market_data"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "instrument_id",
            "timeframe",
            "opened_at",
            name="uq_market_data_source_instrument_timeframe_open",
        ),
        Index("ix_market_data_lookup", "instrument_id", "timeframe", "opened_at"),
        CheckConstraint("high >= low", name="market_data_high_ge_low"),
        CheckConstraint("volume >= 0", name="market_data_volume_non_negative"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("market_data_sources.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False
    )
    timeframe: Mapped[Timeframe] = mapped_column(EnumType(Timeframe, 8), nullable=False)

    opened_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    open: Mapped[Decimal] = mapped_column(price_column(), nullable=False)
    high: Mapped[Decimal] = mapped_column(price_column(), nullable=False)
    low: Mapped[Decimal] = mapped_column(price_column(), nullable=False)
    close: Mapped[Decimal] = mapped_column(price_column(), nullable=False)
    volume: Mapped[Decimal] = mapped_column(quantity_column(), nullable=False, default=0)
    trade_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class MarketDataCoverage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Cached summary of what data exists, so the backtester can validate a requested range
    without scanning the candle table."""

    __tablename__ = "market_data_coverage"
    __table_args__ = (
        UniqueConstraint(
            "source_id", "instrument_id", "timeframe", name="uq_market_data_coverage_series"
        ),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("market_data_sources.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False
    )
    timeframe: Mapped[Timeframe] = mapped_column(EnumType(Timeframe, 8), nullable=False)

    first_bar_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    last_bar_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    bar_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    #: Findings from the last validation pass (gaps, duplicates, invalid OHLC).
    quality_report: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    validated_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)


__all__ = ["MarketData", "MarketDataCoverage", "MarketDataSource"]
