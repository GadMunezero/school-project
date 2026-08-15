"""Orders, positions and trades.

**The lifecycle, stated precisely** (implemented in ``services/trading/position_builder.py`` and
documented in ``docs/FINANCIALS.md``):

1. An :class:`Order` row is an *execution* — a fill with a quantity, a price and a timestamp.
   Orders are the atomic, immutable facts; everything else is derived from them.
2. Orders for the same ``(account, instrument)`` are applied in timestamp order to the currently
   open :class:`Trade`. An order in the same direction as the open trade **scales in**; an order
   in the opposite direction **scales out**.
3. If a closing order exceeds the remaining quantity, the trade is closed at the exact remaining
   size and the surplus **opens a new trade in the opposite direction** (a flip). One order can
   therefore touch two trades; the ``order_allocations`` payload on the order records the split.
4. A :class:`Trade` is the round trip. It stays ``open``/``partially_closed`` while
   ``remaining_quantity > 0`` and becomes ``closed`` when it reaches zero, at which point
   ``exit_timestamp`` is the timestamp of the final closing fill.
5. A :class:`Position` is the live net exposure for an ``(account, instrument)`` pair — a cache of
   "what am I holding right now", kept in step with the open trade.

Cost basis uses the **weighted average price** method (not FIFO). Realised P&L on a partial exit
is ``(exit_price - avg_entry_price) * closed_qty * multiplier * direction_sign``; the average
entry price is unchanged by an exit, so the remaining quantity keeps its original basis.
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
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tradeloom.core.enums import (
    AssetType,
    Direction,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionStatus,
    TimeInForce,
    TradeSource,
    TradeStatus,
    TradingSession,
)
from tradeloom.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from tradeloom.db.types import (
    GUID,
    EnumType,
    JSONDict,
    TZDateTime,
    money_column,
    percent_column,
    price_column,
    quantity_column,
    ratio_column,
)


class Trade(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "trades"
    __table_args__ = (
        Index("ix_trades_org_account_entry", "organization_id", "account_id", "entry_timestamp"),
        Index("ix_trades_org_status", "organization_id", "status"),
        Index("ix_trades_org_symbol", "organization_id", "symbol"),
        Index("ix_trades_org_exit", "organization_id", "exit_timestamp"),
        Index("ix_trades_strategy", "strategy_id"),
        Index("ix_trades_import", "import_id"),
        # Partial indexes for the two hottest journal queries. Created in migration
        # b1d4e7a90c22; declared here so `alembic check` sees no drift.
        Index(
            "ix_trades_open_by_account",
            "account_id",
            "instrument_id",
            "direction",
            postgresql_where=text("status IN ('open', 'partially_closed')"),
            sqlite_where=text("status IN ('open', 'partially_closed')"),
        ),
        Index(
            "ix_trades_active_org_exit",
            "organization_id",
            "exit_timestamp",
            postgresql_where=text("deleted_at IS NULL"),
            sqlite_where=text("deleted_at IS NULL"),
        ),
        UniqueConstraint("account_id", "external_id", name="uq_trades_account_external_id"),
        CheckConstraint("quantity > 0", name="trades_quantity_positive"),
        CheckConstraint("remaining_quantity >= 0", name="trades_remaining_non_negative"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("instruments.id", ondelete="SET NULL"), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    #: Denormalised so a trade keeps its symbol even if the instrument row is later removed.
    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    asset_type: Mapped[AssetType] = mapped_column(EnumType(AssetType, 20), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    contract_multiplier: Mapped[Decimal] = mapped_column(
        quantity_column(), nullable=False, default=Decimal(1)
    )

    direction: Mapped[Direction] = mapped_column(EnumType(Direction, 8), nullable=False)
    status: Mapped[TradeStatus] = mapped_column(
        EnumType(TradeStatus, 20), nullable=False, default=TradeStatus.OPEN
    )
    source: Mapped[TradeSource] = mapped_column(
        EnumType(TradeSource, 16), nullable=False, default=TradeSource.MANUAL
    )

    entry_timestamp: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    exit_timestamp: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)

    #: Quantity-weighted averages across all entry / exit fills.
    entry_price: Mapped[Decimal] = mapped_column(price_column(), nullable=False)
    exit_price: Mapped[Decimal | None] = mapped_column(price_column(), nullable=True)

    #: Total opened size (sum of entry fills). ``closed`` + ``remaining`` always equals it.
    quantity: Mapped[Decimal] = mapped_column(quantity_column(), nullable=False)
    closed_quantity: Mapped[Decimal] = mapped_column(
        quantity_column(), nullable=False, default=Decimal(0)
    )
    remaining_quantity: Mapped[Decimal] = mapped_column(
        quantity_column(), nullable=False, default=Decimal(0)
    )

    stop_loss: Mapped[Decimal | None] = mapped_column(price_column(), nullable=True)
    #: The stop as originally planned; ``stop_loss`` may be trailed. R is measured against this.
    initial_stop_loss: Mapped[Decimal | None] = mapped_column(price_column(), nullable=True)
    take_profit: Mapped[Decimal | None] = mapped_column(price_column(), nullable=True)

    commission: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    fees: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    #: Recorded for analysis only — it is already reflected in the fill prices.
    slippage: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)

    #: Realised P&L before costs.
    gross_pnl: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    #: gross_pnl - commission - fees. The number every performance metric is built from.
    net_pnl: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)

    #: Cash amount at risk at entry. Derived from the initial stop when one exists, otherwise
    #: supplied by the trader. NULL means R multiple is undefined for this trade, not zero.
    risk_amount: Mapped[Decimal | None] = mapped_column(money_column(), nullable=True)
    r_multiple: Mapped[Decimal | None] = mapped_column(ratio_column(), nullable=True)
    return_percentage: Mapped[Decimal | None] = mapped_column(percent_column(), nullable=True)
    holding_seconds: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    #: Maximum favourable / adverse excursion, in price and in account currency. Populated from
    #: market data when candles covering the holding period are available; NULL otherwise.
    mfe_price: Mapped[Decimal | None] = mapped_column(price_column(), nullable=True)
    mae_price: Mapped[Decimal | None] = mapped_column(price_column(), nullable=True)
    mfe_amount: Mapped[Decimal | None] = mapped_column(money_column(), nullable=True)
    mae_amount: Mapped[Decimal | None] = mapped_column(money_column(), nullable=True)
    excursion_source: Mapped[str | None] = mapped_column(String(40), nullable=True)

    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True
    )
    strategy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("strategy_versions.id", ondelete="SET NULL"), nullable=True
    )
    setup_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("setups.id", ondelete="SET NULL"), nullable=True
    )
    session: Mapped[TradingSession | None] = mapped_column(
        EnumType(TradingSession, 20), nullable=True, index=True
    )

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Optional self-assessment, 1-5. Used by the "execution quality" analytics breakdown.
    rating: Mapped[int | None] = mapped_column(nullable=True)
    custom_metadata: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)

    import_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("imports.id", ondelete="SET NULL"), nullable=True
    )
    backtest_run_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=True
    )
    #: Broker/import identity used for duplicate detection. Unique per account when present.
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)

    @property
    def is_open(self) -> bool:
        return self.status in {TradeStatus.OPEN, TradeStatus.PARTIALLY_CLOSED}

    @property
    def is_winner(self) -> bool | None:
        if self.status is not TradeStatus.CLOSED:
            return None
        return self.net_pnl > 0


class Order(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """An execution (fill). Immutable in normal operation.

    Orders that were never filled are retained with a terminal non-filled status so an imported
    order book stays faithful, but only filled quantity affects positions and P&L.
    """

    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_org_account_placed", "organization_id", "account_id", "placed_at"),
        Index("ix_orders_trade", "trade_id"),
        Index("ix_orders_instrument_time", "instrument_id", "filled_at"),
        UniqueConstraint("account_id", "external_id", name="uq_orders_account_external_id"),
        CheckConstraint("quantity > 0", name="orders_quantity_positive"),
        CheckConstraint("filled_quantity >= 0", name="orders_filled_non_negative"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("instruments.id", ondelete="SET NULL"), nullable=True
    )
    trade_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("trades.id", ondelete="SET NULL"), nullable=True
    )
    position_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("positions.id", ondelete="SET NULL"), nullable=True
    )

    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    side: Mapped[OrderSide] = mapped_column(EnumType(OrderSide, 8), nullable=False)
    order_type: Mapped[OrderType] = mapped_column(
        EnumType(OrderType, 16), nullable=False, default=OrderType.MARKET
    )
    time_in_force: Mapped[TimeInForce] = mapped_column(
        EnumType(TimeInForce, 8), nullable=False, default=TimeInForce.GTC
    )
    status: Mapped[OrderStatus] = mapped_column(
        EnumType(OrderStatus, 20), nullable=False, default=OrderStatus.FILLED
    )

    quantity: Mapped[Decimal] = mapped_column(quantity_column(), nullable=False)
    filled_quantity: Mapped[Decimal] = mapped_column(
        quantity_column(), nullable=False, default=Decimal(0)
    )
    #: Limit price for limit/stop-limit orders.
    limit_price: Mapped[Decimal | None] = mapped_column(price_column(), nullable=True)
    #: Trigger price for stop/stop-limit orders.
    stop_price: Mapped[Decimal | None] = mapped_column(price_column(), nullable=True)
    average_fill_price: Mapped[Decimal | None] = mapped_column(price_column(), nullable=True)

    commission: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    fees: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)

    placed_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    filled_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)

    #: True when this fill increased exposure, False when it reduced it. Set by the aggregator.
    is_entry: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: How the fill was split when it closed one trade and opened another (a flip).
    allocations: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)

    broker_order_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("imports.id", ondelete="SET NULL"), nullable=True
    )
    backtest_run_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)


class Position(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Live net exposure for an ``(account, instrument)`` pair.

    Exactly one open position may exist per pair; the partial unique index is created in the
    migration (``WHERE status = 'open'``) because PostgreSQL cannot express it as a table-level
    constraint.
    """

    __tablename__ = "positions"
    __table_args__ = (
        Index("ix_positions_org_account_status", "organization_id", "account_id", "status"),
        Index("ix_positions_instrument", "instrument_id"),
        Index(
            "uq_positions_open_account_instrument",
            "account_id",
            "instrument_id",
            unique=True,
            postgresql_where=text("status = 'open'"),
            sqlite_where=text("status = 'open'"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("instruments.id", ondelete="SET NULL"), nullable=True
    )
    trade_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("trades.id", ondelete="SET NULL"), nullable=True
    )

    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    direction: Mapped[Direction] = mapped_column(EnumType(Direction, 8), nullable=False)
    status: Mapped[PositionStatus] = mapped_column(
        EnumType(PositionStatus, 12), nullable=False, default=PositionStatus.OPEN
    )

    quantity: Mapped[Decimal] = mapped_column(quantity_column(), nullable=False, default=0)
    average_price: Mapped[Decimal] = mapped_column(price_column(), nullable=False, default=0)
    contract_multiplier: Mapped[Decimal] = mapped_column(
        quantity_column(), nullable=False, default=Decimal(1)
    )

    realized_pnl: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    #: Mark-to-market values. NULL when no mark price is available — never faked with entry price.
    last_price: Mapped[Decimal | None] = mapped_column(price_column(), nullable=True)
    unrealized_pnl: Mapped[Decimal | None] = mapped_column(money_column(), nullable=True)
    marked_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)

    opened_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)


class TradeTag(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "trade_tags"
    __table_args__ = (
        UniqueConstraint("trade_id", "tag_id", name="uq_trade_tags_trade_tag"),
        Index("ix_trade_tags_tag", "tag_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trade_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("trades.id", ondelete="CASCADE"), nullable=False
    )
    tag_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("tags.id", ondelete="CASCADE"), nullable=False
    )


__all__ = ["Order", "Position", "Trade", "TradeTag"]
