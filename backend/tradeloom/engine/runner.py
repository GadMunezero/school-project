"""The simulation loop.

Per-bar ordering (the same ordering the replay session uses, which is what makes replay and
backtest agree):

1. ``broker.open_bar`` — match resting orders against the new bar.
2. dispatch position lifecycle callbacks for anything that changed.
3. ``strategy.on_bar`` — the strategy sees the completed bar and may raise signals.
4. ``strategy.risk_management`` while a position is open.
5. drain signals through the risk manager into orders.
6. ``broker.close_bar`` — apply the execution model to newly submitted orders.
7. record an equity sample.

After the final bar, any open position is closed at that bar's close and flagged
``end_of_data`` — leaving it open would silently omit its P&L from every metric.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from tradeloom.core.enums import Direction, OrderSide, OrderType
from tradeloom.core.money import ZERO, quantize_money
from tradeloom.core.timeutil import trading_day
from tradeloom.engine.bars import BarSeries, BarWindow
from tradeloom.engine.broker import BrokerSimulator
from tradeloom.engine.config import BacktestConfig
from tradeloom.engine.events import OrderIntent, SignalDirection, SignalEvent
from tradeloom.engine.orders import SimOrder
from tradeloom.engine.performance import EquitySample, PerformanceAnalyzer, PerformanceReport
from tradeloom.engine.portfolio import Portfolio, SimPosition
from tradeloom.engine.risk import RiskManager
from tradeloom.engine.strategy import Strategy, StrategyContext
from tradeloom.engine.version import ENGINE_VERSION

ProgressCallback = Callable[[int, int], None]


@dataclass(slots=True)
class BacktestResult:
    config: BacktestConfig
    report: PerformanceReport
    equity_curve: list[EquitySample]
    orders: list[SimOrder]
    portfolio: Portfolio
    bars_processed: int
    warnings: list[str] = field(default_factory=list)
    engine_version: str = ENGINE_VERSION
    input_digest: str = ""

    @property
    def trades(self):  # type: ignore[no-untyped-def]
        return self.portfolio.closed_trades


class BacktestRunner:
    def __init__(
        self,
        *,
        config: BacktestConfig,
        strategy: Strategy,
        bars: BarSeries,
        progress: ProgressCallback | None = None,
        progress_interval: int = 500,
    ) -> None:
        self.config = config
        self.strategy = strategy
        self.bars = bars
        self.progress = progress
        self.progress_interval = max(1, progress_interval)

        self.portfolio = Portfolio(
            initial_capital=config.initial_capital,
            contract_multiplier=config.contract_multiplier,
            leverage=config.risk.leverage,
        )
        self.broker = BrokerSimulator(config=config, portfolio=self.portfolio)
        self.risk_manager = RiskManager(config)

        self.equity_curve: list[EquitySample] = []
        self.warnings: list[str] = []
        #: order id -> (stop, target) to attach once the entry order fills.
        self._pending_protection: dict[int, tuple[Decimal | None, Decimal | None]] = {}
        self._rejection_counts: dict[str, int] = {}

    # ------------------------------------------------------------------

    def run(self) -> BacktestResult:
        if len(self.bars) == 0:
            return self._empty_result("no market data available for the requested range")

        self.strategy.initialize(self.config)
        window = BarWindow(self.bars, 0)
        context: StrategyContext | None = None

        for index, bar in enumerate(self.bars):
            had_position = self.portfolio.position is not None
            previous_quantity = (
                self.portfolio.position.quantity if self.portfolio.position else ZERO
            )

            fills = self.broker.open_bar(bar, index)

            window._advance(index)
            context = StrategyContext(
                config=self.config,
                bar=bar,
                index=index,
                history=window,  # type: ignore[arg-type]
                portfolio=self.portfolio,
            )

            for fill in fills:
                self.strategy.on_order_fill(context, fill.order_id, fill.price)
                self._attach_pending_protection(fill.order_id)

            self._dispatch_position_events(context, had_position, previous_quantity)

            if self.config.session.close_at_session_end and self.config.session.is_after_session(
                bar.opened_at
            ):
                self.broker.close_position_at(bar.close, bar.opened_at, "session_end")

            self.strategy.on_bar(context)
            if self.portfolio.position is not None:
                self.strategy.risk_management(context)

            extra = self.strategy.generate_signal(context)
            if extra is not None:
                context.signals.append(extra)

            self._process_signals(context)
            for fill in self.broker.close_bar(bar):
                self.strategy.on_order_fill(context, fill.order_id, fill.price)
                self._attach_pending_protection(fill.order_id)

            self._record_equity(bar)

            for message in context.warnings:
                if message not in self.warnings:
                    self.warnings.append(message)

            if self.progress and (index % self.progress_interval == 0):
                self.progress(index + 1, len(self.bars))

        last_bar = self.bars[-1]
        if self.portfolio.position is not None:
            self.broker.close_position_at(last_bar.close, last_bar.opened_at, "end_of_data")
            self._record_equity(last_bar, replace_last=True)
            self.warnings.append(
                "An open position was closed at the final bar's close so its P&L is included."
            )

        self.strategy.finalize(context)
        self._summarise_rejections()

        if self.progress:
            self.progress(len(self.bars), len(self.bars))

        analyzer = PerformanceAnalyzer(
            trades=self.portfolio.closed_trades,
            equity_curve=self.equity_curve,
            initial_capital=self.config.initial_capital,
            periods_per_year=self.config.periods_per_year,
            risk_free_rate_percent=self.config.risk_free_rate_percent,
            bars_in_market=self.portfolio.bars_in_market,
            total_bars=self.portfolio.total_bars,
            timezone=self.config.session.timezone,
            symbol=self.config.symbol,
        )
        return BacktestResult(
            config=self.config,
            report=analyzer.analyse(),
            equity_curve=self.equity_curve,
            orders=self.broker.order_log,
            portfolio=self.portfolio,
            bars_processed=len(self.bars),
            warnings=self.warnings,
            input_digest=self.input_digest(),
        )

    # ------------------------------------------------------------------

    def _process_signals(self, context: StrategyContext) -> None:
        for signal in context.signals:
            if signal.metadata.get("action") == "adjust_stop":
                self.broker.attach_protection(
                    stop_loss=signal.stop_loss,
                    take_profit=(
                        self.portfolio.position.take_profit if self.portfolio.position else None
                    ),
                )
                continue
            self._handle_signal(signal)
        context.signals.clear()

    def _handle_signal(self, signal: SignalEvent) -> None:
        bar = self.broker._current_bar
        if bar is None:  # pragma: no cover - only possible before the first bar
            return

        # Sizing always references the signal bar's close. The *fill* price depends on the
        # execution model, but the size must be decided from information available right now.
        reference_price = bar.close
        decision = self.risk_manager.evaluate(
            signal,
            portfolio=self.portfolio,
            reference_price=reference_price,
            bars_since_exit=self.broker.bars_since_last_exit(),
            timestamp=bar.opened_at,
        )
        if not decision.allowed or decision.quantity <= 0:
            if decision.reason:
                self._rejection_counts[decision.reason] = (
                    self._rejection_counts.get(decision.reason, 0) + 1
                )
            return

        if signal.direction in (
            SignalDirection.EXIT_LONG,
            SignalDirection.EXIT_SHORT,
            SignalDirection.CLOSE_ALL,
        ):
            position = self.portfolio.position
            if position is None:
                return
            self.broker.cancel_protective_orders("closing")
            self.broker.submit(
                SimOrder(
                    side=OrderSide.SELL if position.direction is Direction.LONG else OrderSide.BUY,
                    quantity=decision.quantity,
                    order_type=OrderType.MARKET,
                    intent=OrderIntent.CLOSE,
                    tag=signal.reason,
                ),
                signal_timestamp=signal.timestamp,
            )
            return

        side = OrderSide.BUY if signal.direction is SignalDirection.ENTER_LONG else OrderSide.SELL
        order = self.broker.submit(
            SimOrder(
                side=side,
                quantity=decision.quantity,
                order_type=OrderType.MARKET,
                intent=OrderIntent.OPEN,
                tag=signal.reason,
            ),
            signal_timestamp=signal.timestamp,
        )
        if signal.stop_loss is not None or signal.take_profit is not None:
            self._pending_protection[order.id] = (signal.stop_loss, signal.take_profit)

    def _attach_pending_protection(self, order_id: int) -> None:
        protection = self._pending_protection.pop(order_id, None)
        if protection is None or self.portfolio.position is None:
            return
        stop, target = protection
        self.broker.attach_protection(stop_loss=stop, take_profit=target)

    def _dispatch_position_events(
        self, context: StrategyContext, had_position: bool, previous_quantity: Decimal
    ) -> None:
        position: SimPosition | None = self.portfolio.position
        if position is not None and not had_position:
            self.strategy.on_position_open(context, position)
        elif position is not None and position.quantity != previous_quantity:
            self.strategy.on_position_update(context, position)
        elif position is None and had_position:
            self.strategy.on_position_close(context)

    def _record_equity(self, bar, replace_last: bool = False) -> None:  # type: ignore[no-untyped-def]
        position = self.portfolio.position
        sample = EquitySample(
            timestamp=bar.opened_at,
            # Daily and monthly returns bucket on this. A futures bar at 19:00 New York belongs to
            # the next session, so grouping it by calendar date would split one trading day across
            # two rows and report a return for a day that had already closed.
            trading_day=trading_day(
                bar.opened_at, self.config.asset_type, self.config.session.timezone
            ),
            equity=quantize_money(self.portfolio.equity(bar.close)),
            cash=self.portfolio.cash(),
            realized_pnl=self.portfolio.realized_pnl,
            unrealized_pnl=(position.unrealized(bar.close) if position is not None else ZERO),
            open_positions=1 if position is not None else 0,
            exposure=self.portfolio.exposure(bar.close),
        )
        if replace_last and self.equity_curve:
            self.equity_curve[-1] = sample
        else:
            self.equity_curve.append(sample)

    def _summarise_rejections(self) -> None:
        """Surface why signals were dropped, so an empty backtest is explainable."""
        labels = {
            "position_already_open": "signals ignored because a position was already open",
            "max_concurrent_positions_reached": "signals ignored at the concurrent-position limit",
            "cooldown_active": "signals ignored during the post-trade cooldown",
            "outside_trading_session": "signals ignored outside the trading session",
            "insufficient_buying_power": "orders rejected for insufficient buying power",
            "size_rounded_to_zero": "signals dropped because the computed size rounded to zero",
            "risk_exceeds_per_trade_limit": "signals dropped for exceeding the per-trade risk cap",
        }
        for reason, count in sorted(self._rejection_counts.items()):
            self.warnings.append(f"{count} {labels.get(reason, reason)}.")

    def _empty_result(self, message: str) -> BacktestResult:
        self.warnings.append(message)
        analyzer = PerformanceAnalyzer(
            trades=[],
            equity_curve=[],
            initial_capital=self.config.initial_capital,
            periods_per_year=self.config.periods_per_year,
            symbol=self.config.symbol,
        )
        return BacktestResult(
            config=self.config,
            report=analyzer.analyse(),
            equity_curve=[],
            orders=[],
            portfolio=self.portfolio,
            bars_processed=0,
            warnings=self.warnings,
            input_digest=self.input_digest(),
        )

    # ------------------------------------------------------------------

    def input_digest(self) -> str:
        """SHA-256 over engine version, configuration and data fingerprint.

        Two runs with the same digest are guaranteed to have consumed identical inputs, which is
        what lets the UI say "this result is reproducible" without re-running anything.
        """
        payload: dict[str, Any] = {
            "engine": ENGINE_VERSION,
            "strategy": self.strategy.key,
            "params": {k: str(v) for k, v in sorted(self.strategy.params.items())},
            "config": self.config.to_dict(),
            "data": self.bars.digest_source(),
        }
        serialised = repr(sorted(payload.items()))
        return hashlib.sha256(serialised.encode("utf-8")).hexdigest()


__all__ = ["BacktestResult", "BacktestRunner", "ProgressCallback"]
