"""Risk manager: turns signals into correctly sized, permitted orders.

Every entry passes through :meth:`RiskManager.evaluate`, which answers two questions in order:

1. **Is this trade allowed?** — concurrency limit, pyramiding rule, cooldown, session window.
2. **How big?** — according to the configured sizing model, then clamped by the maximum position
   size, the account's buying power, and the per-trade risk ceiling.

A rejection is never silent: the reason is returned and recorded on the run's warnings, so a
backtest with suspiciously few trades can be explained rather than guessed at.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from tradeloom.core.enums import Direction, PositionSizingType
from tradeloom.core.money import ONE, ZERO, mul, quantize_money, safe_div
from tradeloom.core.money import quantize_quantity as qq
from tradeloom.engine.config import BacktestConfig
from tradeloom.engine.events import SignalDirection, SignalEvent
from tradeloom.engine.portfolio import Portfolio


@dataclass(frozen=True, slots=True)
class SizingDecision:
    quantity: Decimal
    allowed: bool
    reason: str | None = None
    risk_amount: Decimal | None = None


class RiskManager:
    def __init__(self, config: BacktestConfig) -> None:
        self.config = config
        self.risk = config.risk

    # ------------------------------------------------------------------

    def evaluate(
        self,
        signal: SignalEvent,
        *,
        portfolio: Portfolio,
        reference_price: Decimal,
        bars_since_exit: int | None,
        timestamp,
    ) -> SizingDecision:
        if signal.direction in (
            SignalDirection.EXIT_LONG,
            SignalDirection.EXIT_SHORT,
            SignalDirection.CLOSE_ALL,
        ):
            position = portfolio.position
            quantity = position.quantity if position else ZERO
            return SizingDecision(quantity=quantity, allowed=quantity > 0)

        blocked = self._entry_blocked(portfolio, bars_since_exit, timestamp)
        if blocked is not None:
            return SizingDecision(ZERO, False, blocked)

        quantity, risk_amount = self._size(signal, portfolio, reference_price)
        quantity = self._apply_caps(quantity, portfolio, reference_price)

        if quantity <= 0:
            return SizingDecision(ZERO, False, "size_rounded_to_zero", risk_amount)

        if self.risk.max_risk_percent_per_trade > 0 and risk_amount is not None:
            equity = portfolio.equity(reference_price)
            ceiling = quantize_money(
                mul(equity, self.risk.max_risk_percent_per_trade) / Decimal(100)
            )
            if ceiling > 0 and risk_amount > ceiling:
                return SizingDecision(ZERO, False, "risk_exceeds_per_trade_limit", risk_amount)

        return SizingDecision(quantity, True, None, risk_amount)

    # ------------------------------------------------------------------

    def _entry_blocked(
        self, portfolio: Portfolio, bars_since_exit: int | None, timestamp
    ) -> str | None:
        if not self.config.session.allows_entry(timestamp):
            return "outside_trading_session"

        position = portfolio.position
        if position is not None:
            if not self.risk.allow_pyramiding:
                return "position_already_open"
            if position.entry_count >= self.risk.max_concurrent_positions:
                return "max_concurrent_positions_reached"

        if (
            self.risk.cooldown_bars > 0
            and bars_since_exit is not None
            and bars_since_exit < self.risk.cooldown_bars
        ):
            return "cooldown_active"
        return None

    def _size(
        self, signal: SignalEvent, portfolio: Portfolio, price: Decimal
    ) -> tuple[Decimal, Decimal | None]:
        if signal.quantity is not None:
            quantity = qq(signal.quantity)
            return quantity, self._risk_for(signal, quantity, price)

        equity = portfolio.equity(price)
        multiplier = self.config.contract_multiplier
        strength = max(ZERO, min(ONE, signal.strength))
        sizing = self.risk.sizing
        value = self.risk.value

        if sizing is PositionSizingType.FIXED_QUANTITY:
            quantity = mul(value, strength)

        elif sizing is PositionSizingType.FIXED_NOTIONAL:
            denominator = mul(price, multiplier)
            raw = safe_div(mul(value, strength), denominator)
            quantity = raw if raw is not None else ZERO

        elif sizing is PositionSizingType.PERCENT_OF_EQUITY:
            notional = mul(equity, value) / Decimal(100)
            denominator = mul(price, multiplier)
            raw = safe_div(mul(notional, strength), denominator)
            quantity = raw if raw is not None else ZERO

        elif sizing in (PositionSizingType.FIXED_RISK_AMOUNT, PositionSizingType.PERCENT_RISK):
            budget = (
                value
                if sizing is PositionSizingType.FIXED_RISK_AMOUNT
                else quantize_money(mul(equity, value) / Decimal(100))
            )
            budget = mul(budget, strength)
            per_unit = self._risk_per_unit(signal, price)
            if per_unit is None or per_unit <= 0:
                # Risk-based sizing needs a stop. Without one there is no defensible size, so the
                # signal is rejected rather than silently sized by some other rule.
                return ZERO, None
            raw = safe_div(budget, mul(per_unit, multiplier))
            quantity = raw if raw is not None else ZERO
        else:  # pragma: no cover - enum is exhaustive
            quantity = ZERO

        quantity = self._round_to_lot(quantity)
        return quantity, self._risk_for(signal, quantity, price)

    def _risk_per_unit(self, signal: SignalEvent, price: Decimal) -> Decimal | None:
        if signal.stop_loss is None:
            return None
        direction = (
            Direction.LONG if signal.direction is SignalDirection.ENTER_LONG else Direction.SHORT
        )
        distance = (price - signal.stop_loss) * Decimal(direction.sign)
        return distance if distance > 0 else None

    def _risk_for(self, signal: SignalEvent, quantity: Decimal, price: Decimal) -> Decimal | None:
        per_unit = self._risk_per_unit(signal, price)
        if per_unit is None or quantity <= 0:
            return None
        return quantize_money(mul(mul(per_unit, quantity), self.config.contract_multiplier))

    def _round_to_lot(self, quantity: Decimal) -> Decimal:
        """Floor to a tradable increment. Rounding *down* never over-risks the account."""
        lot = self.config.lot_size
        if lot <= 0:
            return qq(quantity)
        if not self.config.allow_fractional and lot < ONE:
            lot = ONE
        units = (quantity / lot).to_integral_value(rounding="ROUND_FLOOR")
        return qq(mul(units, lot))

    def _apply_caps(self, quantity: Decimal, portfolio: Portfolio, price: Decimal) -> Decimal:
        if quantity <= 0:
            return ZERO
        if self.risk.max_position_quantity is not None:
            existing = portfolio.position.quantity if portfolio.position else ZERO
            headroom = qq(self.risk.max_position_quantity - existing)
            quantity = min(quantity, max(headroom, ZERO))

        buying_power = portfolio.buying_power(price)
        unit_cost = mul(price, self.config.contract_multiplier)
        if unit_cost > 0:
            affordable = safe_div(buying_power, unit_cost)
            if affordable is not None:
                quantity = min(quantity, max(affordable, ZERO))

        return self._round_to_lot(quantity)


__all__ = ["RiskManager", "SizingDecision"]
