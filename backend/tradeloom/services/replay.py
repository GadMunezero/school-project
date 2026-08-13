"""Interactive market replay.

Replay is **not** an animation over a chart. Each step feeds the next real candle into the same
:class:`~tradeloom.engine.broker.BrokerSimulator` the backtester uses, with the same fill rules,
the same commission and slippage models and the same intrabar assumptions. A stop placed during a
replay fills exactly where it would have filled in a backtest.

Two properties make it trustworthy:

* **The future is never sent to the client.** The API returns candles up to the cursor only, so
  the browser cannot peek ahead even if someone reads the network tab.
* **State is server-side.** The simulator is rebuilt from the persisted state on every request,
  so a refresh, a second tab, or a lost connection cannot desynchronise the session from its P&L.

Rebuilding by replaying bars from the start is O(n) per step in the worst case, which is fine for
the session lengths replay is used for (a few thousand bars). The cost is documented rather than
hidden behind a cache that could drift.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core.enums import OrderSide, OrderType
from tradeloom.core.errors import NotFoundError, UnprocessableStateError
from tradeloom.core.money import ONE, quantize_money, quantize_price
from tradeloom.core.timeutil import ensure_aware, utcnow
from tradeloom.engine.bars import BarSeries
from tradeloom.engine.broker import BrokerSimulator
from tradeloom.engine.config import (
    BacktestConfig,
    CommissionConfig,
    SlippageConfig,
    SpreadConfig,
)
from tradeloom.engine.orders import SimOrder
from tradeloom.engine.portfolio import Portfolio
from tradeloom.models.backtest import ReplaySession
from tradeloom.repositories.base import TenantRepository
from tradeloom.repositories.trading import InstrumentRepository
from tradeloom.schemas.backtest import ReplayCreate, ReplayOrderRequest
from tradeloom.services.market_data import MarketDataService

#: Hard ceiling on a replay session's length, to bound the rebuild cost.
MAX_REPLAY_BARS = 20_000


class ReplayRepository(TenantRepository[ReplaySession]):
    model = ReplaySession
    supports_soft_delete = False


class ReplayService:
    def __init__(
        self, session: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        self.session = session
        self.organization_id = organization_id
        self.user_id = user_id
        self.repo = ReplayRepository(session, organization_id)
        self.instruments = InstrumentRepository(session, organization_id)
        self.market_data = MarketDataService(session)

    # ------------------------------------------------------------------

    async def create(self, payload: ReplayCreate) -> ReplaySession:
        instrument = await self.instruments.get(payload.instrument_id)
        if instrument is None:
            raise NotFoundError("Instrument not found.")

        source = (
            await self.market_data.get_source(payload.market_data_source_id)
            if payload.market_data_source_id
            else await self.market_data.default_source()
        )
        series, _ = await self.market_data.get_bars(
            instrument.id,
            payload.timeframe,
            source=source,
            start=payload.start_at,
            end=payload.end_at,
        )
        if len(series) < 2:
            raise UnprocessableStateError(
                "There are not enough candles in that range to replay. Widen the range or load "
                "more market data."
            )
        if len(series) > MAX_REPLAY_BARS:
            raise UnprocessableStateError(
                f"A replay session is limited to {MAX_REPLAY_BARS:,} candles. Narrow the range."
            )

        record = ReplaySession(
            organization_id=self.organization_id,
            user_id=self.user_id,
            instrument_id=instrument.id,
            market_data_source_id=source.id,
            name=payload.name.strip(),
            timeframe=payload.timeframe,
            start_at=ensure_aware(payload.start_at),
            end_at=ensure_aware(payload.end_at),
            # The cursor starts after the warm-up window so the chart has context to read.
            cursor_index=min(payload.warmup_bars, len(series) - 1),
            total_bars=len(series),
            initial_capital=quantize_money(payload.initial_capital),
            currency=payload.currency,
            execution_model=payload.execution_model,
            commission_config=payload.commission_config,
            slippage_config={
                **payload.slippage_config,
                "tick_size": str(instrument.tick_size),
            },
            spread_config=payload.spread_config,
            state={"actions": []},
            last_interacted_at=utcnow(),
        )
        await self.repo.add(record)
        return record

    async def get(self, replay_id: uuid.UUID) -> ReplaySession:
        record = await self.repo.get(replay_id)
        if record is None or record.user_id != self.user_id:
            # A replay belongs to one user inside the workspace.
            raise NotFoundError("Replay session not found.")
        return record

    async def list(self) -> list[ReplaySession]:
        return await self.repo.list(
            ReplaySession.user_id == self.user_id,
            order_by=[ReplaySession.last_interacted_at.desc().nullslast()],
        )

    async def delete(self, replay_id: uuid.UUID) -> None:
        record = await self.get(replay_id)
        await self.repo.hard_delete(record.id)

    # ------------------------------------------------------------------
    # Stepping
    # ------------------------------------------------------------------

    async def step(self, replay_id: uuid.UUID, steps: int = 1) -> dict[str, Any]:
        record = await self.get(replay_id)
        if record.is_finished:
            raise UnprocessableStateError("This replay has reached the end of its data.")

        target = min(record.cursor_index + steps, record.total_bars - 1)
        record.cursor_index = target
        record.is_finished = target >= record.total_bars - 1
        record.last_interacted_at = utcnow()
        await self.session.flush()
        return await self.state(replay_id)

    async def submit_order(
        self, replay_id: uuid.UUID, payload: ReplayOrderRequest
    ) -> dict[str, Any]:
        """Queue a user order. It is executed by the simulator on the next step, using the same
        rules a strategy's order would follow."""
        record = await self.get(replay_id)
        if record.is_finished:
            raise UnprocessableStateError("This replay has finished.")

        actions = list(record.state.get("actions", []))
        actions.append(
            {
                "type": "order",
                "at_index": record.cursor_index,
                "side": payload.side.value,
                "quantity": str(payload.quantity),
                "order_type": payload.order_type.value,
                "limit_price": str(payload.limit_price) if payload.limit_price else None,
                "stop_price": str(payload.stop_price) if payload.stop_price else None,
            }
        )
        record.state = {**record.state, "actions": actions}
        record.last_interacted_at = utcnow()
        await self.session.flush()
        return await self.state(replay_id)

    async def set_protection(
        self, replay_id: uuid.UUID, stop_loss: Decimal | None, take_profit: Decimal | None
    ) -> dict[str, Any]:
        record = await self.get(replay_id)
        actions = list(record.state.get("actions", []))
        actions.append(
            {
                "type": "protection",
                "at_index": record.cursor_index,
                "stop_loss": str(stop_loss) if stop_loss is not None else None,
                "take_profit": str(take_profit) if take_profit is not None else None,
            }
        )
        record.state = {**record.state, "actions": actions}
        record.last_interacted_at = utcnow()
        await self.session.flush()
        return await self.state(replay_id)

    async def close_position(self, replay_id: uuid.UUID) -> dict[str, Any]:
        record = await self.get(replay_id)
        actions = list(record.state.get("actions", []))
        actions.append({"type": "close", "at_index": record.cursor_index})
        record.state = {**record.state, "actions": actions}
        record.last_interacted_at = utcnow()
        await self.session.flush()
        return await self.state(replay_id)

    # ------------------------------------------------------------------
    # State reconstruction
    # ------------------------------------------------------------------

    async def state(self, replay_id: uuid.UUID) -> dict[str, Any]:
        """Replay the recorded actions through the engine and return the resulting state."""
        record = await self.get(replay_id)
        instrument = await self.instruments.get(record.instrument_id)
        if instrument is None:
            raise NotFoundError("Instrument not found.")

        source = await self.market_data.get_source(record.market_data_source_id)
        series, _ = await self.market_data.get_bars(
            instrument.id,
            record.timeframe,
            source=source,
            start=record.start_at,
            end=record.end_at,
        )
        if len(series) == 0:
            raise UnprocessableStateError("The market data for this replay is no longer available.")

        config = BacktestConfig(
            symbol=instrument.symbol,
            initial_capital=record.initial_capital,
            currency=record.currency,
            contract_multiplier=instrument.contract_multiplier,
            tick_size=instrument.tick_size,
            lot_size=instrument.lot_size,
            allow_fractional=instrument.lot_size < ONE,
            execution_model=record.execution_model,
            commission=CommissionConfig.from_dict(record.commission_config),
            slippage=SlippageConfig.from_dict(record.slippage_config),
            spread=SpreadConfig.from_dict(record.spread_config),
        )
        portfolio = Portfolio(
            initial_capital=record.initial_capital,
            contract_multiplier=instrument.contract_multiplier,
        )
        broker = BrokerSimulator(config=config, portfolio=portfolio)

        actions_by_index: dict[int, list[dict[str, Any]]] = {}
        for action in record.state.get("actions", []):
            actions_by_index.setdefault(int(action["at_index"]), []).append(action)

        equity_curve: list[dict[str, Any]] = []
        cursor = min(record.cursor_index, len(series) - 1)

        for index in range(cursor + 1):
            bar = series[index]
            broker.open_bar(bar, index)

            for action in actions_by_index.get(index, []):
                self._apply_action(broker, action, bar)

            broker.close_bar(bar)
            equity_curve.append(
                {
                    "time": bar.opened_at.isoformat().replace("+00:00", "Z"),
                    "equity": _s(quantize_money(portfolio.equity(bar.close))),
                }
            )

        current = series[cursor]
        position = portfolio.position
        unrealized = position.unrealized(current.close) if position else None

        return {
            "id": str(record.id),
            "name": record.name,
            "timeframe": record.timeframe.value,
            "cursor_index": cursor,
            "total_bars": record.total_bars,
            "is_finished": record.is_finished,
            "currency": record.currency,
            "initial_capital": _s(record.initial_capital),
            "equity": _s(quantize_money(portfolio.equity(current.close))),
            "cash": _s(portfolio.cash()),
            "realized_pnl": _s(portfolio.realized_pnl),
            "unrealized_pnl": _s(unrealized),
            "position": _position_payload(position, current.close) if position else None,
            "working_orders": [_order_payload(order) for order in broker.working_orders],
            "closed_trades": [_trade_payload(trade) for trade in portfolio.closed_trades],
            "equity_curve": equity_curve,
            # Only bars up to the cursor — the future is never serialised.
            "visible_candles": MarketDataService.to_chart_payload(
                BarSeries([series[i] for i in range(cursor + 1)])
            ),
            "current_bar": {
                "time": current.opened_at.isoformat().replace("+00:00", "Z"),
                "open": _s(current.open),
                "high": _s(current.high),
                "low": _s(current.low),
                "close": _s(current.close),
            },
            "instrument": {"id": str(instrument.id), "symbol": instrument.symbol},
        }

    def _apply_action(self, broker: BrokerSimulator, action: dict[str, Any], bar) -> None:  # type: ignore[no-untyped-def]
        kind = action.get("type")
        if kind == "order":
            broker.submit(
                SimOrder(
                    side=OrderSide(action["side"]),
                    quantity=Decimal(action["quantity"]),
                    order_type=OrderType(action["order_type"]),
                    limit_price=(
                        quantize_price(Decimal(action["limit_price"]))
                        if action.get("limit_price")
                        else None
                    ),
                    stop_price=(
                        quantize_price(Decimal(action["stop_price"]))
                        if action.get("stop_price")
                        else None
                    ),
                    tag="manual",
                )
            )
        elif kind == "protection":
            broker.attach_protection(
                stop_loss=(
                    quantize_price(Decimal(action["stop_loss"]))
                    if action.get("stop_loss")
                    else None
                ),
                take_profit=(
                    quantize_price(Decimal(action["take_profit"]))
                    if action.get("take_profit")
                    else None
                ),
            )
        elif kind == "close":
            broker.close_position_at(bar.close, bar.opened_at, "manual_close")


