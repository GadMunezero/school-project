"""RSI mean reversion.

Buys oversold readings and exits on a return to the midline or a time stop. Mean-reversion
systems live or die on their exits, so this one exposes both a level exit and a maximum holding
period.
"""

from __future__ import annotations

from decimal import Decimal

from tradeloom.core.enums import ParameterType
from tradeloom.core.money import mul
from tradeloom.engine.config import BacktestConfig
from tradeloom.engine.indicators import ATR, RSI
from tradeloom.engine.strategy import Strategy, StrategyContext, StrategyParameter


class RsiMeanReversionStrategy(Strategy):
    key = "rsi_reversion"
    name = "RSI mean reversion"
    description = (
        "Buys oversold RSI readings (and optionally shorts overbought ones), exiting on a "
        "return to the midline, an ATR stop, or a maximum holding period."
    )
    category = "mean_reversion"

    parameters = (
        StrategyParameter(
            name="rsi_period",
            param_type=ParameterType.INTEGER,
            default=14,
            minimum=Decimal(2),
            maximum=Decimal(100),
            step=Decimal(1),
            description="Bars in the RSI calculation.",
        ),
        StrategyParameter(
            name="oversold",
            param_type=ParameterType.DECIMAL,
            default=Decimal(30),
            minimum=Decimal(1),
            maximum=Decimal(49),
            step=Decimal(1),
            description="RSI level treated as oversold.",
        ),
        StrategyParameter(
            name="overbought",
            param_type=ParameterType.DECIMAL,
            default=Decimal(70),
            minimum=Decimal(51),
            maximum=Decimal(99),
            step=Decimal(1),
            description="RSI level treated as overbought.",
        ),
        StrategyParameter(
            name="exit_level",
            param_type=ParameterType.DECIMAL,
            default=Decimal(50),
            minimum=Decimal(5),
            maximum=Decimal(95),
            step=Decimal(1),
            description="RSI level at which an open position is closed.",
        ),
        StrategyParameter(
            name="stop_atr_multiple",
            param_type=ParameterType.DECIMAL,
            default=Decimal("2.5"),
            minimum=Decimal("0.1"),
            maximum=Decimal("10"),
            step=Decimal("0.1"),
            description="Stop distance as a multiple of ATR.",
        ),
        StrategyParameter(
            name="max_holding_bars",
            param_type=ParameterType.INTEGER,
            default=20,
            minimum=Decimal(0),
            maximum=Decimal(500),
            step=Decimal(1),
            description="Force an exit after this many bars. 0 disables the time stop.",
        ),
        StrategyParameter(
            name="allow_short",
            param_type=ParameterType.BOOLEAN,
            default=True,
            description="Short overbought readings as well as buying oversold ones.",
        ),
    )

    def initialize(self, config: BacktestConfig) -> None:
        self.rsi = RSI(int(self.params["rsi_period"]))
        self.atr = ATR(14)
        self._bars_held = 0

    def on_bar(self, ctx: StrategyContext) -> None:
        rsi = self.rsi.update(ctx.bar)
        atr = self.atr.update(ctx.bar)
        if rsi is None or atr is None:
            return

        exit_level = self.params["exit_level"]
        max_holding = int(self.params["max_holding_bars"])

        if not ctx.is_flat:
            self._bars_held += 1
            if max_holding and self._bars_held >= max_holding:
                ctx.close_position(reason="time_stop")
                return
            if (ctx.is_long and rsi >= exit_level) or (ctx.is_short and rsi <= exit_level):
                ctx.close_position(reason="rsi_midline")
            return

        self._bars_held = 0
        stop_distance = mul(atr, self.params["stop_atr_multiple"])
        if stop_distance <= 0:
            return

        close = ctx.bar.close
        if rsi <= self.params["oversold"]:
            ctx.enter_long(stop_loss=close - stop_distance, reason="rsi_oversold")
        elif rsi >= self.params["overbought"] and bool(self.params["allow_short"]):
            ctx.enter_short(stop_loss=close + stop_distance, reason="rsi_overbought")

    def on_position_close(self, ctx: StrategyContext) -> None:
        self._bars_held = 0


__all__ = ["RsiMeanReversionStrategy"]
