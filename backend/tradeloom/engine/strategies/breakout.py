"""Donchian-style breakout.

Enters on a close beyond the highest high (or lowest low) of the preceding N bars. The rolling
extremes deliberately **exclude the current bar** — comparing a bar's close against a window that
already contains its own high is the classic way to build a breakout system that never triggers,
or that triggers on information it could not have had.
"""

from __future__ import annotations

from decimal import Decimal

from tradeloom.core.enums import ParameterType
from tradeloom.core.money import mul
from tradeloom.engine.config import BacktestConfig
from tradeloom.engine.indicators import ATR, RollingHigh, RollingLow
from tradeloom.engine.strategy import Strategy, StrategyContext, StrategyParameter


class BreakoutStrategy(Strategy):
    key = "breakout"
    name = "Range breakout"
    description = (
        "Buys a close above the prior N-bar high and sells a close below the prior N-bar low, "
        "with an ATR stop and an opposite-extreme trailing exit."
    )
    category = "breakout"

    parameters = (
        StrategyParameter(
            name="entry_lookback",
            param_type=ParameterType.INTEGER,
            default=20,
            minimum=Decimal(2),
            maximum=Decimal(300),
            step=Decimal(1),
            description="Bars in the breakout window.",
        ),
        StrategyParameter(
            name="exit_lookback",
            param_type=ParameterType.INTEGER,
            default=10,
            minimum=Decimal(1),
            maximum=Decimal(300),
            step=Decimal(1),
            description="Bars in the opposite-extreme trailing exit window.",
        ),
        StrategyParameter(
            name="stop_atr_multiple",
            param_type=ParameterType.DECIMAL,
            default=Decimal("2.0"),
            minimum=Decimal("0.1"),
            maximum=Decimal("10"),
            step=Decimal("0.1"),
            description="Initial stop distance as a multiple of ATR.",
        ),
        StrategyParameter(
            name="allow_short",
            param_type=ParameterType.BOOLEAN,
            default=True,
            description="Take downside breakouts as short trades.",
        ),
    )

    def initialize(self, config: BacktestConfig) -> None:
        entry = int(self.params["entry_lookback"])
        exit_window = int(self.params["exit_lookback"])
        self.entry_high = RollingHigh(entry, exclude_current=True)
        self.entry_low = RollingLow(entry, exclude_current=True)
        self.exit_high = RollingHigh(exit_window, exclude_current=True)
        self.exit_low = RollingLow(exit_window, exclude_current=True)
        self.atr = ATR(14)

    def on_bar(self, ctx: StrategyContext) -> None:
        high = self.entry_high.update(ctx.bar)
        low = self.entry_low.update(ctx.bar)
        exit_high = self.exit_high.update(ctx.bar)
        exit_low = self.exit_low.update(ctx.bar)
        atr = self.atr.update(ctx.bar)

        if high is None or low is None or atr is None:
            return

        close = ctx.bar.close

        if not ctx.is_flat:
            if (ctx.is_long and exit_low is not None and close < exit_low) or (
                ctx.is_short and exit_high is not None and close > exit_high
            ):
                ctx.close_position(reason="trailing_exit")
            return

        stop_distance = mul(atr, self.params["stop_atr_multiple"])
        if stop_distance <= 0:
            return

        if close > high:
            ctx.enter_long(stop_loss=close - stop_distance, reason="breakout_up")
        elif close < low and bool(self.params["allow_short"]):
            ctx.enter_short(stop_loss=close + stop_distance, reason="breakout_down")


__all__ = ["BreakoutStrategy"]
