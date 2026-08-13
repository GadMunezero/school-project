"""Backtest configuration and cost models.

Every execution assumption is an explicit, serialisable setting. Nothing is hard-coded, and a
stored run keeps a frozen copy of this configuration so the result can be reproduced exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal
from typing import Any

from tradeloom.core.enums import (
    CommissionModelType,
    ExecutionModelType,
    IntrabarPriority,
    PositionSizingType,
    SlippageModelType,
)
from tradeloom.core.money import ONE, ZERO, mul, quantize_money, to_decimal


@dataclass(frozen=True, slots=True)
class CommissionConfig:
    """How trading costs are charged.

    * ``per_share`` / ``per_contract`` — ``rate * quantity``
    * ``per_trade`` — flat ``rate`` per fill
    * ``percent_of_notional`` — ``rate% * price * quantity * multiplier``

    ``minimum`` and ``maximum`` clamp the result. Commission is charged on **every fill**,
    entries and exits alike, which is how brokers actually bill.
    """

    model: CommissionModelType = CommissionModelType.NONE
    rate: Decimal = ZERO
    minimum: Decimal = ZERO
    maximum: Decimal | None = None

    def charge(self, *, quantity: Decimal, price: Decimal, multiplier: Decimal = ONE) -> Decimal:
        if self.model is CommissionModelType.NONE:
            return ZERO
        if self.model in (CommissionModelType.PER_SHARE, CommissionModelType.PER_CONTRACT):
            amount = mul(self.rate, quantity)
        elif self.model is CommissionModelType.PER_TRADE:
            amount = self.rate
        elif self.model in (
            CommissionModelType.PERCENT_OF_NOTIONAL,
            CommissionModelType.TIERED_PERCENT,
        ):
            notional = mul(mul(price, quantity), multiplier)
            amount = mul(notional, self.rate) / Decimal(100)
        else:  # pragma: no cover - enum is exhaustive
            amount = ZERO

        amount = max(quantize_money(amount), quantize_money(self.minimum))
        if self.maximum is not None:
            amount = min(amount, quantize_money(self.maximum))
        return quantize_money(amount)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model.value,
            "rate": str(self.rate),
            "minimum": str(self.minimum),
            "maximum": str(self.maximum) if self.maximum is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> CommissionConfig:
        if not data:
            return cls()
        return cls(
            model=CommissionModelType(data.get("model", CommissionModelType.NONE.value)),
            rate=to_decimal(data.get("rate", 0), default=ZERO),
            minimum=to_decimal(data.get("minimum", 0), default=ZERO),
            maximum=(
                to_decimal(data["maximum"]) if data.get("maximum") not in (None, "") else None
            ),
        )


@dataclass(frozen=True, slots=True)
class SlippageConfig:
    """Adverse price movement applied to every fill.

    Slippage always works *against* the trade: buys fill higher, sells fill lower. There is no
    configuration that makes slippage favourable, because a model that sometimes helps you is a
    model that flatters your backtest.
    """

    model: SlippageModelType = SlippageModelType.NONE
    #: Ticks for FIXED_TICKS, percent for PERCENT_OF_PRICE, fraction of spread for SPREAD_FRACTION.
    amount: Decimal = ZERO
    tick_size: Decimal = Decimal("0.01")

    def adjust(self, price: Decimal, *, is_buy: bool, spread: Decimal = ZERO) -> Decimal:
        if self.model is SlippageModelType.NONE or self.amount == 0:
            return price
        if self.model is SlippageModelType.FIXED_TICKS:
            offset = mul(self.amount, self.tick_size)
        elif self.model is SlippageModelType.PERCENT_OF_PRICE:
            offset = mul(price, self.amount) / Decimal(100)
        elif self.model is SlippageModelType.SPREAD_FRACTION:
            offset = mul(spread, self.amount)
        else:  # pragma: no cover
            offset = ZERO
        return price + offset if is_buy else price - offset

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model.value,
            "amount": str(self.amount),
            "tick_size": str(self.tick_size),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SlippageConfig:
        if not data:
            return cls()
        return cls(
            model=SlippageModelType(data.get("model", SlippageModelType.NONE.value)),
            amount=to_decimal(data.get("amount", 0), default=ZERO),
            tick_size=to_decimal(data.get("tick_size", "0.01"), default=Decimal("0.01")),
        )


@dataclass(frozen=True, slots=True)
class SpreadConfig:
    """Bid/ask spread.

    Candles are mid or last prices; a real buy pays the ask and a real sell receives the bid.
    Half the spread is applied to each side.
    """

    #: Absolute price spread. Ignored when ``percent`` is set.
    absolute: Decimal = ZERO
    #: Spread as a percentage of price.
    percent: Decimal = ZERO

    def spread_at(self, price: Decimal) -> Decimal:
        if self.percent > 0:
            return quantize_money(mul(price, self.percent) / Decimal(100))
        return self.absolute

    def adjust(self, price: Decimal, *, is_buy: bool) -> Decimal:
        half = self.spread_at(price) / Decimal(2)
        if half == 0:
            return price
        return price + half if is_buy else price - half

    def to_dict(self) -> dict[str, Any]:
        return {"absolute": str(self.absolute), "percent": str(self.percent)}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SpreadConfig:
        if not data:
            return cls()
        return cls(
            absolute=to_decimal(data.get("absolute", 0), default=ZERO),
            percent=to_decimal(data.get("percent", 0), default=ZERO),
        )


@dataclass(frozen=True, slots=True)
class RiskConfig:
    """Position sizing and exposure limits."""

    sizing: PositionSizingType = PositionSizingType.PERCENT_RISK
    #: Meaning depends on ``sizing``: quantity, notional, cash risk, or percent.
    value: Decimal = Decimal(1)
    max_concurrent_positions: int = 1
    max_position_quantity: Decimal | None = None
    #: Total notional cannot exceed ``equity * leverage``.
    leverage: Decimal = ONE
    allow_pyramiding: bool = False
    #: Bars to wait after a position closes before a new entry is allowed.
    cooldown_bars: int = 0
    #: Reject an entry that would risk more than this fraction of equity (0 disables the check).
    max_risk_percent_per_trade: Decimal = Decimal(10)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sizing": self.sizing.value,
            "value": str(self.value),
            "max_concurrent_positions": self.max_concurrent_positions,
            "max_position_quantity": (
                str(self.max_position_quantity) if self.max_position_quantity is not None else None
            ),
            "leverage": str(self.leverage),
            "allow_pyramiding": self.allow_pyramiding,
            "cooldown_bars": self.cooldown_bars,
            "max_risk_percent_per_trade": str(self.max_risk_percent_per_trade),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RiskConfig:
        if not data:
            return cls()
        raw_max = data.get("max_position_quantity")
        return cls(
            sizing=PositionSizingType(data.get("sizing", PositionSizingType.PERCENT_RISK.value)),
            value=to_decimal(data.get("value", 1), default=ONE),
            max_concurrent_positions=int(data.get("max_concurrent_positions", 1)),
            max_position_quantity=to_decimal(raw_max) if raw_max not in (None, "") else None,
            leverage=to_decimal(data.get("leverage", 1), default=ONE),
            allow_pyramiding=bool(data.get("allow_pyramiding", False)),
            cooldown_bars=int(data.get("cooldown_bars", 0)),
            max_risk_percent_per_trade=to_decimal(
                data.get("max_risk_percent_per_trade", 10), default=Decimal(10)
            ),
        )


@dataclass(frozen=True, slots=True)
class SessionConfig:
    """Trading-hours restrictions, evaluated in ``timezone``.

    ``start``/``end`` bound when *new* entries may be opened. ``close_at_session_end`` forces open
    positions flat at the session boundary, which is how an intraday strategy avoids carrying
    overnight risk it never intended to take.
    """

    timezone: str = "UTC"
    start: time | None = None
    end: time | None = None
    #: 0 = Monday. Empty means every weekday is tradable.
    weekdays: tuple[int, ...] = ()
    close_at_session_end: bool = False

    def allows_entry(self, moment: datetime) -> bool:
        from tradeloom.core.timeutil import to_zone

        local = to_zone(moment, self.timezone)
        if self.weekdays and local.weekday() not in self.weekdays:
            return False
        if self.start is not None and local.time() < self.start:
            return False
        return not (self.end is not None and local.time() >= self.end)

    def is_after_session(self, moment: datetime) -> bool:
        if self.end is None:
            return False
        from tradeloom.core.timeutil import to_zone

        return to_zone(moment, self.timezone).time() >= self.end

    def to_dict(self) -> dict[str, Any]:
        return {
            "timezone": self.timezone,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "weekdays": list(self.weekdays),
            "close_at_session_end": self.close_at_session_end,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SessionConfig:
        if not data:
            return cls()

        def _parse(value: Any) -> time | None:
            if not value:
                return None
            return time.fromisoformat(str(value))

        return cls(
            timezone=data.get("timezone", "UTC"),
            start=_parse(data.get("start")),
            end=_parse(data.get("end")),
            weekdays=tuple(int(day) for day in data.get("weekdays", [])),
            close_at_session_end=bool(data.get("close_at_session_end", False)),
        )


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    """The complete, reproducible definition of a run."""

    symbol: str
    initial_capital: Decimal
    currency: str = "USD"
    contract_multiplier: Decimal = ONE
    tick_size: Decimal = Decimal("0.01")
    #: Smallest tradable increment; sizes are floored to a multiple of this.
    lot_size: Decimal = ONE
    #: Whether fractional quantities are allowed at all (crypto yes, shares usually no).
    allow_fractional: bool = False

    execution_model: ExecutionModelType = ExecutionModelType.NEXT_BAR_OPEN
    intrabar_priority: IntrabarPriority = IntrabarPriority.STOP_FIRST

    commission: CommissionConfig = field(default_factory=CommissionConfig)
    slippage: SlippageConfig = field(default_factory=SlippageConfig)
    spread: SpreadConfig = field(default_factory=SpreadConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    session: SessionConfig = field(default_factory=SessionConfig)

    #: Bars per year, used to annualise Sharpe/Sortino. Derived from the timeframe by the caller.
    periods_per_year: int = 252
    #: Risk-free rate as an annual percentage, used by Sharpe.
    risk_free_rate_percent: Decimal = ZERO

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "initial_capital": str(self.initial_capital),
            "currency": self.currency,
            "contract_multiplier": str(self.contract_multiplier),
            "tick_size": str(self.tick_size),
            "lot_size": str(self.lot_size),
            "allow_fractional": self.allow_fractional,
            "execution_model": self.execution_model.value,
            "intrabar_priority": self.intrabar_priority.value,
            "commission": self.commission.to_dict(),
            "slippage": self.slippage.to_dict(),
            "spread": self.spread.to_dict(),
            "risk": self.risk.to_dict(),
            "session": self.session.to_dict(),
            "periods_per_year": self.periods_per_year,
            "risk_free_rate_percent": str(self.risk_free_rate_percent),
        }


__all__ = [
    "BacktestConfig",
    "CommissionConfig",
    "RiskConfig",
    "SessionConfig",
    "SlippageConfig",
    "SpreadConfig",
]
