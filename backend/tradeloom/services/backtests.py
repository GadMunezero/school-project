"""Backtest configuration, submission and execution.

Submission and execution are separate on purpose:

* :meth:`BacktestService.submit` validates everything, creates a queued run and returns
  immediately with a job id. No HTTP request ever waits for a simulation.
* :meth:`BacktestService.execute` is what the Celery worker calls. It loads the data, runs the
  engine and persists the full result — trades, orders, equity samples, drawdown episodes and
  metrics — inside one transaction.

Everything needed to reproduce a run is frozen onto the run row at execution time, including the
engine version and a digest of the exact bars consumed.
"""

from __future__ import annotations

import builtins
import uuid
from datetime import datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core.enums import (
    AuditAction,
    BacktestRunMode,
    ExecutionModelType,
    JobStatus,
    StrategyKind,
)
from tradeloom.core.errors import (
    NotFoundError,
    UnprocessableStateError,
    ValidationError,
)
from tradeloom.core.logging import get_logger
from tradeloom.core.money import ONE, quantize_money, quantize_percent
from tradeloom.core.pagination import Page, PageParams
from tradeloom.core.timeutil import UTC, utcnow
from tradeloom.engine.config import (
    BacktestConfig,
    CommissionConfig,
    RiskConfig,
    SessionConfig,
    SlippageConfig,
    SpreadConfig,
)
from tradeloom.engine.registry import UnknownStrategyError, build_strategy, is_registered
from tradeloom.engine.runner import BacktestResult, BacktestRunner
from tradeloom.engine.strategy import StrategyParameterError
from tradeloom.engine.version import ENGINE_VERSION
from tradeloom.models.backtest import (
    Backtest,
    BacktestOrder,
    BacktestRun,
    BacktestTrade,
    DrawdownPoint,
    EquityPoint,
)
from tradeloom.models.strategy import Strategy, StrategyVersion
from tradeloom.repositories.base import TenantRepository
from tradeloom.repositories.trading import InstrumentRepository, StrategyRepository
from tradeloom.schemas.backtest import BacktestCreate
from tradeloom.services.audit import AuditService
from tradeloom.services.jobs import JobService
from tradeloom.services.market_data import MarketDataService

logger = get_logger(__name__)

#: Equity samples are stored per bar up to this many; beyond it they are evenly downsampled so a
#: multi-year 1-minute run does not write millions of rows.
MAX_STORED_EQUITY_POINTS = 5_000


class BacktestRepository(TenantRepository[Backtest]):
    model = Backtest


class BacktestRunRepository(TenantRepository[BacktestRun]):
    model = BacktestRun
    supports_soft_delete = False


