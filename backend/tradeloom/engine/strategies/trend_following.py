"""Trend following with a chandelier trailing stop.

Enters in the direction of a long-term moving average once a shorter-term average agrees, then
manages the position with a trailing stop anchored to the highest close since entry. This is the
only built-in that modifies its stop while in a trade, so it also exercises the engine's
stop-replacement path.
"""

from __future__ import annotations

from decimal import Decimal

from tradeloom.core.enums import ParameterType
from tradeloom.core.money import mul
from tradeloom.engine.config import BacktestConfig
from tradeloom.engine.indicators import ATR, EMA
from tradeloom.engine.strategy import Strategy, StrategyContext, StrategyParameter


class TrendFollowingStrategy(Strategy):
    key = "trend_following"
    name = "Trend following"
    description = (
        "Trades in the direction of a long-term EMA once a short-term EMA confirms, managed "
        "with an ATR chandelier trailing stop."
    )
    category = "trend"

    parameters = (
        StrategyParameter(
            name="trend_period",
            param_type=ParameterType.INTEGER,
            default=100,
            minimum=Decimal(10),
            maximum=Decimal(500),
            step=Decimal(1),
            description="Bars in the long-term trend EMA.",
        ),
        StrategyParameter(
            name="signal_period",
            param_type=ParameterType.INTEGER,
            default=20,
            minimum=Decimal(2),
            maximum=Decimal(200),
            step=Decimal(1),
            description="Bars in the short-term confirmation EMA.",
        ),
        StrategyParameter(
            name="atr_period",
            param_type=ParameterType.INTEGER,
            default=14,
            minimum=Decimal(2),
            maximum=Decimal(100),
            step=Decimal(1),
            description="Bars in the ATR used for the trailing stop.",
        ),
        StrategyParameter(
            name="trail_atr_multiple",
            param_type=ParameterType.DECIMAL,
            default=Decimal("3.0"),
            minimum=Decimal("0.5"),
            maximum=Decimal("15"),
            step=Decimal("0.1"),
            description="Trailing stop distance as a multiple of ATR.",
        ),
        StrategyParameter(
            name="allow_short",
            param_type=ParameterType.BOOLEAN,
            default=True,
            description="Take short trades when the trend is down.",
        ),
    )

    def initialize(self, config: BacktestConfig) -> None:
        self.trend = EMA(int(self.params["trend_period"]))
        self.signal = EMA(int(self.params["signal_period"]))
        self.atr = ATR(int(self.params["atr_period"]))
        self._anchor: Decimal | None = None

    def on_bar(self, ctx: StrategyContext) -> None:
        trend = self.trend.update(ctx.bar)
        signal = self.signal.update(ctx.bar)
        atr = self.atr.update(ctx.bar)
        if trend is None or signal is None or atr is None:
            return

        close = ctx.bar.close
        trail = mul(atr, self.params["trail_atr_multiple"])

        if not ctx.is_flat:
            self.risk_management(ctx)
            return

        self._anchor = None
        if close > trend and signal > trend:
            ctx.enter_long(stop_loss=close - trail, reason="trend_long")
        elif close < trend and signal < trend and bool(self.params["allow_short"]):
            ctx.enter_short(stop_loss=close + trail, reason="trend_short")

    def risk_management(self, ctx: StrategyContext) -> None:
        """Ratchet the stop in the direction of the trade; it never moves backwards."""
        position = ctx.position
        atr = self.atr.value
        if position is None or atr is None:
            return

        trail = mul(atr, self.params["trail_atr_multiple"])
        close = ctx.bar.close

        if position.direction.value == "long":
            self._anchor = close if self._anchor is None else max(self._anchor, close)
            candidate = self._anchor - trail
            if position.stop_loss is None or candidate > position.stop_loss:
                ctx.signals.append(_trail_signal(ctx, candidate))
        else:
            self._anchor = close if self._anchor is None else min(self._anchor, close)
            candidate = self._anchor + trail
            if position.stop_loss is None or candidate < position.stop_loss:
                ctx.signals.append(_trail_signal(ctx, candidate))

    def on_position_close(self, ctx: StrategyContext) -> None:
        self._anchor = None


def _trail_signal(ctx: StrategyContext, stop: Decimal):  # type: ignore[no-untyped-def]
    """A stop-adjustment request. The runner recognises it by its metadata marker."""
    from tradeloom.engine.events import SignalDirection, SignalEvent

    return SignalEvent(
        timestamp=ctx.timestamp,
        direction=SignalDirection.CLOSE_ALL,
        stop_loss=stop,
        reason="trail_stop",
        metadata={"action": "adjust_stop"},
    )


__all__ = ["TrendFollowingStrategy"]
