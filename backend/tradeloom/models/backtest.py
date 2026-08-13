"""Backtest configuration, runs, results and replay sessions.

A :class:`Backtest` is the *configuration* a user saved. A :class:`BacktestRun` is one execution
of it, and carries a complete reproducibility record: engine version, strategy version, resolved
parameters, data source, the exact bar range consumed, and every cost/execution model setting.
Re-running a stored run with the same inputs must produce byte-identical results — the engine has
no wall-clock or RNG dependency (see ``docs/BACKTESTING.md``).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from tradeloom.core.enums import (
    BacktestRunMode,
    Direction,
    ExecutionModelType,
    JobStatus,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSizingType,
    Timeframe,
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


class Backtest(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "backtests"
    __table_args__ = (Index("ix_backtests_org_created", "organization_id", "created_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("strategy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False
    )
    market_data_source_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("market_data_sources.id", ondelete="RESTRICT"), nullable=False
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeframe: Mapped[Timeframe] = mapped_column(EnumType(Timeframe, 8), nullable=False)
    start_date: Mapped[date] = mapped_column(nullable=False)
    end_date: Mapped[date] = mapped_column(nullable=False)

    initial_capital: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    leverage: Mapped[Decimal] = mapped_column(quantity_column(), nullable=False, default=Decimal(1))

    position_sizing: Mapped[PositionSizingType] = mapped_column(
        EnumType(PositionSizingType, 32), nullable=False, default=PositionSizingType.PERCENT_RISK
    )
    sizing_config: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    risk_percent: Mapped[Decimal | None] = mapped_column(percent_column(), nullable=True)
    max_concurrent_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_position_size: Mapped[Decimal | None] = mapped_column(quantity_column(), nullable=True)
    allow_pyramiding: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cooldown_bars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    execution_model: Mapped[ExecutionModelType] = mapped_column(
        EnumType(ExecutionModelType, 32), nullable=False, default=ExecutionModelType.NEXT_BAR_OPEN
    )
    commission_config: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    slippage_config: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    spread_config: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    session_config: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    #: Parameter overrides layered on top of the strategy version's defaults.
    parameters: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)

    last_run_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)


class BacktestRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "backtest_runs"
    __table_args__ = (
        Index("ix_backtest_runs_org_status", "organization_id", "status"),
        Index("ix_backtest_runs_backtest", "backtest_id", "created_at"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    backtest_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("backtests.id", ondelete="CASCADE"), nullable=False
    )
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("job_records.id", ondelete="SET NULL"), nullable=True
    )

    mode: Mapped[BacktestRunMode] = mapped_column(
        EnumType(BacktestRunMode, 16), nullable=False, default=BacktestRunMode.BACKTEST
    )
    status: Mapped[JobStatus] = mapped_column(
        EnumType(JobStatus, 16), nullable=False, default=JobStatus.QUEUED
    )
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    started_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # --- reproducibility record -------------------------------------------
    engine_version: Mapped[str] = mapped_column(String(24), nullable=False, default="0")
    #: Frozen copy of every input: strategy key + resolved params, cost models, sizing, sessions.
    config_snapshot: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    #: Data provenance: source key, instrument, timeframe, first/last bar, bar count, digest.
    data_snapshot: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    #: SHA-256 over the ordered inputs. Identical hash + identical engine version => identical run.
    input_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)

    bars_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Full metric set from PerformanceAnalyzer, stored as JSON so new metrics need no migration.
    metrics: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    #: Breakdowns: by symbol, session, weekday, hour, month, long/short, R buckets.
    breakdowns: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    #: Non-fatal notes surfaced to the user (data gaps, orders rejected for insufficient equity).
    warnings: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    #: User-safe failure message plus an error code. Stack traces stay in the job record.
    error: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)

    final_equity: Mapped[Decimal | None] = mapped_column(money_column(), nullable=True)
    total_return_percent: Mapped[Decimal | None] = mapped_column(percent_column(), nullable=True)
    max_drawdown_percent: Mapped[Decimal | None] = mapped_column(percent_column(), nullable=True)
    profit_factor: Mapped[Decimal | None] = mapped_column(ratio_column(), nullable=True)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class BacktestTrade(Base, UUIDPrimaryKeyMixin):
    """A closed round trip produced by a simulation.

    Kept separate from :class:`~tradeloom.models.trading.Trade` so simulated results can never be
    mistaken for, or aggregated into, a trader's real journal.
    """

    __tablename__ = "backtest_trades"
    __table_args__ = (
        Index("ix_backtest_trades_run_entry", "backtest_run_id", "entry_timestamp"),
        UniqueConstraint("backtest_run_id", "sequence", name="uq_backtest_trades_run_sequence"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    backtest_run_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    #: 1-based ordinal, making runs directly comparable trade-by-trade.
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    direction: Mapped[Direction] = mapped_column(EnumType(Direction, 8), nullable=False)
    entry_timestamp: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    exit_timestamp: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    entry_price: Mapped[Decimal] = mapped_column(price_column(), nullable=False)
    exit_price: Mapped[Decimal | None] = mapped_column(price_column(), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(quantity_column(), nullable=False)

    stop_loss: Mapped[Decimal | None] = mapped_column(price_column(), nullable=True)
    take_profit: Mapped[Decimal | None] = mapped_column(price_column(), nullable=True)
    commission: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    slippage: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    gross_pnl: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    net_pnl: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    risk_amount: Mapped[Decimal | None] = mapped_column(money_column(), nullable=True)
    r_multiple: Mapped[Decimal | None] = mapped_column(ratio_column(), nullable=True)
    return_percentage: Mapped[Decimal | None] = mapped_column(percent_column(), nullable=True)
    holding_seconds: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    mfe_price: Mapped[Decimal | None] = mapped_column(price_column(), nullable=True)
    mae_price: Mapped[Decimal | None] = mapped_column(price_column(), nullable=True)
    mfe_amount: Mapped[Decimal | None] = mapped_column(money_column(), nullable=True)
    mae_amount: Mapped[Decimal | None] = mapped_column(money_column(), nullable=True)
    #: Why the position closed: "signal", "stop_loss", "take_profit", "end_of_data", "session_end".
    exit_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    equity_after: Mapped[Decimal | None] = mapped_column(money_column(), nullable=True)


class BacktestOrder(Base, UUIDPrimaryKeyMixin):
    """Every order the simulation created, including ones that were never filled.

    This is the audit trail that makes an execution assumption checkable: signal time, order time,
    fill time and fill price are all recorded separately.
    """

    __tablename__ = "backtest_orders"
    __table_args__ = (Index("ix_backtest_orders_run_seq", "backtest_run_id", "sequence"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    backtest_run_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    backtest_trade_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("backtest_trades.id", ondelete="CASCADE"), nullable=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    symbol: Mapped[str] = mapped_column(String(40), nullable=False)
    side: Mapped[OrderSide] = mapped_column(EnumType(OrderSide, 8), nullable=False)
    order_type: Mapped[OrderType] = mapped_column(EnumType(OrderType, 16), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(EnumType(OrderStatus, 20), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(quantity_column(), nullable=False)
    filled_quantity: Mapped[Decimal] = mapped_column(
        quantity_column(), nullable=False, default=Decimal(0)
    )
    limit_price: Mapped[Decimal | None] = mapped_column(price_column(), nullable=True)
    stop_price: Mapped[Decimal | None] = mapped_column(price_column(), nullable=True)
    fill_price: Mapped[Decimal | None] = mapped_column(price_column(), nullable=True)
    #: Fill price before slippage/spread, so cost impact is attributable.
    reference_price: Mapped[Decimal | None] = mapped_column(price_column(), nullable=True)
    commission: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    slippage: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)

    #: The three timestamps that define the execution assumption.
    signal_timestamp: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    order_timestamp: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    fill_timestamp: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tag: Mapped[str | None] = mapped_column(String(40), nullable=True)


class EquityPoint(Base, UUIDPrimaryKeyMixin):
    """Equity curve sample. One row per bar for replay-grade fidelity, or per day for long runs —
    ``resolution`` records which."""

    __tablename__ = "equity_points"
    __table_args__ = (
        Index("ix_equity_points_run_ts", "backtest_run_id", "timestamp"),
        UniqueConstraint("backtest_run_id", "timestamp", name="uq_equity_points_run_ts"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    backtest_run_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    resolution: Mapped[str] = mapped_column(String(8), nullable=False, default="bar")

    equity: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    cash: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    realized_pnl: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    unrealized_pnl: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    open_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    exposure: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)


class DrawdownPoint(Base, UUIDPrimaryKeyMixin):
    """One row per drawdown episode: peak, trough, recovery and duration."""

    __tablename__ = "drawdown_points"
    __table_args__ = (Index("ix_drawdown_points_run_start", "backtest_run_id", "started_at"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    backtest_run_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("backtest_runs.id", ondelete="CASCADE"), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    trough_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    #: NULL when the run ended before equity recovered to the prior peak.
    recovered_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    peak_equity: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    trough_equity: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    depth: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    depth_percent: Mapped[Decimal] = mapped_column(percent_column(), nullable=False)
    duration_seconds: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    recovery_seconds: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class ReplaySession(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Interactive replay state.

    Replay is not an animation: it drives the same :class:`BrokerSimulator` as the backtester, one
    bar at a time, persisting the simulator state so a session survives a page refresh.
    """

    __tablename__ = "replay_sessions"
    __table_args__ = (Index("ix_replay_sessions_org_user", "organization_id", "user_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False
    )
    market_data_source_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("market_data_sources.id", ondelete="RESTRICT"), nullable=False
    )
    backtest_run_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("backtest_runs.id", ondelete="SET NULL"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    timeframe: Mapped[Timeframe] = mapped_column(EnumType(Timeframe, 8), nullable=False)
    start_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    end_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    #: Index of the last bar delivered to the simulator. Bars beyond it are never sent to the
    #: client, which is what prevents the user from seeing the future.
    cursor_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_bars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    initial_capital: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    execution_model: Mapped[ExecutionModelType] = mapped_column(
        EnumType(ExecutionModelType, 32), nullable=False, default=ExecutionModelType.NEXT_BAR_OPEN
    )
    commission_config: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    slippage_config: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    spread_config: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)

    #: Serialised simulator state (cash, positions, working orders, closed trades, equity curve).
    state: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    is_finished: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_interacted_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)


__all__ = [
    "Backtest",
    "BacktestOrder",
    "BacktestRun",
    "BacktestTrade",
    "DrawdownPoint",
    "EquityPoint",
    "ReplaySession",
]
