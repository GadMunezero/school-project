"""Trade, order and position contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, model_validator

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
from tradeloom.schemas.common import TradeloomModel


class TagRef(TradeloomModel):
    id: Any
    name: str
    slug: str
    color: str | None = None
    category: str = "custom"


class FillInput(TradeloomModel):
    """One execution supplied by the client."""

    side: OrderSide
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    timestamp: datetime
    commission: Decimal = Field(default=Decimal(0), ge=0)
    fees: Decimal = Field(default=Decimal(0), ge=0)
    order_type: OrderType = OrderType.MARKET
    external_id: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=500)


class TradeCreate(TradeloomModel):
    """Create a trade from an entry (and optionally an exit).

    Either supply ``fills`` for full control over scaling, or the simple
    entry/exit fields for the common single-entry single-exit case.
    """

    account_id: Any
    symbol: str = Field(min_length=1, max_length=40)
    asset_type: AssetType = AssetType.EQUITY
    direction: Direction | None = None

    entry_timestamp: datetime | None = None
    entry_price: Decimal | None = Field(default=None, gt=0)
    exit_timestamp: datetime | None = None
    exit_price: Decimal | None = Field(default=None, gt=0)
    quantity: Decimal | None = Field(default=None, gt=0)
    commission: Decimal = Field(default=Decimal(0), ge=0)
    fees: Decimal = Field(default=Decimal(0), ge=0)

    fills: list[FillInput] = Field(default_factory=list)

    stop_loss: Decimal | None = Field(default=None, gt=0)
    take_profit: Decimal | None = Field(default=None, gt=0)
    #: Explicit cash risk, used when there is no stop. Leave unset to derive it from the stop.
    risk_amount: Decimal | None = Field(default=None, gt=0)

    strategy_id: Any | None = None
    setup_id: Any | None = None
    tag_ids: list[Any] = Field(default_factory=list)
    notes: str | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    custom_metadata: dict[str, Any] = Field(default_factory=dict)
    external_id: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _shape(self) -> TradeCreate:
        if self.fills:
            return self
        missing = [
            name
            for name, value in (
                ("direction", self.direction),
                ("entry_timestamp", self.entry_timestamp),
                ("entry_price", self.entry_price),
                ("quantity", self.quantity),
            )
            if value is None
        ]
        if missing:
            raise ValueError("Provide either `fills`, or all of: " + ", ".join(sorted(missing)))
        if (self.exit_price is None) != (self.exit_timestamp is None):
            raise ValueError("An exit needs both `exit_price` and `exit_timestamp`")
        if (
            self.exit_timestamp is not None
            and self.entry_timestamp is not None
            and self.exit_timestamp < self.entry_timestamp
        ):
            raise ValueError("`exit_timestamp` cannot be before `entry_timestamp`")
        return self


class TradeUpdate(TradeloomModel):
    """Editable journal fields.

    Prices, quantities and timestamps are *not* here: they are derived from fills. Changing an
    execution means adding or removing a fill, so the ledger and the trade can never disagree.
    """

    stop_loss: Decimal | None = Field(default=None, gt=0)
    take_profit: Decimal | None = Field(default=None, gt=0)
    risk_amount: Decimal | None = Field(default=None, gt=0)
    strategy_id: Any | None = None
    setup_id: Any | None = None
    tag_ids: list[Any] | None = None
    notes: str | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    custom_metadata: dict[str, Any] | None = None


class OrderRead(TradeloomModel):
    id: Any
    account_id: Any
    trade_id: Any | None
    symbol: str
    side: OrderSide
    order_type: OrderType
    time_in_force: TimeInForce
    status: OrderStatus
    quantity: Decimal
    filled_quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    average_fill_price: Decimal | None
    commission: Decimal
    fees: Decimal
    placed_at: datetime
    filled_at: datetime | None
    is_entry: bool | None
    external_id: str | None
    notes: str | None


class TradeRead(TradeloomModel):
    id: Any
    account_id: Any
    instrument_id: Any | None
    symbol: str
    asset_type: AssetType
    currency: str
    contract_multiplier: Decimal
    direction: Direction
    status: TradeStatus
    source: TradeSource

    entry_timestamp: datetime
    exit_timestamp: datetime | None
    entry_price: Decimal
    exit_price: Decimal | None
    quantity: Decimal
    closed_quantity: Decimal
    remaining_quantity: Decimal

    stop_loss: Decimal | None
    initial_stop_loss: Decimal | None
    take_profit: Decimal | None

    commission: Decimal
    fees: Decimal
    slippage: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal

    risk_amount: Decimal | None
    r_multiple: Decimal | None
    return_percentage: Decimal | None
    holding_seconds: int | None

    mfe_price: Decimal | None
    mae_price: Decimal | None
    mfe_amount: Decimal | None
    mae_amount: Decimal | None

    strategy_id: Any | None
    setup_id: Any | None
    session: TradingSession | None
    notes: str | None
    rating: int | None
    custom_metadata: dict[str, Any]
    external_id: str | None
    import_id: Any | None
    created_at: datetime
    updated_at: datetime

    tags: list[TagRef] = Field(default_factory=list)
    #: Denormalised labels so the journal table does not need a second round trip.
    account_name: str | None = None
    strategy_name: str | None = None
    setup_name: str | None = None


class ScreenshotRead(TradeloomModel):
    id: Any
    file_object_id: Any
    caption: str | None
    phase: str
    timeframe: str | None
    display_order: int
    content_type: str
    size_bytes: int
    original_filename: str | None
    created_at: datetime
    #: Short-lived signed URL, minted per request after an ownership check.
    url: str | None = None


class TradeDetail(TradeloomModel):
    trade: TradeRead
    orders: list[OrderRead] = Field(default_factory=list)
    screenshots: list[ScreenshotRead] = Field(default_factory=list)
    #: Reward:risk implied by the original stop and target.
    planned_reward_risk: Decimal | None = None
    #: Fraction of the favourable excursion actually captured.
    efficiency: Decimal | None = None


class TradeFilters(TradeloomModel):
    """Server-side filters. The journal never ships a full dataset to the browser."""

    account_ids: list[Any] = Field(default_factory=list)
    symbols: list[str] = Field(default_factory=list)
    directions: list[Direction] = Field(default_factory=list)
    statuses: list[TradeStatus] = Field(default_factory=list)
    strategy_ids: list[Any] = Field(default_factory=list)
    setup_ids: list[Any] = Field(default_factory=list)
    tag_ids: list[Any] = Field(default_factory=list)
    sessions: list[TradingSession] = Field(default_factory=list)
    asset_types: list[AssetType] = Field(default_factory=list)

    date_from: datetime | None = None
    date_to: datetime | None = None
    #: "winners" | "losers" | "breakeven" | None
    outcome: str | None = Field(default=None, pattern="^(winners|losers|breakeven)$")
    pnl_min: Decimal | None = None
    pnl_max: Decimal | None = None
    r_min: Decimal | None = None
    r_max: Decimal | None = None
    weekdays: list[int] = Field(default_factory=list)
    hours: list[int] = Field(default_factory=list)
    search: str | None = Field(default=None, max_length=120)
    has_screenshots: bool | None = None
    has_notes: bool | None = None


class BulkTradeAction(TradeloomModel):
    trade_ids: list[Any] = Field(min_length=1, max_length=1000)


class BulkTagAction(BulkTradeAction):
    add_tag_ids: list[Any] = Field(default_factory=list)
    remove_tag_ids: list[Any] = Field(default_factory=list)


class BulkEditAction(BulkTradeAction):
    strategy_id: Any | None = None
    setup_id: Any | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    #: Appended to existing notes rather than replacing them.
    append_note: str | None = Field(default=None, max_length=2000)


class PositionRead(TradeloomModel):
    id: Any
    account_id: Any
    instrument_id: Any | None
    trade_id: Any | None
    symbol: str
    direction: Direction
    status: PositionStatus
    quantity: Decimal
    average_price: Decimal
    contract_multiplier: Decimal
    realized_pnl: Decimal
    last_price: Decimal | None
    unrealized_pnl: Decimal | None
    marked_at: datetime | None
    opened_at: datetime
    closed_at: datetime | None


class MarkPriceRequest(TradeloomModel):
    """Update the mark used for unrealised P&L on open positions."""

    prices: dict[str, Decimal] = Field(min_length=1)


__all__ = [
    "BulkEditAction",
    "BulkTagAction",
    "BulkTradeAction",
    "FillInput",
    "MarkPriceRequest",
    "OrderRead",
    "PositionRead",
    "ScreenshotRead",
    "TagRef",
    "TradeCreate",
    "TradeDetail",
    "TradeFilters",
    "TradeRead",
    "TradeUpdate",
]
