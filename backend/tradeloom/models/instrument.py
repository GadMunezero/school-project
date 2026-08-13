"""Instrument catalogue and symbol aliasing.

Instruments are global (shared across tenants) when ``organization_id`` is NULL, and
tenant-private when it is set — a user importing an exotic broker symbol gets a private
instrument rather than polluting the shared catalogue.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tradeloom.core.enums import AssetType
from tradeloom.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from tradeloom.db.types import GUID, EnumType, JSONDict, price_column, quantity_column


class Instrument(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "symbol", "asset_type", name="uq_instruments_org_symbol_asset"
        ),
        Index("ix_instruments_symbol", "symbol"),
        CheckConstraint("tick_size > 0", name="instruments_tick_size_positive"),
        CheckConstraint("contract_multiplier > 0", name="instruments_multiplier_positive"),
    )

    #: NULL means the instrument is part of the shared catalogue visible to every tenant.
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )

    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    asset_type: Mapped[AssetType] = mapped_column(EnumType(AssetType, 20), nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(40), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")

    #: Minimum price increment. Slippage models expressed in ticks multiply by this.
    tick_size: Mapped[Decimal] = mapped_column(
        price_column(), nullable=False, default=Decimal("0.01")
    )
    #: Cash value of one point of price movement for one unit (futures/options/CFD multiplier).
    contract_multiplier: Mapped[Decimal] = mapped_column(
        quantity_column(), nullable=False, default=Decimal(1)
    )
    #: Smallest tradable quantity increment (1 for shares, 0.00000001 for BTC).
    lot_size: Mapped[Decimal] = mapped_column(quantity_column(), nullable=False, default=Decimal(1))
    price_precision: Mapped[int] = mapped_column(nullable=False, default=2)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Futures/options only.
    expires_on: Mapped[date | None] = mapped_column(nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)

    @property
    def is_global(self) -> bool:
        return self.organization_id is None


class InstrumentAlias(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Maps broker-specific symbols onto a canonical instrument.

    Example: ``ES SEP24``, ``ESU4`` and ``/ESU24`` all resolve to the same futures instrument.
    Aliases are matched case-insensitively on the normalised (uppercased, punctuation-stripped)
    form stored in ``alias_normalized``.
    """

    __tablename__ = "instrument_aliases"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "alias_normalized", "source", name="uq_instrument_aliases_scope"
        ),
        Index("ix_instrument_aliases_instrument", "instrument_id"),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False
    )
    alias: Mapped[str] = mapped_column(String(60), nullable=False)
    alias_normalized: Mapped[str] = mapped_column(String(60), nullable=False)
    #: Which broker/feed uses this alias; "*" means any.
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="*")


__all__ = ["Instrument", "InstrumentAlias"]
