"""Simulated position and portfolio accounting.

Capital model
-------------
Tradeloom simulates a **margin account**::

    equity        = initial_capital + realised P&L + unrealised P&L
    exposure      = |quantity| * price * contract_multiplier
    buying power  = equity * leverage - exposure

Cash is not decremented by the full notional on entry, because a leveraged account does not pay
the full notional. This is stated explicitly because it changes what "ran out of money" means:
an entry is rejected when it would exceed *buying power*, not when notional exceeds cash.

Position accounting uses the same weighted-average cost basis as the live journal
(:mod:`tradeloom.services.trading.position_builder`), so a backtested trade and a real trade with
identical fills report identical P&L.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from tradeloom.core.enums import Direction
from tradeloom.core.money import ONE, ZERO, is_zero, mul, quantize_money, quantize_price, safe_div
from tradeloom.core.money import quantize_quantity as qq


@dataclass(slots=True)
class SimTrade:
    """A completed round trip produced by the simulator."""

    sequence: int
    direction: Direction
    entry_timestamp: datetime
    exit_timestamp: datetime | None
    entry_price: Decimal
    exit_price: Decimal | None
    quantity: Decimal
    gross_pnl: Decimal
    commission: Decimal
    slippage: Decimal
    net_pnl: Decimal
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    risk_amount: Decimal | None = None
    r_multiple: Decimal | None = None
    return_percentage: Decimal | None = None
    holding_seconds: int | None = None
    mfe_price: Decimal | None = None
    mae_price: Decimal | None = None
    mfe_amount: Decimal | None = None
    mae_amount: Decimal | None = None
    exit_reason: str | None = None
    equity_after: Decimal | None = None
    entry_count: int = 1


@dataclass(slots=True)
class SimPosition:
    """Net exposure in the simulated instrument."""

    direction: Direction
    quantity: Decimal
    average_price: Decimal
    contract_multiplier: Decimal
    opened_at: datetime
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    initial_stop_loss: Decimal | None = None
    realized_pnl: Decimal = ZERO
    commission: Decimal = ZERO
    slippage: Decimal = ZERO
    entry_count: int = 1
    #: Best/worst prices seen while the position was open, for MFE/MAE.
    highest_price: Decimal | None = None
    lowest_price: Decimal | None = None
    risk_amount: Decimal | None = None

    @property
    def signed_quantity(self) -> Decimal:
        return self.quantity * Decimal(self.direction.sign)

    def notional(self, price: Decimal) -> Decimal:
        return quantize_money(mul(mul(self.quantity, price), self.contract_multiplier))

    def unrealized(self, price: Decimal) -> Decimal:
        delta = price - self.average_price
        return quantize_money(
            mul(mul(delta, self.quantity), self.contract_multiplier) * Decimal(self.direction.sign)
        )

    def observe(self, high: Decimal, low: Decimal) -> None:
        self.highest_price = high if self.highest_price is None else max(self.highest_price, high)
        self.lowest_price = low if self.lowest_price is None else min(self.lowest_price, low)

    def add(self, quantity: Decimal, price: Decimal) -> None:
        """Scale in: recompute the weighted-average basis."""
        new_quantity = qq(self.quantity + quantity)
        blended = safe_div(
            mul(self.average_price, self.quantity) + mul(price, quantity), new_quantity
        )
        self.average_price = quantize_price(blended if blended is not None else price)
        self.quantity = new_quantity
        self.entry_count += 1

    def reduce(self, quantity: Decimal, price: Decimal) -> Decimal:
        """Scale out: realise P&L on ``quantity`` at ``price``, leaving the basis untouched."""
        delta = price - self.average_price
        realized = quantize_money(
            mul(mul(delta, quantity), self.contract_multiplier) * Decimal(self.direction.sign)
        )
        self.quantity = qq(self.quantity - quantity)
        self.realized_pnl = quantize_money(self.realized_pnl + realized)
        return realized


@dataclass(slots=True)
class Portfolio:
    initial_capital: Decimal
    contract_multiplier: Decimal = ONE
    leverage: Decimal = ONE

    realized_pnl: Decimal = ZERO
    total_commission: Decimal = ZERO
    total_slippage: Decimal = ZERO
    position: SimPosition | None = None
    closed_trades: list[SimTrade] = field(default_factory=list)

    #: Bars during which a position was open, used for the exposure metric.
    bars_in_market: int = 0
    total_bars: int = 0

    @property
    def is_flat(self) -> bool:
        return self.position is None or is_zero(self.position.quantity)

    def equity(self, mark_price: Decimal | None) -> Decimal:
        """Account value including open-position mark-to-market."""
        base = quantize_money(self.initial_capital + self.realized_pnl)
        if self.position is None or mark_price is None:
            return base
        return quantize_money(base + self.position.unrealized(mark_price))

    def cash(self) -> Decimal:
        """Settled cash: capital plus realised P&L. Unrealised gains are not spendable."""
        return quantize_money(self.initial_capital + self.realized_pnl)

    def exposure(self, price: Decimal) -> Decimal:
        return ZERO if self.position is None else self.position.notional(price)

    def buying_power(self, price: Decimal) -> Decimal:
        """Remaining notional the account may take on."""
        equity = self.equity(price)
        limit = quantize_money(mul(equity, self.leverage))
        return quantize_money(limit - self.exposure(price))

    def record_costs(self, commission: Decimal, slippage: Decimal) -> None:
        self.total_commission = quantize_money(self.total_commission + commission)
        self.total_slippage = quantize_money(self.total_slippage + slippage)
        # Commission is a real cash cost and reduces realised P&L immediately.
        self.realized_pnl = quantize_money(self.realized_pnl - commission)


__all__ = ["Portfolio", "SimPosition", "SimTrade"]
