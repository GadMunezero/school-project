"""Turn a stream of fills into positions and completed trades.

Pure logic: no database, no I/O, no clock. That makes it exhaustively testable, and it is the
single implementation used by manual entry, CSV import, and (through an adapter) the backtest
engine — so a partial exit means the same thing everywhere.

Algorithm (weighted-average cost basis)
---------------------------------------
Fills for one ``(account, instrument)`` are processed in timestamp order. Ties are broken by the
caller-supplied sequence number so the result is deterministic for same-millisecond fills.

* No open trade -> the fill opens one. Direction is long for a buy, short for a sell.
* Fill in the same direction as the open trade -> **scale in**. The average entry price becomes
  the quantity-weighted average of the old basis and the new fill.
* Fill in the opposite direction -> **scale out**. Realised P&L for the closed quantity is
  ``(exit_price - avg_entry_price) * qty * multiplier * direction_sign``. The average entry price
  is *not* changed by an exit: the surviving quantity keeps its original basis.
* An opposite fill larger than the open quantity **flips**: it closes the trade exactly, then
  opens a new trade in the other direction with the surplus. The fill's commission and fees are
  split between the two trades in proportion to quantity.

Why average cost and not FIFO
-----------------------------
FIFO and average cost only differ for partially-closed positions, and average cost matches how
brokers report a position's "average price" in the platform UI — the number a trader sees while
managing the trade. Tax-lot accounting is a reporting concern, not a journalling one. The choice
is documented in ``docs/FINANCIALS.md`` and asserted by the test suite.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from tradeloom.core.enums import Direction, OrderSide, TradeStatus
from tradeloom.core.money import ONE, ZERO, is_zero, mul, quantize_money, quantize_price, safe_div
from tradeloom.core.money import quantize_quantity as qq


class PositionBuildError(ValueError):
    """Raised for fills that cannot form a coherent position (non-positive quantity, ...)."""


@dataclass(slots=True)
class Fill:
    """One execution. ``sequence`` disambiguates fills sharing a timestamp."""

    timestamp: datetime
    side: OrderSide
    quantity: Decimal
    price: Decimal
    commission: Decimal = ZERO
    fees: Decimal = ZERO
    sequence: int = 0
    order_id: uuid.UUID | None = None
    external_id: str | None = None

    def __post_init__(self) -> None:
        self.quantity = qq(self.quantity)
        self.price = quantize_price(self.price)
        self.commission = quantize_money(self.commission)
        self.fees = quantize_money(self.fees)
        if self.quantity <= 0:
            raise PositionBuildError("fill quantity must be greater than zero")
        if self.price <= 0:
            raise PositionBuildError("fill price must be greater than zero")


@dataclass(slots=True)
class Allocation:
    """How much of a fill was applied to a particular trade, and with what effect."""

    order_id: uuid.UUID | None
    timestamp: datetime
    side: OrderSide
    quantity: Decimal
    price: Decimal
    commission: Decimal
    fees: Decimal
    is_entry: bool
    #: Realised P&L contributed by this allocation (zero for entries).
    realized_pnl: Decimal = ZERO


@dataclass(slots=True)
class TradeAggregate:
    """A round trip under construction, or completed."""

    direction: Direction
    contract_multiplier: Decimal = ONE
    entry_timestamp: datetime | None = None
    exit_timestamp: datetime | None = None

    #: Quantity-weighted average of every entry fill still contributing basis.
    average_entry_price: Decimal = ZERO
    #: Quantity-weighted average of every exit fill.
    average_exit_price: Decimal | None = None

    #: Total quantity ever opened.
    total_quantity: Decimal = ZERO
    #: Quantity closed so far.
    closed_quantity: Decimal = ZERO
    #: Quantity still open.
    open_quantity: Decimal = ZERO

    gross_pnl: Decimal = ZERO
    commission: Decimal = ZERO
    fees: Decimal = ZERO

    allocations: list[Allocation] = field(default_factory=list)

    # -- derived ------------------------------------------------------------

    @property
    def net_pnl(self) -> Decimal:
        """Realised P&L after costs. This is the number every metric is built from."""
        return quantize_money(self.gross_pnl - self.commission - self.fees)

    @property
    def status(self) -> TradeStatus:
        if is_zero(self.open_quantity):
            return TradeStatus.CLOSED
        if self.closed_quantity > 0:
            return TradeStatus.PARTIALLY_CLOSED
        return TradeStatus.OPEN

    @property
    def is_closed(self) -> bool:
        return is_zero(self.open_quantity)

    @property
    def cost_basis(self) -> Decimal:
        """Notional value of the total opened quantity at the average entry price."""
        return quantize_money(
            mul(mul(self.average_entry_price, self.total_quantity), self.contract_multiplier)
        )

    @property
    def holding_seconds(self) -> int | None:
        if self.entry_timestamp is None or self.exit_timestamp is None:
            return None
        return int((self.exit_timestamp - self.entry_timestamp).total_seconds())

    def return_percentage(self) -> Decimal | None:
        """Net P&L as a percentage of the cost basis.

        Undefined (``None``) when the basis is zero, never silently 0 — see
        :func:`tradeloom.core.money.safe_div`.
        """
        basis = self.cost_basis
        if basis == 0:
            return None
        ratio = safe_div(self.net_pnl, abs(basis))
        return None if ratio is None else quantize_money(mul(ratio, Decimal(100)))

    def unrealized_pnl(self, mark_price: Decimal | None) -> Decimal | None:
        """Mark-to-market on the still-open quantity. ``None`` without a mark price — we never
        substitute the entry price, which would fake a P&L of exactly zero."""
        if mark_price is None or is_zero(self.open_quantity):
            return None
        delta = quantize_price(mark_price) - self.average_entry_price
        return quantize_money(
            mul(mul(delta, self.open_quantity), self.contract_multiplier)
            * Decimal(self.direction.sign)
        )

    # -- mutation -----------------------------------------------------------

    def apply_entry(
        self,
        *,
        quantity: Decimal,
        price: Decimal,
        timestamp: datetime,
        commission: Decimal,
        fees: Decimal,
        side: OrderSide,
        order_id: uuid.UUID | None,
    ) -> None:
        quantity = qq(quantity)
        price = quantize_price(price)
        if self.entry_timestamp is None:
            self.entry_timestamp = timestamp

        prior_notional = mul(self.average_entry_price, self.open_quantity)
        added_notional = mul(price, quantity)
        new_open = qq(self.open_quantity + quantity)
        weighted = safe_div(prior_notional + added_notional, new_open)
        self.average_entry_price = quantize_price(weighted if weighted is not None else price)

        self.open_quantity = new_open
        self.total_quantity = qq(self.total_quantity + quantity)
        self.commission = quantize_money(self.commission + commission)
        self.fees = quantize_money(self.fees + fees)
        self.allocations.append(
            Allocation(
                order_id=order_id,
                timestamp=timestamp,
                side=side,
                quantity=quantity,
                price=price,
                commission=commission,
                fees=fees,
                is_entry=True,
            )
        )

    def apply_exit(
        self,
        *,
        quantity: Decimal,
        price: Decimal,
        timestamp: datetime,
        commission: Decimal,
        fees: Decimal,
        side: OrderSide,
        order_id: uuid.UUID | None,
    ) -> Decimal:
        quantity = qq(quantity)
        price = quantize_price(price)
        if quantity > self.open_quantity:  # pragma: no cover - caller clamps first
            raise PositionBuildError("exit quantity exceeds open quantity")

        delta = price - self.average_entry_price
        realized = quantize_money(
            mul(mul(delta, quantity), self.contract_multiplier) * Decimal(self.direction.sign)
        )

        prior_exit_notional = mul(self.average_exit_price or ZERO, self.closed_quantity)
        self.closed_quantity = qq(self.closed_quantity + quantity)
        blended = safe_div(prior_exit_notional + mul(price, quantity), self.closed_quantity)
        self.average_exit_price = quantize_price(blended if blended is not None else price)

        self.open_quantity = qq(self.open_quantity - quantity)
        self.gross_pnl = quantize_money(self.gross_pnl + realized)
        self.commission = quantize_money(self.commission + commission)
        self.fees = quantize_money(self.fees + fees)
        self.exit_timestamp = timestamp

        self.allocations.append(
            Allocation(
                order_id=order_id,
                timestamp=timestamp,
                side=side,
                quantity=quantity,
                price=price,
                commission=commission,
                fees=fees,
                is_entry=False,
                realized_pnl=realized,
            )
        )
        return realized


def _prorate(total: Decimal, part: Decimal, whole: Decimal) -> Decimal:
    """Split a per-fill cost across allocations in proportion to quantity."""
    if whole == 0:
        return ZERO
    if part == whole:
        return quantize_money(total)
    share = safe_div(part, whole)
    return quantize_money(mul(total, share if share is not None else ZERO))


@dataclass(slots=True)
class BuildResult:
    closed_trades: list[TradeAggregate]
    open_trade: TradeAggregate | None

    @property
    def all_trades(self) -> list[TradeAggregate]:
        return [*self.closed_trades, *([self.open_trade] if self.open_trade else [])]


def build_trades(
    fills: list[Fill],
    *,
    contract_multiplier: Decimal = ONE,
    initial: TradeAggregate | None = None,
) -> BuildResult:
    """Fold fills into trades.

    ``initial`` lets an incremental importer continue an already-open trade rather than
    re-processing an account's entire history.
    """
    multiplier = qq(contract_multiplier)
    if multiplier <= 0:
        raise PositionBuildError("contract multiplier must be greater than zero")

    ordered = sorted(fills, key=lambda f: (f.timestamp, f.sequence))
    closed: list[TradeAggregate] = []
    current: TradeAggregate | None = initial

    for fill in ordered:
        remaining = fill.quantity
        while remaining > 0:
            if current is None:
                current = TradeAggregate(
                    direction=Direction.LONG if fill.side is OrderSide.BUY else Direction.SHORT,
                    contract_multiplier=multiplier,
                )

            same_direction = fill.side.sign == current.direction.sign

            if same_direction:
                applied = remaining
                current.apply_entry(
                    quantity=applied,
                    price=fill.price,
                    timestamp=fill.timestamp,
                    commission=_prorate(fill.commission, applied, fill.quantity),
                    fees=_prorate(fill.fees, applied, fill.quantity),
                    side=fill.side,
                    order_id=fill.order_id,
                )
                remaining = qq(remaining - applied)
                continue

            applied = min(remaining, current.open_quantity)
            if applied <= 0:  # pragma: no cover - defensive; open_quantity is > 0 by construction
                raise PositionBuildError("cannot close a trade with no open quantity")

            current.apply_exit(
                quantity=applied,
                price=fill.price,
                timestamp=fill.timestamp,
                commission=_prorate(fill.commission, applied, fill.quantity),
                fees=_prorate(fill.fees, applied, fill.quantity),
                side=fill.side,
                order_id=fill.order_id,
            )
            remaining = qq(remaining - applied)

            if current.is_closed:
                closed.append(current)
                current = None
                # Any surplus quantity flips into a new trade on the next loop iteration.

    return BuildResult(closed_trades=closed, open_trade=current)


def summarise_position(
    trade: TradeAggregate, mark_price: Decimal | None = None
) -> dict[str, object]:
    """Flat snapshot used to refresh the ``positions`` cache table."""
    return {
        "direction": trade.direction,
        "quantity": trade.open_quantity,
        "average_price": trade.average_entry_price,
        "realized_pnl": trade.gross_pnl,
        "unrealized_pnl": trade.unrealized_pnl(mark_price),
        "last_price": mark_price,
        "contract_multiplier": trade.contract_multiplier,
        "opened_at": trade.entry_timestamp,
        "closed_at": trade.exit_timestamp if trade.is_closed else None,
    }


__all__ = [
    "Allocation",
    "BuildResult",
    "Fill",
    "PositionBuildError",
    "TradeAggregate",
    "build_trades",
    "summarise_position",
]
