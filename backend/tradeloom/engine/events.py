"""Engine events.

The pipeline is strictly one-directional::

    MarketDataEvent -> Strategy -> SignalEvent -> RiskManager -> OrderEvent
                                                              -> BrokerSimulator -> FillEvent
                                                              -> Portfolio -> PerformanceRecorder

Each event carries the timestamp at which it came into existence. Keeping signal, order and fill
timestamps distinct is what makes an execution assumption auditable after the fact: you can read
a stored run and see that a signal at 09:30 produced an order at 09:30 that filled at 09:35.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from tradeloom.engine.bars import Bar


class SignalDirection(StrEnum):
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    EXIT_LONG = "exit_long"
    EXIT_SHORT = "exit_short"
    CLOSE_ALL = "close_all"


class OrderIntent(StrEnum):
    OPEN = "open"
    CLOSE = "close"
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"


@dataclass(frozen=True, slots=True)
class MarketDataEvent:
    timestamp: datetime
    bar: Bar
    #: Index of this bar within the run's series. Strategies must never index beyond it.
    index: int


@dataclass(frozen=True, slots=True)
class SignalEvent:
    """A strategy's intent, before any sizing or risk decision."""

    timestamp: datetime
    direction: SignalDirection
    #: 0..1 conviction, multiplied into the risk budget by the risk manager.
    strength: Decimal = Decimal(1)
    stop_loss: Decimal | None = None
    take_profit: Decimal | None = None
    #: Explicit size request; when None the risk manager sizes the position.
    quantity: Decimal | None = None
    reason: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OrderEvent:
    timestamp: datetime
    order_id: int
    intent: OrderIntent
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class FillEvent:
    timestamp: datetime
    order_id: int
    quantity: Decimal
    price: Decimal
    #: Price before slippage and spread, so cost impact stays attributable.
    reference_price: Decimal
    commission: Decimal
    slippage: Decimal


__all__ = [
    "FillEvent",
    "MarketDataEvent",
    "OrderEvent",
    "OrderIntent",
    "SignalDirection",
    "SignalEvent",
]