def _s(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def _position_payload(position, mark: Decimal) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {
        "direction": position.direction.value,
        "quantity": _s(position.quantity),
        "average_price": _s(position.average_price),
        "stop_loss": _s(position.stop_loss),
        "take_profit": _s(position.take_profit),
        "unrealized_pnl": _s(position.unrealized(mark)),
        "opened_at": position.opened_at.isoformat(),
    }


def _order_payload(order: SimOrder) -> dict[str, Any]:
    return {
        "id": order.id,
        "side": order.side.value,
        "order_type": order.order_type.value,
        "quantity": _s(order.quantity),
        "limit_price": _s(order.limit_price),
        "stop_price": _s(order.stop_price),
        "intent": order.intent.value,
        "status": order.status.value,
    }


def _trade_payload(trade) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return {
        "sequence": trade.sequence,
        "direction": trade.direction.value,
        "entry_timestamp": trade.entry_timestamp.isoformat(),
        "exit_timestamp": trade.exit_timestamp.isoformat() if trade.exit_timestamp else None,
        "entry_price": _s(trade.entry_price),
        "exit_price": _s(trade.exit_price),
        "quantity": _s(trade.quantity),
        "net_pnl": _s(trade.net_pnl),
        "r_multiple": _s(trade.r_multiple),
        "exit_reason": trade.exit_reason,
    }


__all__ = ["MAX_REPLAY_BARS", "ReplayRepository", "ReplayService"]
