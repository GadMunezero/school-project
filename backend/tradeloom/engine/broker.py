"""The broker simulator: order book, fills, and position bookkeeping.

Bar lifecycle — this ordering *is* the look-ahead guarantee
-----------------------------------------------------------
For each bar N the runner performs exactly these steps, in this order:

1. ``open_bar(bar, index)`` — orders that were already working (submitted on an earlier bar) are
   matched against bar N's OHLC. Protective stops and targets are evaluated here.
2. the strategy's ``on_bar`` runs, seeing bar N *complete* (O/H/L/C) and every earlier bar. It
   may submit orders.
3. ``close_bar(bar)`` — under ``current_bar_close`` execution, market orders submitted in step 2
   fill at bar N's close. Under ``next_bar_open`` (the default) they are merely activated, and
   will fill against bar N+1 in step 1 of the next iteration.

A strategy therefore can never trade at a price from a bar it has not seen, and can never act on
bar N's close before that close exists. Orders submitted in step 2 are marked inactive for the
remainder of bar N, which is what stops a same-bar stop order from being filled by the very bar
that created it.

Intrabar ambiguity
------------------
When a single bar's range spans both the stop and the target, the bar alone cannot say which came
first. ``IntrabarPriority`` makes the assumption explicit; the default ``stop_first`` is the
pessimistic choice, so a backtest never flatters itself by assuming the good outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from tradeloom.core.enums import (
    Direction,
    ExecutionModelType,
    IntrabarPriority,
    OrderSide,
    OrderStatus,
    OrderType,
)
from tradeloom.core.money import ZERO, is_zero, mul, quantize_money, quantize_price
from tradeloom.core.money import quantize_quantity as qq
from tradeloom.engine.bars import Bar
from tradeloom.engine.config import BacktestConfig
from tradeloom.engine.events import FillEvent, OrderIntent
from tradeloom.engine.orders import SimOrder
from tradeloom.engine.portfolio import Portfolio, SimPosition, SimTrade


@dataclass(slots=True)
class BrokerSimulator:
    config: BacktestConfig
    portfolio: Portfolio

    working_orders: list[SimOrder] = field(default_factory=list)
    order_log: list[SimOrder] = field(default_factory=list)
    fills: list[FillEvent] = field(default_factory=list)

    _current_bar: Bar | None = None
    _current_index: int = -1
    _trade_sequence: int = 0
    _last_exit_index: int | None = None
    #: Why the fill currently being applied is closing a position. Set by :meth:`_execute`.
    _pending_exit_reason: str = "signal"

    # ------------------------------------------------------------------
    # Order entry
    # ------------------------------------------------------------------

    def submit(self, order: SimOrder, *, signal_timestamp: datetime | None = None) -> SimOrder:
        """Accept an order into the book.

        Under ``next_bar_open`` the order stays inactive for the rest of the current bar, so it
        cannot be filled by the bar that produced the signal.
        """
        order.signal_timestamp = signal_timestamp or (
            self._current_bar.opened_at if self._current_bar else None
        )
        order.order_timestamp = (
            self._current_bar.opened_at if self._current_bar else order.signal_timestamp
        )
        order.status = OrderStatus.WORKING
        # Always inactive for the remainder of the bar that created it. `open_bar` only matches
        # active orders, so this is what prevents a signal from being filled by the very bar that
        # produced it. `close_bar` then either fills it at this bar's close (current_bar_close) or
        # activates it for the next bar (next_bar_open).
        order.is_active = False
        self.working_orders.append(order)
        self.order_log.append(order)
        return order

    def cancel(self, order: SimOrder, reason: str = "cancelled") -> None:
        if order.is_working:
            order.cancel(reason)
        if order in self.working_orders:
            self.working_orders.remove(order)

    def cancel_protective_orders(self, reason: str = "position_closed") -> None:
        for order in list(self.working_orders):
            if order.is_protective:
                self.cancel(order, reason)

    # ------------------------------------------------------------------
    # Bar lifecycle
    # ------------------------------------------------------------------

    def open_bar(self, bar: Bar, index: int) -> list[FillEvent]:
        """Step 1: match already-working orders against this bar."""
        self._current_bar = bar
        self._current_index = index
        self.portfolio.total_bars += 1

        if self.portfolio.position is not None:
            self.portfolio.position.observe(bar.high, bar.low)
            self.portfolio.bars_in_market += 1

        produced: list[FillEvent] = []
        for order in self._ordered_for_matching():
            if not order.is_working or not order.is_active:
                continue
            price = order.trigger_price_for(bar)
            if price is None:
                continue
            event = self._execute(order, price, bar.opened_at)
            if event is not None:
                produced.append(event)

        return produced

    def close_bar(self, bar: Bar) -> list[FillEvent]:
        """Step 3: apply the execution model to orders submitted during this bar."""
        produced: list[FillEvent] = []
        for order in list(self.working_orders):
            if not order.is_working or order.is_active:
                continue
            if self.config.execution_model is ExecutionModelType.CURRENT_BAR_CLOSE:
                if order.order_type is OrderType.MARKET:
                    event = self._execute(order, bar.close, bar.opened_at)
                    if event is not None:
                        produced.append(event)
                else:
                    order.is_active = True
            else:
                # next_bar_open: becomes eligible from the following bar onwards.
                order.is_active = True
        return produced

    def _ordered_for_matching(self) -> list[SimOrder]:
        """Deterministic matching order.

        Protective orders are evaluated before new entries so a stop-out on bar N is not
        pre-empted by an entry on the same bar. Within the protective group, the intrabar
        priority decides whether the stop or the target is assumed to trigger first.
        """
        stops = [o for o in self.working_orders if o.intent is OrderIntent.STOP_LOSS]
        targets = [o for o in self.working_orders if o.intent is OrderIntent.TAKE_PROFIT]
        others = [o for o in self.working_orders if not o.is_protective]

        if self.config.intrabar_priority is IntrabarPriority.TARGET_FIRST:
            protective = [*targets, *stops]
        else:
            # STOP_FIRST and WORST_CASE both resolve the stop first for a directional position.
            protective = [*stops, *targets]
        return [*protective, *others]

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _execute(
        self, order: SimOrder, raw_price: Decimal, timestamp: datetime
    ) -> FillEvent | None:
        reference = quantize_price(raw_price)
        spread_adjusted = self.config.spread.adjust(reference, is_buy=order.is_buy)
        spread_width = self.config.spread.spread_at(reference)
        final_price = self.config.slippage.adjust(
            spread_adjusted, is_buy=order.is_buy, spread=spread_width
        )
        final_price = quantize_price(final_price)
        if final_price <= 0:
            order.reject("non_positive_fill_price")
            self.working_orders.remove(order)
            return None

        quantity = qq(order.quantity)
        if order.intent is OrderIntent.OPEN and not self._can_afford(quantity, final_price):
            order.reject("insufficient_buying_power")
            self.working_orders.remove(order)
            return None

        commission = self.config.commission.charge(
            quantity=quantity, price=final_price, multiplier=self.config.contract_multiplier
        )
        slippage_cost = quantize_money(
            mul(mul(abs(final_price - reference), quantity), self.config.contract_multiplier)
        )

        order.status = OrderStatus.FILLED
        order.filled_quantity = quantity
        order.fill_price = final_price
        order.reference_price = reference
        order.commission = commission
        order.slippage = slippage_cost
        order.fill_timestamp = timestamp
        self.working_orders.remove(order)

        self._pending_exit_reason = _exit_reason(order)
        self._apply_fill(order, final_price, timestamp, commission, slippage_cost)

        event = FillEvent(
            timestamp=timestamp,
            order_id=order.id,
            quantity=quantity,
            price=final_price,
            reference_price=reference,
            commission=commission,
            slippage=slippage_cost,
        )
        self.fills.append(event)
        return event

    def _can_afford(self, quantity: Decimal, price: Decimal) -> bool:
        required = quantize_money(mul(mul(quantity, price), self.config.contract_multiplier))
        return self.portfolio.buying_power(price) >= required

    def _apply_fill(
        self,
        order: SimOrder,
        price: Decimal,
        timestamp: datetime,
        commission: Decimal,
        slippage: Decimal,
    ) -> None:
        portfolio = self.portfolio
        portfolio.record_costs(commission, slippage)
        position = portfolio.position

        if position is None:
            direction = Direction.LONG if order.is_buy else Direction.SHORT
            portfolio.position = SimPosition(
                direction=direction,
                quantity=order.filled_quantity,
                average_price=price,
                contract_multiplier=self.config.contract_multiplier,
                opened_at=timestamp,
                commission=commission,
                slippage=slippage,
                highest_price=price,
                lowest_price=price,
            )
            return

        same_direction = (order.is_buy and position.direction is Direction.LONG) or (
            not order.is_buy and position.direction is Direction.SHORT
        )
        position.commission = quantize_money(position.commission + commission)
        position.slippage = quantize_money(position.slippage + slippage)

        if same_direction:
            position.add(order.filled_quantity, price)
            return

        closing = min(order.filled_quantity, position.quantity)
        realized = position.reduce(closing, price)
        portfolio.realized_pnl = quantize_money(portfolio.realized_pnl + realized)

        if is_zero(position.quantity):
            self._close_position(position, price, timestamp, closing)
            surplus = qq(order.filled_quantity - closing)
            if surplus > 0:
                # A flip: the surplus opens a new position in the opposite direction.
                portfolio.position = SimPosition(
                    direction=Direction.LONG if order.is_buy else Direction.SHORT,
                    quantity=surplus,
                    average_price=price,
                    contract_multiplier=self.config.contract_multiplier,
                    opened_at=timestamp,
                    highest_price=price,
                    lowest_price=price,
                )
            else:
                portfolio.position = None
                self.cancel_protective_orders()
                self._last_exit_index = self._current_index

    def _close_position(
        self,
        position: SimPosition,
        exit_price: Decimal,
        timestamp: datetime,
        closed_quantity: Decimal,
    ) -> None:
        from tradeloom.core.money import quantize_ratio, safe_div

        self._trade_sequence += 1
        gross = quantize_money(position.realized_pnl)
        net = quantize_money(gross - position.commission)

        risk = position.risk_amount
        r_multiple = None
        if risk is not None and risk > 0:
            ratio = safe_div(net, risk)
            r_multiple = quantize_ratio(ratio) if ratio is not None else None

        # ``position.quantity`` is zero by this point, so the basis comes from the closing size.
        traded_quantity = closed_quantity
        basis = quantize_money(
            mul(mul(position.average_price, traded_quantity), position.contract_multiplier)
        )
        return_percentage = None
        if basis != 0:
            ratio = safe_div(net, abs(basis))
            return_percentage = (
                quantize_money(mul(ratio, Decimal(100))) if ratio is not None else None
            )

        mfe_price = (
            position.highest_price
            if position.direction is Direction.LONG
            else position.lowest_price
        )
        mae_price = (
            position.lowest_price
            if position.direction is Direction.LONG
            else position.highest_price
        )
        sign = Decimal(position.direction.sign)
        mfe_amount = mae_amount = None
        if mfe_price is not None:
            move = max((mfe_price - position.average_price) * sign, ZERO)
            mfe_amount = quantize_money(
                mul(mul(move, traded_quantity), position.contract_multiplier)
            )
        if mae_price is not None:
            move = max((position.average_price - mae_price) * sign, ZERO)
            mae_amount = quantize_money(
                mul(mul(move, traded_quantity), position.contract_multiplier)
            )

        trade = SimTrade(
            sequence=self._trade_sequence,
            # Carried so the breakdowns know which market's trading day applies to this trade.
            asset_type=self.config.asset_type,
            direction=position.direction,
            entry_timestamp=position.opened_at,
            exit_timestamp=timestamp,
            entry_price=position.average_price,
            exit_price=exit_price,
            quantity=traded_quantity,
            gross_pnl=gross,
            commission=position.commission,
            slippage=position.slippage,
            net_pnl=net,
            stop_loss=position.initial_stop_loss or position.stop_loss,
            take_profit=position.take_profit,
            risk_amount=risk,
            r_multiple=r_multiple,
            return_percentage=return_percentage,
            holding_seconds=int((timestamp - position.opened_at).total_seconds()),
            mfe_price=mfe_price,
            mae_price=mae_price,
            mfe_amount=mfe_amount,
            mae_amount=mae_amount,
            exit_reason=self._pending_exit_reason,
            equity_after=self.portfolio.equity(exit_price),
            entry_count=position.entry_count,
        )
        self.portfolio.closed_trades.append(trade)

    # ------------------------------------------------------------------
    # Convenience used by strategies and the replay session
    # ------------------------------------------------------------------

    def bars_since_last_exit(self) -> int | None:
        if self._last_exit_index is None:
            return None
        return self._current_index - self._last_exit_index

    def attach_protection(
        self, *, stop_loss: Decimal | None, take_profit: Decimal | None
    ) -> list[SimOrder]:
        """Place resting stop/target orders for the current position.

        They become active on the *next* bar, matching the execution model: a stop created from
        bar N's close cannot be filled by bar N.
        """
        position = self.portfolio.position
        if position is None:
            return []

        self.cancel_protective_orders("replaced")
        position.stop_loss = stop_loss
        if position.initial_stop_loss is None:
            position.initial_stop_loss = stop_loss
        position.take_profit = take_profit

        if stop_loss is not None:
            risk_per_unit = (position.average_price - stop_loss) * Decimal(position.direction.sign)
            if risk_per_unit > 0:
                position.risk_amount = quantize_money(
                    mul(mul(risk_per_unit, position.quantity), position.contract_multiplier)
                )

        exit_side = OrderSide.SELL if position.direction is Direction.LONG else OrderSide.BUY
        created: list[SimOrder] = []
        if stop_loss is not None:
            created.append(
                self.submit(
                    SimOrder(
                        side=exit_side,
                        quantity=position.quantity,
                        order_type=OrderType.STOP,
                        stop_price=quantize_price(stop_loss),
                        intent=OrderIntent.STOP_LOSS,
                        tag="stop_loss",
                    )
                )
            )
        if take_profit is not None:
            created.append(
                self.submit(
                    SimOrder(
                        side=exit_side,
                        quantity=position.quantity,
                        order_type=OrderType.LIMIT,
                        limit_price=quantize_price(take_profit),
                        intent=OrderIntent.TAKE_PROFIT,
                        tag="take_profit",
                    )
                )
            )
        return created

    def close_position_at(self, price: Decimal, timestamp: datetime, reason: str) -> None:
        """Force-flatten at a known price (end of data, session close, user action)."""
        position = self.portfolio.position
        if position is None or is_zero(position.quantity):
            return
        order = SimOrder(
            side=OrderSide.SELL if position.direction is Direction.LONG else OrderSide.BUY,
            quantity=position.quantity,
            order_type=OrderType.MARKET,
            intent=OrderIntent.CLOSE,
            tag=reason,
        )
        order.order_timestamp = timestamp
        order.signal_timestamp = timestamp
        self.order_log.append(order)
        self._execute_forced(order, price, timestamp)

    def _execute_forced(self, order: SimOrder, price: Decimal, timestamp: datetime) -> None:
        self.working_orders.append(order)
        order.status = OrderStatus.WORKING
        self._execute(order, price, timestamp)
        self.cancel_protective_orders("position_closed")


def _exit_reason(order: SimOrder) -> str:
    if order.intent is OrderIntent.STOP_LOSS:
        return "stop_loss"
    if order.intent is OrderIntent.TAKE_PROFIT:
        return "take_profit"
    if order.tag:
        return order.tag
    return "signal"


__all__ = ["BrokerSimulator"]
