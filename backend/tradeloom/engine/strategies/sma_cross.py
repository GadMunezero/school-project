"""SMA crossover with an optional trend filter.

Structurally similar to the EMA variant but with a percentage stop rather than an ATR stop, so
the two can be compared to see how much of a result comes from the stop model rather than the
signal.
"""

from __future__ import annotations

from decimal import Decimal

from tradeloom.core.enums import ParameterType
from tradeloom.core.money import mul
from tradeloom.engine.config import BacktestConfig
from tradeloom.engine.indicators import SMA
from tradeloom.engine.strategy import Strategy, StrategyContext, StrategyParameter


class SmaCrossStrategy(Strategy):
    key = "sma_cross"
    name = "SMA crossover"
    description = (
        "Classic moving-average crossover with a percentage stop and an optional "
        "long-term trend filter."
    )
    category = "trend"

    parameters = (
        StrategyParameter(
            name="fast_period",
            param_type=ParameterType.INTEGER,
            default=20,
            minimum=Decimal(2),
            maximum=Decimal(200),
            step=Decimal(1),
            description="Bars in the fast SMA.",
        ),
        StrategyParameter(
            name="slow_period",
            param_type=ParameterType.INTEGER,
            default=50,
            minimum=Decimal(3),
            maximum=Decimal(400),
            step=Decimal(1),
            description="Bars in the slow SMA.",
        ),
        StrategyParameter(
            name="trend_period",
            param_type=ParameterType.INTEGER,
            default=200,
            minimum=Decimal(0),
            maximum=Decimal(1000),
            step=Decimal(1),
            description="Bars in the trend filter SMA. 0 disables the filter.",
        ),
        StrategyParameter(
            name="stop_percent",
            param_type=ParameterType.DECIMAL,
            default=Decimal("2.0"),
            minimum=Decimal("0.05"),
            maximum=Decimal("50"),
            step=Decimal("0.05"),
            description="Stop distance as a percentage of the entry price.",
        ),
        StrategyParameter(
            name="reward_risk",
            param_type=ParameterType.DECIMAL,
            default=Decimal("2.0"),
            minimum=Decimal("0"),
            maximum=Decimal("20"),
            step=Decimal("0.1"),
            description="Target distance as a multiple of the stop distance. 0 disables it.",
        ),
        StrategyParameter(
            name="allow_short",
            param_type=ParameterType.BOOLEAN,
            default=False,
            description="Take short trades on the bearish cross.",
        ),
    )

    def initialize(self, config: BacktestConfig) -> None:
        fast = int(self.params["fast_period"])
        slow = int(self.params["slow_period"])
        if fast >= slow:
            raise ValueError("fast_period must be smaller than slow_period")
        self.fast = SMA(fast)
        self.slow = SMA(slow)
        trend_period = int(self.params["trend_period"])
        self.trend = SMA(trend_period) if trend_period > 0 else None
        self._previous_spread: Decimal | None = None

    def on_bar(self, ctx: StrategyContext) -> None:
        fast = self.fast.update(ctx.bar)
        slow = self.slow.update(ctx.bar)
        trend = self.trend.update(ctx.bar) if self.trend is not None else None

        if fast is None or slow is None:
            return
        if self.trend is not None and trend is None:
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

        close = ctx.bar.close
        if not ctx.is_flat:
            if (crossed_down and ctx.is_long) or (crossed_up and ctx.is_short):
                ctx.close_position(reason="sma_cross_exit")
            return

        stop_distance = mul(close, self.params["stop_percent"]) / Decimal(100)
        if stop_distance <= 0:
            return
        reward = self.params["reward_risk"]

        if crossed_up and (trend is None or close > trend):
            ctx.enter_long(
                stop_loss=close - stop_distance,
                take_profit=close + mul(stop_distance, reward) if reward > 0 else None,
                reason="sma_cross_up",
            )
        elif crossed_down and bool(self.params["allow_short"]) and (trend is None or close < trend):
            ctx.enter_short(
                stop_loss=close + stop_distance,
                take_profit=close - mul(stop_distance, reward) if reward > 0 else None,
                reason="sma_cross_down",
            )


__all__ = ["SmaCrossStrategy"]