class BacktestService:
    def __init__(
        self,
        session: AsyncSession,
        organization_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID | None = None,
    ) -> None:
        self.session = session
        self.organization_id = organization_id
        self.actor_user_id = actor_user_id
        self.backtests = BacktestRepository(session, organization_id)
        self.runs = BacktestRunRepository(session, organization_id)
        self.strategies = StrategyRepository(session, organization_id)
        self.instruments = InstrumentRepository(session, organization_id)
        self.market_data = MarketDataService(session)
        self.jobs = JobService(session)
        self.audit = AuditService(session)

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    async def get(self, backtest_id: uuid.UUID) -> Backtest:
        record = await self.backtests.get(backtest_id)
        if record is None:
            raise NotFoundError("Backtest not found.")
        return record

    async def list(self, params: PageParams) -> Page[Backtest]:
        return await self.backtests.paginate(params, order_by=[Backtest.created_at.desc()])

    async def create(self, payload: BacktestCreate) -> Backtest:
        strategy = await self.strategies.get(payload.strategy_id)
        if strategy is None:
            raise NotFoundError("Strategy not found.")
        if strategy.kind is not StrategyKind.BUILTIN or not is_registered(strategy.engine_key):
            raise ValidationError(
                "This strategy has no executable logic. Choose a strategy backed by a built-in "
                "engine to run a backtest."
            )

        version = await self._resolve_version(strategy, payload.strategy_version_id)
        instrument = await self.instruments.get(payload.instrument_id)
        if instrument is None:
            raise NotFoundError("Instrument not found.")

        source = (
            await self.market_data.get_source(payload.market_data_source_id)
            if payload.market_data_source_id
            else await self.market_data.default_source()
        )

        if payload.end_date < payload.start_date:
            raise ValidationError("The end date must be on or after the start date.")

        # Validate parameters against the engine schema *now*, not when the worker picks it up.
        merged = {**(version.parameters or {}), **payload.parameters}
        self._validate_parameters(strategy.engine_key or "", merged)

        record = Backtest(
            organization_id=self.organization_id,
            created_by_user_id=self.actor_user_id,
            strategy_id=strategy.id,
            strategy_version_id=version.id,
            instrument_id=instrument.id,
            market_data_source_id=source.id,
            name=payload.name.strip(),
            description=payload.description,
            timeframe=payload.timeframe,
            start_date=payload.start_date,
            end_date=payload.end_date,
            initial_capital=quantize_money(payload.initial_capital),
            currency=payload.currency,
            leverage=payload.leverage,
            position_sizing=payload.position_sizing,
            sizing_config=payload.sizing_config,
            risk_percent=payload.risk_percent,
            max_concurrent_positions=payload.max_concurrent_positions,
            max_position_size=payload.max_position_size,
            allow_pyramiding=payload.allow_pyramiding,
            cooldown_bars=payload.cooldown_bars,
            execution_model=payload.execution_model,
            commission_config=payload.commission_config,
            slippage_config=payload.slippage_config,
            spread_config=payload.spread_config,
            session_config=payload.session_config,
            parameters=payload.parameters,
        )
        await self.backtests.add(record)
        return record

    async def _resolve_version(
        self, strategy: Strategy, version_id: uuid.UUID | None
    ) -> StrategyVersion:
        if version_id is not None:
            version = await self.session.get(StrategyVersion, version_id)
            if (
                version is None
                or version.organization_id != self.organization_id
                or version.strategy_id != strategy.id
            ):
                raise NotFoundError("Strategy version not found.")
            return version

        if strategy.current_version_id:
            version = await self.session.get(StrategyVersion, strategy.current_version_id)
            if version is not None:
                return version

        result = await self.session.execute(
            select(StrategyVersion)
            .where(
                StrategyVersion.strategy_id == strategy.id,
                StrategyVersion.organization_id == self.organization_id,
            )
            .order_by(StrategyVersion.version.desc())
            .limit(1)
        )
        version = result.scalar_one_or_none()
        if version is None:
            raise UnprocessableStateError("This strategy has no published version to run.")
        return version

    def _validate_parameters(self, engine_key: str, parameters: dict[str, Any]) -> None:
        try:
            build_strategy(engine_key, parameters)
        except UnknownStrategyError as exc:
            raise ValidationError(str(exc)) from exc
        except StrategyParameterError as exc:
            raise ValidationError(str(exc)) from exc
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------

    async def submit(self, backtest_id: uuid.UUID) -> tuple[BacktestRun, Any]:
        """Queue a run. Returns immediately — the HTTP request never waits for the simulation."""
        backtest = await self.get(backtest_id)
        await self._assert_data_available(backtest)

        run = BacktestRun(
            organization_id=self.organization_id,
            backtest_id=backtest.id,
            triggered_by_user_id=self.actor_user_id,
            mode=BacktestRunMode.BACKTEST,
            status=JobStatus.QUEUED,
            engine_version=ENGINE_VERSION,
        )
        self.session.add(run)
        await self.session.flush()

        job = await self.jobs.create(
            kind="backtest.run",
            organization_id=self.organization_id,
            requested_by_user_id=self.actor_user_id,
            payload={"backtest_run_id": str(run.id), "backtest_id": str(backtest.id)},
        )
        run.job_id = job.id
        backtest.last_run_id = run.id
        await self.session.flush()

        await self.audit.record(
            AuditAction.BACKTEST_SUBMITTED,
            organization_id=self.organization_id,
            actor_user_id=self.actor_user_id,
            entity_type="backtest_run",
            entity_id=run.id,
            summary=f"Queued backtest {backtest.name}",
        )
        return run, job

    async def _assert_data_available(self, backtest: Backtest) -> None:
        coverage = await self.market_data.coverage(
            backtest.instrument_id, backtest.timeframe, backtest.market_data_source_id
        )
        if coverage is None or coverage.bar_count == 0:
            raise UnprocessableStateError(
                "No market data is available for that instrument and timeframe. "
                "Load candles before running a backtest."
            )
        start = datetime.combine(backtest.start_date, time.min, tzinfo=UTC)
        end = datetime.combine(backtest.end_date, time.max, tzinfo=UTC)
        if coverage.last_bar_at and coverage.last_bar_at < start:
            raise UnprocessableStateError(
                "The requested range starts after the last available candle."
            )
        if coverage.first_bar_at and coverage.first_bar_at > end:
            raise UnprocessableStateError(
                "The requested range ends before the first available candle."
            )

    # ------------------------------------------------------------------
    # Execution (called by the worker)
    # ------------------------------------------------------------------

    async def execute(
        self, run_id: uuid.UUID, *, progress=None
    ) -> BacktestRun:  # type: ignore[no-untyped-def]
        run = await self.runs.get(run_id)
        if run is None:
            raise NotFoundError("Backtest run not found.")
        if run.status.is_terminal:
            raise UnprocessableStateError("This run has already finished.")

        backtest = await self.get(run.backtest_id)
        version = await self.session.get(StrategyVersion, backtest.strategy_version_id)
        strategy_row = await self.strategies.get(backtest.strategy_id)
        if version is None or strategy_row is None:
            raise NotFoundError("The strategy for this backtest no longer exists.")

        instrument = await self.instruments.get(backtest.instrument_id)
        if instrument is None:
            raise NotFoundError("The instrument for this backtest no longer exists.")

        source = await self.market_data.get_source(backtest.market_data_source_id)
        start = datetime.combine(backtest.start_date, time.min, tzinfo=UTC)
        end = datetime.combine(backtest.end_date, time.max, tzinfo=UTC)
        series, _ = await self.market_data.get_bars(
            instrument.id, backtest.timeframe, source=source, start=start, end=end
        )

        run.status = JobStatus.RUNNING
        run.started_at = utcnow()
        run.engine_version = ENGINE_VERSION
        await self.session.flush()

        config = self._build_config(backtest, instrument)
        parameters = {**(version.parameters or {}), **(backtest.parameters or {})}
        strategy = build_strategy(strategy_row.engine_key or "", parameters)

        runner = BacktestRunner(config=config, strategy=strategy, bars=series, progress=progress)
        result = runner.run()

        await self._persist(
            run, backtest, result, source_key=source.key, series_digest=series.digest_source()
        )
        run.status = JobStatus.COMPLETED
        run.finished_at = utcnow()
        run.progress_percent = 100
        if run.started_at:
            run.duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)
        await self.session.flush()
        return run

    def _build_config(self, backtest: Backtest, instrument) -> BacktestConfig:  # type: ignore[no-untyped-def]
        session_config = SessionConfig.from_dict(backtest.session_config)
        risk = RiskConfig.from_dict(
            {
                "sizing": backtest.position_sizing.value,
                "value": str(
                    backtest.risk_percent
                    if backtest.risk_percent is not None
                    else backtest.sizing_config.get("value", 1)
                ),
                "max_concurrent_positions": backtest.max_concurrent_positions,
                "max_position_quantity": (
                    str(backtest.max_position_size)
                    if backtest.max_position_size is not None
                    else None
                ),
                "leverage": str(backtest.leverage),
                "allow_pyramiding": backtest.allow_pyramiding,
                "cooldown_bars": backtest.cooldown_bars,
                **{
                    key: value
                    for key, value in (backtest.sizing_config or {}).items()
                    if key == "max_risk_percent_per_trade"
                },
            }
        )
        slippage = SlippageConfig.from_dict(
            {**backtest.slippage_config, "tick_size": str(instrument.tick_size)}
        )
        return BacktestConfig(
            symbol=instrument.symbol,
            initial_capital=backtest.initial_capital,
            currency=backtest.currency,
            # Decides the trading day for the weekday, monthly and daily breakdowns.
            asset_type=instrument.asset_type,
            contract_multiplier=instrument.contract_multiplier,
            tick_size=instrument.tick_size,
            lot_size=instrument.lot_size,
            allow_fractional=instrument.lot_size < ONE,
            execution_model=backtest.execution_model or ExecutionModelType.NEXT_BAR_OPEN,
            commission=CommissionConfig.from_dict(backtest.commission_config),
            slippage=slippage,
            spread=SpreadConfig.from_dict(backtest.spread_config),
            risk=risk,
            session=session_config,
            periods_per_year=MarketDataService.periods_per_year(backtest.timeframe),
        )

    async def _persist(
        self,
        run: BacktestRun,
        backtest: Backtest,
        result: BacktestResult,
        *,
        source_key: str,
        series_digest: str,
    ) -> None:
        """Write the full result set. Any previous attempt's rows are cleared first."""
        for model in (BacktestOrder, BacktestTrade, EquityPoint, DrawdownPoint):
            await self.session.execute(
                delete(model).where(
                    model.backtest_run_id == run.id,
                    model.organization_id == self.organization_id,
                )
            )

        trade_rows: dict[int, BacktestTrade] = {}
        for trade in result.trades:
            row = BacktestTrade(
                organization_id=self.organization_id,
                backtest_run_id=run.id,
                sequence=trade.sequence,
                symbol=result.config.symbol,
                direction=trade.direction,
                entry_timestamp=trade.entry_timestamp,
                exit_timestamp=trade.exit_timestamp,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                quantity=trade.quantity,
                stop_loss=trade.stop_loss,
                take_profit=trade.take_profit,
                commission=trade.commission,
                slippage=trade.slippage,
                gross_pnl=trade.gross_pnl,
                net_pnl=trade.net_pnl,
                risk_amount=trade.risk_amount,
                r_multiple=trade.r_multiple,
                return_percentage=trade.return_percentage,
                holding_seconds=trade.holding_seconds,
                mfe_price=trade.mfe_price,
                mae_price=trade.mae_price,
                mfe_amount=trade.mfe_amount,
                mae_amount=trade.mae_amount,
                exit_reason=trade.exit_reason,
                equity_after=trade.equity_after,
            )
            self.session.add(row)
            trade_rows[trade.sequence] = row

        for index, order in enumerate(result.orders, start=1):
            self.session.add(
                BacktestOrder(
                    organization_id=self.organization_id,
                    backtest_run_id=run.id,
                    sequence=index,
                    symbol=result.config.symbol,
                    side=order.side,
                    order_type=order.order_type,
                    status=order.status,
                    quantity=order.quantity,
                    filled_quantity=order.filled_quantity,
                    limit_price=order.limit_price,
                    stop_price=order.stop_price,
                    fill_price=order.fill_price,
                    reference_price=order.reference_price,
                    commission=order.commission,
                    slippage=order.slippage,
                    signal_timestamp=order.signal_timestamp or order.order_timestamp or utcnow(),
                    order_timestamp=order.order_timestamp or utcnow(),
                    fill_timestamp=order.fill_timestamp,
                    reject_reason=order.reject_reason,
                    tag=order.tag,
                )
            )

        for sample in _downsample(result.equity_curve, MAX_STORED_EQUITY_POINTS):
            self.session.add(
                EquityPoint(
                    organization_id=self.organization_id,
                    backtest_run_id=run.id,
                    timestamp=sample.timestamp,
                    resolution=(
                        "bar" if len(result.equity_curve) <= MAX_STORED_EQUITY_POINTS else "sampled"
                    ),
                    equity=sample.equity,
                    cash=sample.cash,
                    realized_pnl=sample.realized_pnl,
                    unrealized_pnl=sample.unrealized_pnl,
                    open_positions=sample.open_positions,
                    exposure=sample.exposure,
                )
            )

        for episode in result.report.drawdowns:
            self.session.add(
                DrawdownPoint(
                    organization_id=self.organization_id,
                    backtest_run_id=run.id,
                    started_at=episode.started_at,
                    trough_at=episode.trough_at,
                    recovered_at=episode.recovered_at,
                    peak_equity=episode.peak_equity,
                    trough_equity=episode.trough_equity,
                    depth=episode.depth,
                    depth_percent=episode.depth_percent,
                    duration_seconds=episode.duration_seconds,
                    recovery_seconds=episode.recovery_seconds,
                )
            )

        metrics = result.report.metrics
        run.metrics = metrics
        run.breakdowns = result.report.breakdowns
        run.warnings = {"items": result.warnings}
        run.bars_processed = result.bars_processed
        run.trade_count = int(metrics.get("total_trades", 0) or 0)
        run.input_digest = result.input_digest
        run.config_snapshot = {
            "engine_version": ENGINE_VERSION,
            "strategy_id": str(backtest.strategy_id),
            "strategy_version_id": str(backtest.strategy_version_id),
            "parameters": {
                k: str(v)
                for k, v in sorted(result.config.to_dict().items())
                if k not in ("commission", "slippage", "spread", "risk", "session")
            },
            "engine_config": result.config.to_dict(),
        }
        run.data_snapshot = {
            "source_key": source_key,
            "instrument_id": str(backtest.instrument_id),
            "timeframe": backtest.timeframe.value,
            "start_date": backtest.start_date.isoformat(),
            "end_date": backtest.end_date.isoformat(),
            "bar_count": result.bars_processed,
            "series_digest": series_digest,
        }
        run.final_equity = _decimal(metrics.get("final_equity"))
        run.total_return_percent = _percent(metrics.get("total_return_percent"))
        run.max_drawdown_percent = _percent(metrics.get("max_drawdown_percent"))
        run.profit_factor = _decimal(metrics.get("profit_factor"))
        await self.session.flush()

    async def mark_failed(
        self, run_id: uuid.UUID, *, code: str, message: str
    ) -> BacktestRun | None:
        run = await self.runs.get(run_id)
        if run is None:
            return None
        run.status = JobStatus.FAILED
        run.finished_at = utcnow()
        # User-safe only. Stack traces live on the job record.
        run.error = {"code": code, "message": message}
        await self.session.flush()
        return run

    # ------------------------------------------------------------------
    # Results
    # ------------------------------------------------------------------

    async def get_run(self, run_id: uuid.UUID) -> BacktestRun:
        run = await self.runs.get(run_id)
        if run is None:
            raise NotFoundError("Backtest run not found.")
        return run

    async def list_runs(self, backtest_id: uuid.UUID, params: PageParams) -> Page[BacktestRun]:
        await self.get(backtest_id)
        return await self.runs.paginate(
            params,
            BacktestRun.backtest_id == backtest_id,
            order_by=[BacktestRun.created_at.desc()],
        )

    async def run_trades(self, run_id: uuid.UUID) -> builtins.list[BacktestTrade]:
        result = await self.session.execute(
            select(BacktestTrade)
            .where(
                BacktestTrade.backtest_run_id == run_id,
                BacktestTrade.organization_id == self.organization_id,
            )
            .order_by(BacktestTrade.sequence.asc())
        )
        return list(result.scalars().all())

    async def run_equity(self, run_id: uuid.UUID) -> builtins.list[EquityPoint]:
        result = await self.session.execute(
            select(EquityPoint)
            .where(
                EquityPoint.backtest_run_id == run_id,
                EquityPoint.organization_id == self.organization_id,
            )
            .order_by(EquityPoint.timestamp.asc())
        )
        return list(result.scalars().all())

    async def run_drawdowns(self, run_id: uuid.UUID) -> builtins.list[DrawdownPoint]:
        result = await self.session.execute(
            select(DrawdownPoint)
            .where(
                DrawdownPoint.backtest_run_id == run_id,
                DrawdownPoint.organization_id == self.organization_id,
            )
            .order_by(DrawdownPoint.started_at.asc())
        )
        return list(result.scalars().all())

    async def run_orders(self, run_id: uuid.UUID) -> builtins.list[BacktestOrder]:
        result = await self.session.execute(
            select(BacktestOrder)
            .where(
                BacktestOrder.backtest_run_id == run_id,
                BacktestOrder.organization_id == self.organization_id,
            )
            .order_by(BacktestOrder.sequence.asc())
        )
        return list(result.scalars().all())

    async def compare_runs(self, run_ids: list[uuid.UUID]) -> dict[str, Any]:
        """Side-by-side metric comparison. Runs from different engine versions are flagged."""
        runs = [await self.get_run(run_id) for run_id in run_ids]
        versions = {run.engine_version for run in runs}
        return {
            "runs": [
                {
                    "id": str(run.id),
                    "backtest_id": str(run.backtest_id),
                    "status": run.status.value,
                    "engine_version": run.engine_version,
                    "metrics": run.metrics,
                    "input_digest": run.input_digest,
                }
                for run in runs
            ],
            "comparable": len(versions) == 1,
            "engine_versions": sorted(versions),
        }


def _downsample(samples: list, limit: int) -> list:  # type: ignore[no-untyped-def]
    """Keep at most ``limit`` samples, always retaining the first and last."""
    if len(samples) <= limit:
        return samples
    step = len(samples) / limit
    picked = [samples[int(index * step)] for index in range(limit - 1)]
    picked.append(samples[-1])
    return picked


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return quantize_money(Decimal(str(value)))
    except Exception:
        return None


def _percent(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return quantize_percent(Decimal(str(value)))
    except Exception:
        return None


__all__ = [
    "MAX_STORED_EQUITY_POINTS",
    "BacktestRepository",
    "BacktestRunRepository",
    "BacktestService",
]
