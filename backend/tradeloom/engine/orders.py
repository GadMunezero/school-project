"""Simulated orders and the exact fill rules for each type.

Fill pricing, stated precisely (and asserted by the engine tests):

======================  =============================================================
Order type              Fill rule for a bar with (O, H, L, C)
======================  =============================================================
market                  ``O`` under ``next_bar_open``; ``C`` under ``current_bar_close``
buy stop @ P            triggers when ``H >= P``; fills at ``max(P, O)`` — a gap above
                        the stop fills at the open, never at the untraded stop price
sell stop @ P           triggers when ``L <= P``; fills at ``min(P, O)``
buy limit @ P           fills when ``L <= P``, at ``min(P, O)`` — a gap below the limit
                        gives the better open price, which is what really happens
sell limit @ P          fills when ``H >= P``, at ``max(P, O)``
stop-limit              stop must trigger *and* the limit condition must hold within the
                        same bar; otherwise it stays working
======================  =============================================================

Spread and slippage are applied on top of the fill price, always adversely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from itertools import count

from tradeloom.core.enums import OrderSide, OrderStatus, OrderType, TimeInForce
from tradeloom.core.money import ZERO
from tradeloom.engine.bars import Bar
from tradeloom.engine.events import OrderIntent

_order_ids = count(1)


def next_order_id() -> int:
    return next(_order_ids)


def reset_order_ids() -> None:
    """Tests reset the counter so ids are comparable between runs."""
    global _order_ids
    _order_ids = count(1)


@dataclass(slots=True)
class SimOrder:
    side: OrderSide
    quantity: Decimal
    order_type: OrderType = OrderType.MARKET
    intent: OrderIntent = OrderIntent.OPEN
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None
    time_in_force: TimeInForce = TimeInForce.GTC

    #: When the strategy decided (bar N's close).
    signal_timestamp: datetime | None = None
    #: When the order entered the book.
    order_timestamp: datetime | None = None
    #: When it filled.
    fill_timestamp: datetime | None = None

    status: OrderStatus = OrderStatus.PENDING
    filled_quantity: Decimal = ZERO
    fill_price: Decimal | None = None
    reference_price: Decimal | None = None
    commission: Decimal = ZERO
    slippage: Decimal = ZERO
    reject_reason: str | None = None
    tag: str | None = None

    id: int = field(default_factory=next_order_id)
    #: False until the bar after submission under ``next_bar_open`` execution.
    is_active: bool = False

    @property
    def is_buy(self) -> bool:
        return self.side is OrderSide.BUY

    @property
    def is_working(self) -> bool:
        return self.status in (OrderStatus.PENDING, OrderStatus.WORKING)

    @property
    def is_protective(self) -> bool:
        return self.intent in (OrderIntent.STOP_LOSS, OrderIntent.TAKE_PROFIT)

    def trigger_price_for(self, bar: Bar) -> Decimal | None:
        """The untouched fill price for this order against ``bar``, or ``None`` if it does not fill.

        Spread and slippage are applied by the broker afterwards, so this stays a pure statement
        about what the market would have given.
        """
        if self.order_type is OrderType.MARKET:
            return bar.open

        if self.order_type is OrderType.LIMIT:
            assert self.limit_price is not None
            if self.is_buy:
                return min(self.limit_price, bar.open) if bar.low <= self.limit_price else None
            return max(self.limit_price, bar.open) if bar.high >= self.limit_price else None

        if self.order_type is OrderType.STOP:
            assert self.stop_price is not None
            if self.is_buy:
                return max(self.stop_price, bar.open) if bar.high >= self.stop_price else None
            return min(self.stop_price, bar.open) if bar.low <= self.stop_price else None

        if self.order_type is OrderType.STOP_LIMIT:
            assert self.stop_price is not None and self.limit_price is not None
            triggered = bar.high >= self.stop_price if self.is_buy else bar.low <= self.stop_price
            if not triggered:
                return None
            # Conservative: the limit must also be reachable within the same bar.
            if self.is_buy:
                return min(self.limit_price, bar.open) if bar.low <= self.limit_price else None
            return max(self.limit_price, bar.open) if bar.high >= self.limit_price else None

        return None  # pragma: no cover - enum is exhaustive

    def cancel(self, reason: str) -> None:
        self.status = OrderStatus.CANCELLED
        self.reject_reason = reason

    def reject(self, reason: str) -> None:
        self.status = OrderStatus.REJECTED
        self.reject_reason = reason


__all__ = ["SimOrder", "next_order_id", "reset_order_ids"]
