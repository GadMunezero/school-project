"""Backtest and replay contracts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, model_validator

from tradeloom.core.enums import (
    BacktestRunMode,
    Direction,
    ExecutionModelType,
    JobStatus,
    OrderSide,
    OrderType,
    PositionSizingType,
    Timeframe,
)
from tradeloom.schemas.common import TradeloomModel


class BacktestCreate(TradeloomModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    strategy_id: Any
    strategy_version_id: Any | None = None
    instrument_id: Any
    market_data_source_id: Any | None = None
    timeframe: Timeframe
    start_date: date
    end_date: date

    initial_capital: Decimal = Field(gt=0)
    currency: str = Field(default="USD", max_length=8)
    leverage: Decimal = Field(default=Decimal(1), gt=0, le=Decimal(100))

    position_sizing: PositionSizingType = PositionSizingType.PERCENT_RISK
    sizing_config: dict[str, Any] = Field(default_factory=dict)
    risk_percent: Decimal | None = Field(default=Decimal(1), ge=0, le=100)
    max_concurrent_positions: int = Field(default=1, ge=1, le=50)
    max_position_size: Decimal | None = Field(default=None, gt=0)
    allow_pyramiding: bool = False
    cooldown_bars: int = Field(default=0, ge=0, le=1000)

    execution_model: ExecutionModelType = ExecutionModelType.NEXT_BAR_OPEN
    commission_config: dict[str, Any] = Field(default_factory=dict)
    slippage_config: dict[str, Any] = Field(default_factory=dict)
    spread_config: dict[str, Any] = Field(default_factory=dict)
    session_config: dict[str, Any] = Field(default_factory=dict)
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _dates(self) -> BacktestCreate:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class BacktestRead(TradeloomModel):
    id: Any
    name: str
    description: str | None
    strategy_id: Any
    strategy_version_id: Any
    instrument_id: Any
    market_data_source_id: Any
    timeframe: Timeframe
    start_date: date
    end_date: date
    initial_capital: Decimal
    currency: str
    leverage: Decimal
    position_sizing: PositionSizingType
    risk_percent: Decimal | None
    max_concurrent_positions: int
    allow_pyramiding: bool
    cooldown_bars: int
    execution_model: ExecutionModelType
    commission_config: dict[str, Any]
    slippage_config: dict[str, Any]
    spread_config: dict[str, Any]
    session_config: dict[str, Any]
    parameters: dict[str, Any]
    last_run_id: Any | None
    created_at: datetime
    updated_at: datetime
    strategy_name: str | None = None
    instrument_symbol: str | None = None


class BacktestRunRead(TradeloomModel):
    id: Any
    backtest_id: Any
    mode: BacktestRunMode
    status: JobStatus
    progress_percent: int
    engine_version: str
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    bars_processed: int
    trade_count: int
    final_equity: Decimal | None
    total_return_percent: Decimal | None
    max_drawdown_percent: Decimal | None
    profit_factor: Decimal | None
    #: Everything needed to reproduce this run byte-for-byte.
    input_digest: str | None
    config_snapshot: dict[str, Any]
    data_snapshot: dict[str, Any]
    warnings: dict[str, Any]
    error: dict[str, Any]
    created_at: datetime
    job_id: Any | None


class BacktestTradeRead(TradeloomModel):
    sequence: int
    symbol: str
    direction: Direction
    entry_timestamp: datetime
    exit_timestamp: datetime | None
    entry_price: Decimal
    exit_price: Decimal | None
    quantity: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    commission: Decimal
    slippage: Decimal
    gross_pnl: Decimal
    net_pnl: Decimal
    risk_amount: Decimal | None
    r_multiple: Decimal | None
    return_percentage: Decimal | None
    holding_seconds: int | None
    mfe_amount: Decimal | None
    mae_amount: Decimal | None
    exit_reason: str | None
    equity_after: Decimal | None


class BacktestOrderRead(TradeloomModel):
    sequence: int
    side: OrderSide
    order_type: OrderType
    status: str
    quantity: Decimal
    filled_quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    fill_price: Decimal | None
    reference_price: Decimal | None
    commission: Decimal
    slippage: Decimal
    #: The three timestamps that make the execution assumption auditable.
    signal_timestamp: datetime
    order_timestamp: datetime
    fill_timestamp: datetime | None
    reject_reason: str | None
    tag: str | None


class EquityPointRead(TradeloomModel):
    timestamp: datetime
    equity: Decimal
    cash: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    open_positions: int
    exposure: Decimal


class DrawdownPointRead(TradeloomModel):
    started_at: datetime
    trough_at: datetime
    recovered_at: datetime | None
    peak_equity: Decimal
    trough_equity: Decimal
    depth: Decimal
    depth_percent: Decimal
    duration_seconds: int
    recovery_seconds: int | None


class BacktestResultRead(TradeloomModel):
    run: BacktestRunRead
    metrics: dict[str, Any]
    breakdowns: dict[str, Any]
    equity_curve: list[EquityPointRead] = Field(default_factory=list)
    drawdowns: list[DrawdownPointRead] = Field(default_factory=list)
    trades: list[BacktestTradeRead] = Field(default_factory=list)


class CompareRunsRequest(TradeloomModel):
    run_ids: list[Any] = Field(min_length=2, max_length=6)


# --- replay ----------------------------------------------------------------


class ReplayCreate(TradeloomModel):
    name: str = Field(min_length=1, max_length=160)
    instrument_id: Any
    market_data_source_id: Any | None = None
    timeframe: Timeframe
    start_at: datetime
    end_at: datetime
    initial_capital: Decimal = Field(default=Decimal(100_000), gt=0)
    currency: str = Field(default="USD", max_length=8)
    execution_model: ExecutionModelType = ExecutionModelType.NEXT_BAR_OPEN
    commission_config: dict[str, Any] = Field(default_factory=dict)
    slippage_config: dict[str, Any] = Field(default_factory=dict)
    spread_config: dict[str, Any] = Field(default_factory=dict)
    #: Bars revealed before the session starts, so the chart has context.
    warmup_bars: int = Field(default=100, ge=0, le=2000)

    @model_validator(mode="after")
    def _range(self) -> ReplayCreate:
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class ReplayStepRequest(TradeloomModel):
    steps: int = Field(default=1, ge=1, le=500)


class ReplayOrderRequest(TradeloomModel):
    side: OrderSide
    quantity: Decimal = Field(gt=0)
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = Field(default=None, gt=0)
    stop_price: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _prices(self) -> ReplayOrderRequest:
        if self.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and self.limit_price is None:
            raise ValueError("limit_price is required for limit orders")
        if self.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and self.stop_price is None:
            raise ValueError("stop_price is required for stop orders")
        return self


class ReplayProtectionRequest(TradeloomModel):
    stop_loss: Decimal | None = Field(default=None, gt=0)
    take_profit: Decimal | None = Field(default=None, gt=0)


class ReplayStateRead(TradeloomModel):
    id: Any
    name: str
    timeframe: Timeframe
    cursor_index: int
    total_bars: int
    is_finished: bool
    currency: str
    initial_capital: Decimal
    equity: Decimal
    cash: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal | None
    position: dict[str, Any] | None
    working_orders: list[dict[str, Any]] = Field(default_factory=list)
    closed_trades: list[dict[str, Any]] = Field(default_factory=list)
    equity_curve: list[dict[str, Any]] = Field(default_factory=list)
    #: Only bars up to the cursor. Future candles are never sent to the client.
    visible_candles: list[dict[str, Any]] = Field(default_factory=list)
    current_bar: dict[str, Any] | None = None


__all__ = [
    "BacktestCreate",
    "BacktestOrderRead",
    "BacktestRead",
    "BacktestResultRead",
    "BacktestRunRead",
    "BacktestTradeRead",
    "CompareRunsRequest",
    "DrawdownPointRead",
    "EquityPointRead",
    "ReplayCreate",
    "ReplayOrderRequest",
    "ReplayProtectionRequest",
    "ReplayStateRead",
    "ReplayStepRequest",
]
