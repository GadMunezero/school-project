"""EMA crossover.

Long when the fast EMA crosses above the slow EMA, flat (or short, when enabled) on the opposite
cross. The stop is placed a multiple of ATR below entry, which is what makes risk-percent sizing
meaningful — without a stop there is no defensible position size.
"""

from __future__ import annotations

from decimal import Decimal

from tradeloom.core.enums import ParameterType
from tradeloom.core.money import mul
from tradeloom.engine.config import BacktestConfig
from tradeloom.engine.indicators import ATR, EMA
from tradeloom.engine.strategy import Strategy, StrategyContext, StrategyParameter


class EmaCrossStrategy(Strategy):
    key = "ema_cross"
    name = "EMA crossover"
    description = (
        "Enters when the fast EMA crosses the slow EMA and exits on the opposite cross. "
        "Stops and targets are scaled by ATR."
    )
    category = "trend"

    parameters = (
        StrategyParameter(
            name="fast_period",
            param_type=ParameterType.INTEGER,
            default=12,
            minimum=Decimal(2),
            maximum=Decimal(200),
            step=Decimal(1),
            description="Bars in the fast EMA.",
        ),
        StrategyParameter(
            name="slow_period",
            param_type=ParameterType.INTEGER,
            default=26,
            minimum=Decimal(3),
            maximum=Decimal(400),
            step=Decimal(1),
            description="Bars in the slow EMA. Must exceed the fast period.",
        ),
        StrategyParameter(
            name="atr_period",
            param_type=ParameterType.INTEGER,
            default=14,
            minimum=Decimal(2),
            maximum=Decimal(100),
            step=Decimal(1),
            description="Bars in the ATR used to size the stop.",
        ),
        StrategyParameter(
            name="stop_atr_multiple",
            param_type=ParameterType.DECIMAL,
            default=Decimal("2.0"),
            minimum=Decimal("0.1"),
            maximum=Decimal("10"),
            step=Decimal("0.1"),
            description="Stop distance as a multiple of ATR.",
        ),
        StrategyParameter(
            name="target_atr_multiple",
            param_type=ParameterType.DECIMAL,
            default=Decimal("4.0"),
            minimum=Decimal("0"),
            maximum=Decimal("30"),
            step=Decimal("0.1"),
            description="Target distance as a multiple of ATR. 0 disables the target.",
        ),
        StrategyParameter(
            name="allow_short",
            param_type=ParameterType.BOOLEAN,
            default=True,
            description="Take short trades on the bearish cross.",
        ),
    )

    def initialize(self, config: BacktestConfig) -> None:
        fast = int(self.params["fast_period"])
        slow = int(self.params["slow_period"])
        if fast >= slow:
            # Caught before a job is queued, but re-checked here so the engine is safe standalone.
            raise ValueError("fast_period must be smaller than slow_period")
        self.fast = EMA(fast)
        self.slow = EMA(slow)
        self.atr = ATR(int(self.params["atr_period"]))
        self._previous_spread: Decimal | None = None

    def on_bar(self, ctx: StrategyContext) -> None:
        fast = self.fast.update(ctx.bar)
        slow = self.slow.update(ctx.bar)
        atr = self.atr.update(ctx.bar)

        if fast is None or slow is None or atr is None:
            return

        spread = fast - slow
        previous = self._previous_spread
        self._previous_spread = spread
        if previous is None:
            return

        crossed_up = previous <= 0 < spread
        crossed_down = previous >= 0 > spread
        if not crossed_up and not crossed_down:
            return

        if not ctx.is_flat:
            # The cross is also the exit signal for an opposing position.
            if (crossed_down and ctx.is_long) or (crossed_up and ctx.is_short):
                ctx.close_position(reason="ema_cross_exit")
            return

        stop_distance = mul(atr, self.params["stop_atr_multiple"])
        target_multiple = self.params["target_atr_multiple"]
        if stop_distance <= 0:
            return

        close = ctx.bar.close
        if crossed_up:
            ctx.enter_long(
                stop_loss=close - stop_distance,
                take_profit=close + mul(atr, target_multiple) if target_multiple > 0 else None,
                reason="ema_cross_up",
            )
        elif crossed_down and bool(self.params["allow_short"]):
            ctx.enter_short(
                stop_loss=close + stop_distance,
                take_profit=close - mul(atr, target_multiple) if target_multiple > 0 else None,
                reason="ema_cross_down",
            )


__all__ = ["EmaCrossStrategy"]
