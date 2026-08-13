"""Trade journal service.

Everything that changes a trade goes through :meth:`TradeService.ingest_fills`. Manual entry,
CSV import and broker sync are all just different sources of fills, so a partial exit behaves
identically no matter where it came from — and there is exactly one place where P&L is computed.

Derived values written on every rebuild:

* ``risk_amount`` — from the initial stop when present, else the trader's declared risk
* ``r_multiple`` — ``net_pnl / risk_amount``, ``NULL`` when risk is unknown
* ``return_percentage`` — ``net_pnl / cost_basis * 100``
* ``holding_seconds``, ``session`` — computed in the *account's* timezone
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core.enums import (
    AssetType,
    AuditAction,
    Direction,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionStatus,
    TradeSource,
    TradeStatus,
)
from tradeloom.core.errors import ConflictError, NotFoundError, UnprocessableStateError
from tradeloom.core.money import ONE, ZERO, is_zero, quantize_money, quantize_price
from tradeloom.core.money import quantize_quantity as qq
from tradeloom.core.pagination import Page, PageParams
from tradeloom.core.timeutil import ensure_aware, session_for, utcnow
from tradeloom.models.account import Account
from tradeloom.models.instrument import Instrument
from tradeloom.models.strategy import Setup, Strategy, Tag
from tradeloom.models.trading import Order, Position, Trade, TradeTag
from tradeloom.repositories.trading import (
    AccountRepository,
    InstrumentRepository,
    OrderRepository,
    PositionRepository,
    SetupRepository,
    StrategyRepository,
    TagRepository,
    TradeRepository,
)
from tradeloom.schemas.trade import (
    BulkEditAction,
    BulkTagAction,
    FillInput,
    TradeCreate,
    TradeFilters,
    TradeUpdate,
)
from tradeloom.services.audit import AuditService, diff_changes
from tradeloom.services.trading import calculations
from tradeloom.services.trading.position_builder import (
    Fill,
    TradeAggregate,
    build_trades,
)

SORTABLE_FIELDS: dict[str, Any] = {
    "entry_timestamp": Trade.entry_timestamp,
    "exit_timestamp": Trade.exit_timestamp,
    "symbol": Trade.symbol,
    "net_pnl": Trade.net_pnl,
    "r_multiple": Trade.r_multiple,
    "quantity": Trade.quantity,
    "return_percentage": Trade.return_percentage,
    "holding_seconds": Trade.holding_seconds,
    "created_at": Trade.created_at,
}


class TradeService:
    def __init__(
        self,
        session: AsyncSession,
        organization_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID | None = None,
    ) -> None:
        self.session = session
        self.organization_id = organization_id
        self.actor_user_id = actor_user_id
        self.trades = TradeRepository(session, organization_id)
        self.orders = OrderRepository(session, organization_id)
        self.positions = PositionRepository(session, organization_id)
        self.accounts = AccountRepository(session, organization_id)
        self.instruments = InstrumentRepository(session, organization_id)
        self.tags = TagRepository(session, organization_id)
        self.setups = SetupRepository(session, organization_id)
        self.strategies = StrategyRepository(session, organization_id)
        self.audit = AuditService(session)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get(self, trade_id: uuid.UUID) -> Trade:
        trade = await self.trades.get(trade_id)
        if trade is None:
            raise NotFoundError("Trade not found.")
        return trade

    def _apply_filters(
        self, stmt: Select[tuple[Trade]], filters: TradeFilters
    ) -> Select[tuple[Trade]]:
        if filters.account_ids:
            stmt = stmt.where(Trade.account_id.in_(filters.account_ids))
        if filters.symbols:
            stmt = stmt.where(
                func.upper(Trade.symbol).in_([s.strip().upper() for s in filters.symbols])
            )
        if filters.directions:
            stmt = stmt.where(Trade.direction.in_(filters.directions))
        if filters.statuses:
            stmt = stmt.where(Trade.status.in_(filters.statuses))
        if filters.strategy_ids:
            stmt = stmt.where(Trade.strategy_id.in_(filters.strategy_ids))
        if filters.setup_ids:
            stmt = stmt.where(Trade.setup_id.in_(filters.setup_ids))
        if filters.sessions:
            stmt = stmt.where(Trade.session.in_(filters.sessions))
        if filters.asset_types:
            stmt = stmt.where(Trade.asset_type.in_(filters.asset_types))
        if filters.date_from:
            stmt = stmt.where(Trade.entry_timestamp >= ensure_aware(filters.date_from))
        if filters.date_to:
            stmt = stmt.where(Trade.entry_timestamp <= ensure_aware(filters.date_to))
        if filters.pnl_min is not None:
            stmt = stmt.where(Trade.net_pnl >= filters.pnl_min)
        if filters.pnl_max is not None:
            stmt = stmt.where(Trade.net_pnl <= filters.pnl_max)
        if filters.r_min is not None:
            stmt = stmt.where(Trade.r_multiple >= filters.r_min)
        if filters.r_max is not None:
            stmt = stmt.where(Trade.r_multiple <= filters.r_max)

        if filters.outcome == "winners":
            stmt = stmt.where(Trade.status == TradeStatus.CLOSED, Trade.net_pnl > 0)
        elif filters.outcome == "losers":
            stmt = stmt.where(Trade.status == TradeStatus.CLOSED, Trade.net_pnl < 0)
        elif filters.outcome == "breakeven":
            stmt = stmt.where(Trade.status == TradeStatus.CLOSED, Trade.net_pnl == 0)

        if filters.tag_ids:
            # EXISTS keeps the row count correct; a JOIN would multiply rows per matching tag.
            stmt = stmt.where(
                select(TradeTag.id)
                .where(TradeTag.trade_id == Trade.id, TradeTag.tag_id.in_(filters.tag_ids))
                .exists()
            )
        if filters.has_notes is not None:
            condition = and_(Trade.notes.isnot(None), func.length(Trade.notes) > 0)
            stmt = stmt.where(condition if filters.has_notes else ~condition)
        if filters.search:
            needle = f"%{filters.search.strip().lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(Trade.symbol).like(needle),
                    func.lower(func.coalesce(Trade.notes, "")).like(needle),
                    func.lower(func.coalesce(Trade.external_id, "")).like(needle),
                )
            )
        return stmt

    async def list(
        self, filters: TradeFilters, params: PageParams
    ) -> tuple[Page[Trade], dict[uuid.UUID, list[Tag]]]:
        base = self._apply_filters(self.trades.select(), filters)

        # Weekday/hour filters need the account timezone, which SQL cannot express portably.
        # They are applied in Python over the page, and the total reflects the SQL-level filter.
        count_stmt = select(func.count()).select_from(base.subquery())
        total = int(await self.session.scalar(count_stmt) or 0)

        column = SORTABLE_FIELDS.get(params.sort_by or "entry_timestamp", Trade.entry_timestamp)
        ordering = column.desc() if params.sort_dir == "desc" else column.asc()
        result = await self.session.execute(
            base.order_by(ordering, Trade.id.desc()).offset(params.offset).limit(params.limit)
        )
        items = list(result.scalars().all())

        if filters.weekdays or filters.hours:
            items = await self._filter_by_local_time(items, filters)

        tag_map = await self.tags.for_trades([trade.id for trade in items])
        return (
            Page(items=items, total=total, page=params.page, page_size=params.page_size),
            tag_map,
        )

    async def _filter_by_local_time(
        self, trades: list[Trade], filters: TradeFilters
    ) -> list[Trade]:
        timezones = await self._account_timezones({t.account_id for t in trades})
        kept: list[Trade] = []
        for trade in trades:
            local = ensure_aware(trade.entry_timestamp).astimezone(
                _zone(timezones.get(trade.account_id, "UTC"))
            )
            if filters.weekdays and local.weekday() not in filters.weekdays:
                continue
            if filters.hours and local.hour not in filters.hours:
                continue
            kept.append(trade)
        return kept

    async def _account_timezones(self, account_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not account_ids:
            return {}
        result = await self.session.execute(
            select(Account.id, Account.timezone).where(
                Account.organization_id == self.organization_id, Account.id.in_(account_ids)
            )
        )
        return {row[0]: row[1] for row in result.all()}

    async def labels_for(self, trades: Sequence[Trade]) -> dict[str, dict[uuid.UUID, str]]:
        """Batch-load display names so the journal table is one query per lookup table."""
        account_ids = {t.account_id for t in trades}
        strategy_ids = {t.strategy_id for t in trades if t.strategy_id}
        setup_ids = {t.setup_id for t in trades if t.setup_id}

        labels: dict[str, dict[uuid.UUID, str]] = {"account": {}, "strategy": {}, "setup": {}}
        if account_ids:
            rows = await self.session.execute(
                select(Account.id, Account.name).where(
                    Account.organization_id == self.organization_id, Account.id.in_(account_ids)
                )
            )
            labels["account"] = {row[0]: row[1] for row in rows.all()}
        if strategy_ids:
            rows = await self.session.execute(
                select(Strategy.id, Strategy.name).where(
                    Strategy.organization_id == self.organization_id,
                    Strategy.id.in_(strategy_ids),
                )
            )
            labels["strategy"] = {row[0]: row[1] for row in rows.all()}
        if setup_ids:
            rows = await self.session.execute(
                select(Setup.id, Setup.name).where(
                    Setup.organization_id == self.organization_id, Setup.id.in_(setup_ids)
                )
            )
            labels["setup"] = {row[0]: row[1] for row in rows.all()}
        return labels

    # ------------------------------------------------------------------
    # Fill ingestion — the single write path
    # ------------------------------------------------------------------

    async def ingest_fills(
        self,
        *,
        account: Account,
        symbol: str,
        asset_type: AssetType,
        fills: list[FillInput],
        source: TradeSource,
        instrument: Instrument | None = None,
        import_id: uuid.UUID | None = None,
        stop_loss: Decimal | None = None,
        take_profit: Decimal | None = None,
        risk_amount: Decimal | None = None,
        strategy_id: uuid.UUID | None = None,
        setup_id: uuid.UUID | None = None,
        notes: str | None = None,
        rating: int | None = None,
        custom_metadata: dict[str, Any] | None = None,
        external_id: str | None = None,
    ) -> list[Trade]:
        """Apply fills to an account's stream and persist the resulting trades.

        Returns every trade the fills touched (continued, closed, or newly opened).
        """
        if not fills:
            raise UnprocessableStateError("At least one fill is required.")

        symbol = symbol.strip().upper()
        multiplier = instrument.contract_multiplier if instrument else ONE

        order_rows = await self._persist_orders(
            account=account,
            symbol=symbol,
            instrument=instrument,
            fills=fills,
            import_id=import_id,
        )

        engine_fills = [
            Fill(
                timestamp=ensure_aware(payload.timestamp),
                side=payload.side,
                quantity=payload.quantity,
                price=payload.price,
                commission=payload.commission,
                fees=payload.fees,
                sequence=index,
                order_id=order.id,
                external_id=payload.external_id,
            )
            for index, (payload, order) in enumerate(zip(fills, order_rows, strict=True))
        ]

        existing = await self.trades.open_trade_for(
            account.id, instrument.id if instrument else None, symbol
        )
        initial = _aggregate_from_trade(existing, multiplier) if existing else None

        result = build_trades(engine_fills, contract_multiplier=multiplier, initial=initial)

        touched: list[Trade] = []
        for aggregate in result.all_trades:
            if initial is not None and aggregate is initial and existing is not None:
                trade = existing
                await self._apply_aggregate(trade, aggregate, account)
            else:
                trade = Trade(
                    organization_id=self.organization_id,
                    account_id=account.id,
                    instrument_id=instrument.id if instrument else None,
                    created_by_user_id=self.actor_user_id,
                    symbol=symbol,
                    asset_type=asset_type,
                    currency=account.currency,
                    contract_multiplier=multiplier,
                    direction=aggregate.direction,
                    source=source,
                    import_id=import_id,
                    external_id=external_id,
                    strategy_id=strategy_id,
                    setup_id=setup_id,
                    notes=notes,
                    rating=rating,
                    custom_metadata=custom_metadata or {},
                    stop_loss=stop_loss,
                    initial_stop_loss=stop_loss,
                    take_profit=take_profit,
                    entry_timestamp=aggregate.entry_timestamp or utcnow(),
                    entry_price=aggregate.average_entry_price,
                    quantity=aggregate.total_quantity,
                )
                if risk_amount is not None:
                    trade.risk_amount = quantize_money(risk_amount)
                await self.trades.add(trade)
                await self._apply_aggregate(trade, aggregate, account)
            touched.append(trade)
            await self._link_orders(trade, aggregate)

        await self._refresh_position(account, instrument, symbol, result.open_trade, touched)
        await self.session.flush()
        return touched

    async def _persist_orders(
        self,
        *,
        account: Account,
        symbol: str,
        instrument: Instrument | None,
        fills: list[FillInput],
        import_id: uuid.UUID | None,
    ) -> list[Order]:
        orders: list[Order] = []
        for payload in fills:
            order = Order(
                organization_id=self.organization_id,
                account_id=account.id,
                instrument_id=instrument.id if instrument else None,
                symbol=symbol,
                side=payload.side,
                order_type=payload.order_type,
                status=OrderStatus.FILLED,
                quantity=qq(payload.quantity),
                filled_quantity=qq(payload.quantity),
                average_fill_price=quantize_price(payload.price),
                limit_price=(
                    quantize_price(payload.price)
                    if payload.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT)
                    else None
                ),
                commission=quantize_money(payload.commission),
                fees=quantize_money(payload.fees),
                placed_at=ensure_aware(payload.timestamp),
                filled_at=ensure_aware(payload.timestamp),
                external_id=payload.external_id,
                import_id=import_id,
                notes=payload.notes,
            )
            self.session.add(order)
            orders.append(order)
        await self.session.flush()
        return orders

    async def _link_orders(self, trade: Trade, aggregate: TradeAggregate) -> None:
        """Attach each allocation back to its order, recording entry/exit and any split."""
        by_order: dict[uuid.UUID, list] = {}
        for allocation in aggregate.allocations:
            if allocation.order_id is not None:
                by_order.setdefault(allocation.order_id, []).append(allocation)

        if not by_order:
            return
        result = await self.session.execute(
            select(Order).where(
                Order.organization_id == self.organization_id, Order.id.in_(list(by_order))
            )
        )
        for order in result.scalars().all():
            allocations = by_order[order.id]
            order.trade_id = trade.id
            order.is_entry = allocations[0].is_entry
            existing = dict(order.allocations or {})
            existing[str(trade.id)] = {
                "quantity": str(sum(a.quantity for a in allocations)),
                "is_entry": allocations[0].is_entry,
            }
            order.allocations = existing

    async def _apply_aggregate(
        self, trade: Trade, aggregate: TradeAggregate, account: Account
    ) -> None:
        """Write the aggregate's numbers and every derived value onto the trade row."""
        trade.direction = aggregate.direction
        trade.entry_timestamp = aggregate.entry_timestamp or trade.entry_timestamp
        trade.entry_price = aggregate.average_entry_price
        trade.quantity = aggregate.total_quantity
        trade.closed_quantity = aggregate.closed_quantity
        trade.remaining_quantity = aggregate.open_quantity
        trade.gross_pnl = aggregate.gross_pnl
        trade.commission = aggregate.commission
        trade.fees = aggregate.fees
        trade.net_pnl = aggregate.net_pnl
        trade.status = aggregate.status
        trade.contract_multiplier = aggregate.contract_multiplier

        if aggregate.closed_quantity > 0:
            trade.exit_price = aggregate.average_exit_price
            trade.exit_timestamp = aggregate.exit_timestamp
        if not aggregate.is_closed:
            # A partially closed trade has no final exit yet.
            trade.exit_timestamp = None if aggregate.closed_quantity == 0 else trade.exit_timestamp

        trade.session = session_for(trade.entry_timestamp, account.timezone)
        trade.holding_seconds = aggregate.holding_seconds if aggregate.is_closed else None
        trade.return_percentage = aggregate.return_percentage() if aggregate.is_closed else None

        derived_risk = calculations.risk_amount(
            trade.entry_price,
            trade.initial_stop_loss or trade.stop_loss,
            trade.quantity,
            trade.direction,
            trade.contract_multiplier,
        )
        if derived_risk is not None:
            trade.risk_amount = derived_risk
        trade.r_multiple = (
            calculations.r_multiple(trade.net_pnl, trade.risk_amount)
            if aggregate.is_closed
            else None
        )

    async def _refresh_position(
        self,
        account: Account,
        instrument: Instrument | None,
        symbol: str,
        open_aggregate: TradeAggregate | None,
        touched: list[Trade],
    ) -> None:
        """Keep the ``positions`` cache in step with the open trade."""
        position = await self.positions.open_for(
            account.id, instrument.id if instrument else None, symbol
        )

        if open_aggregate is None or is_zero(open_aggregate.open_quantity):
            if position is not None:
                position.status = PositionStatus.CLOSED
                position.quantity = ZERO
                position.closed_at = utcnow()
                position.unrealized_pnl = None
            return

        open_trade = next((t for t in touched if t.status != TradeStatus.CLOSED), None)
        if position is None:
            position = Position(
                organization_id=self.organization_id,
                account_id=account.id,
                instrument_id=instrument.id if instrument else None,
                symbol=symbol,
                direction=open_aggregate.direction,
                status=PositionStatus.OPEN,
                opened_at=open_aggregate.entry_timestamp or utcnow(),
            )
            self.session.add(position)

        position.direction = open_aggregate.direction
        position.quantity = open_aggregate.open_quantity
        position.average_price = open_aggregate.average_entry_price
        position.contract_multiplier = open_aggregate.contract_multiplier
        position.realized_pnl = open_aggregate.gross_pnl
        position.trade_id = open_trade.id if open_trade else None
        position.status = PositionStatus.OPEN
        position.closed_at = None
        if position.last_price is not None:
            position.unrealized_pnl = open_aggregate.unrealized_pnl(position.last_price)

    # ------------------------------------------------------------------
    # Public write operations
    # ------------------------------------------------------------------

    async def create(self, payload: TradeCreate) -> list[Trade]:
        account = await self._require_account(payload.account_id)
        instrument = await self.instruments.by_symbol(payload.symbol)

        if payload.external_id:
            duplicate = await self.trades.by_external_id(account.id, payload.external_id)
            if duplicate is not None:
                raise ConflictError("A trade with that reference already exists on this account.")

        await self._validate_references(payload.strategy_id, payload.setup_id, payload.tag_ids)

        fills = payload.fills or self._fills_from_simple(payload)
        trades = await self.ingest_fills(
            account=account,
            symbol=payload.symbol,
            asset_type=payload.asset_type,
            fills=fills,
            source=TradeSource.MANUAL,
            instrument=instrument,
            stop_loss=payload.stop_loss,
            take_profit=payload.take_profit,
            risk_amount=payload.risk_amount,
            strategy_id=payload.strategy_id,
            setup_id=payload.setup_id,
            notes=payload.notes,
            rating=payload.rating,
            custom_metadata=payload.custom_metadata,
            external_id=payload.external_id,
        )
        if payload.tag_ids:
            for trade in trades:
                await self.set_tags(trade, payload.tag_ids)

        await self.audit.record(
            AuditAction.CREATED,
            organization_id=self.organization_id,
            actor_user_id=self.actor_user_id,
            entity_type="trade",
            entity_id=trades[0].id if trades else None,
            summary=f"Recorded {payload.symbol.upper()} trade",
        )
        return trades

    def _fills_from_simple(self, payload: TradeCreate) -> list[FillInput]:
        assert payload.direction and payload.entry_price and payload.quantity
        entry_side = OrderSide.BUY if payload.direction is Direction.LONG else OrderSide.SELL
        fills = [
            FillInput(
                side=entry_side,
                quantity=payload.quantity,
                price=payload.entry_price,
                timestamp=payload.entry_timestamp or utcnow(),
                commission=payload.commission,
                fees=payload.fees,
            )
        ]
        if payload.exit_price is not None and payload.exit_timestamp is not None:
            fills.append(
                FillInput(
                    side=entry_side.opposite,
                    quantity=payload.quantity,
                    price=payload.exit_price,
                    timestamp=payload.exit_timestamp,
                    commission=ZERO,
                    fees=ZERO,
                )
            )
        return fills

    async def update(self, trade_id: uuid.UUID, payload: TradeUpdate) -> Trade:
        trade = await self.get(trade_id)
        before = trade.to_dict()
        data = payload.model_dump(exclude_unset=True)

        tag_ids = data.pop("tag_ids", None)
        await self._validate_references(
            data.get("strategy_id"), data.get("setup_id"), tag_ids or []
        )

        stop_changed = "stop_loss" in data
        for field, value in data.items():
            setattr(trade, field, value)

        if stop_changed and trade.initial_stop_loss is None:
            trade.initial_stop_loss = trade.stop_loss

        # Risk and R depend on the stop, so recompute rather than leave a stale number behind.
        derived = calculations.risk_amount(
            trade.entry_price,
            trade.initial_stop_loss or trade.stop_loss,
            trade.quantity,
            trade.direction,
            trade.contract_multiplier,
        )
        if derived is not None and "risk_amount" not in data:
            trade.risk_amount = derived
        if trade.status is TradeStatus.CLOSED:
            trade.r_multiple = calculations.r_multiple(trade.net_pnl, trade.risk_amount)

        if tag_ids is not None:
            await self.set_tags(trade, tag_ids)

        await self.session.flush()
        await self.audit.record(
            AuditAction.UPDATED,
            organization_id=self.organization_id,
            actor_user_id=self.actor_user_id,
            entity_type="trade",
            entity_id=trade.id,
            changes=diff_changes(before, trade.to_dict()),
        )
        return trade

    async def delete(self, trade_id: uuid.UUID) -> None:
        trade = await self.get(trade_id)
        await self.trades.soft_delete(trade.id)
        # Detach the orders so a later rebuild does not resurrect the trade.
        await self.session.execute(
            delete(TradeTag).where(
                TradeTag.trade_id == trade.id, TradeTag.organization_id == self.organization_id
            )
        )
        position = await self.positions.open_for(
            trade.account_id, trade.instrument_id, trade.symbol
        )
        if position is not None and position.trade_id == trade.id:
            position.status = PositionStatus.CLOSED
            position.quantity = ZERO
            position.closed_at = utcnow()
        await self.audit.record(
            AuditAction.DELETED,
            organization_id=self.organization_id,
            actor_user_id=self.actor_user_id,
            entity_type="trade",
            entity_id=trade.id,
            summary=f"Deleted {trade.symbol} trade",
        )

    async def duplicate(self, trade_id: uuid.UUID) -> Trade:
        """Copy a trade's plan into a new open trade for re-entry, without copying its P&L."""
        source = await self.get(trade_id)
        account = await self._require_account(source.account_id)
        instrument = (
            await self.instruments.get(source.instrument_id) if source.instrument_id else None
        )
        entry_side = OrderSide.BUY if source.direction is Direction.LONG else OrderSide.SELL
        trades = await self.ingest_fills(
            account=account,
            symbol=source.symbol,
            asset_type=source.asset_type,
            fills=[
                FillInput(
                    side=entry_side,
                    quantity=source.quantity,
                    price=source.entry_price,
                    timestamp=utcnow(),
                )
            ],
            source=TradeSource.MANUAL,
            instrument=instrument,
            stop_loss=source.stop_loss,
            take_profit=source.take_profit,
            strategy_id=source.strategy_id,
            setup_id=source.setup_id,
            notes=source.notes,
            custom_metadata={**source.custom_metadata, "duplicated_from": str(source.id)},
        )
        return trades[-1]

    # ------------------------------------------------------------------
    # Tags and bulk operations
    # ------------------------------------------------------------------

    async def set_tags(self, trade: Trade, tag_ids: list[uuid.UUID]) -> None:
        valid = {tag.id for tag in await self.tags.get_many(tag_ids)}
        await self.session.execute(
            delete(TradeTag).where(
                TradeTag.trade_id == trade.id, TradeTag.organization_id == self.organization_id
            )
        )
        for tag_id in valid:
            self.session.add(
                TradeTag(organization_id=self.organization_id, trade_id=trade.id, tag_id=tag_id)
            )
        await self.session.flush()

    async def bulk_tag(self, payload: BulkTagAction) -> int:
        trades = await self.trades.get_many(payload.trade_ids)
        add_ids = {tag.id for tag in await self.tags.get_many(payload.add_tag_ids)}
        remove_ids = {tag.id for tag in await self.tags.get_many(payload.remove_tag_ids)}

        for trade in trades:
            if remove_ids:
                await self.session.execute(
                    delete(TradeTag).where(
                        TradeTag.trade_id == trade.id,
                        TradeTag.tag_id.in_(remove_ids),
                        TradeTag.organization_id == self.organization_id,
                    )
                )
            if add_ids:
                existing = {
                    row[0]
                    for row in (
                        await self.session.execute(
                            select(TradeTag.tag_id).where(TradeTag.trade_id == trade.id)
                        )
                    ).all()
                }
                for tag_id in add_ids - existing:
                    self.session.add(
                        TradeTag(
                            organization_id=self.organization_id,
                            trade_id=trade.id,
                            tag_id=tag_id,
                        )
                    )
        await self.session.flush()
        return len(trades)

    async def bulk_edit(self, payload: BulkEditAction) -> int:
        trades = await self.trades.get_many(payload.trade_ids)
        await self._validate_references(payload.strategy_id, payload.setup_id, [])
        for trade in trades:
            if payload.strategy_id is not None:
                trade.strategy_id = payload.strategy_id
            if payload.setup_id is not None:
                trade.setup_id = payload.setup_id
            if payload.rating is not None:
                trade.rating = payload.rating
            if payload.append_note:
                trade.notes = (
                    f"{trade.notes}\n\n{payload.append_note}"
                    if trade.notes
                    else payload.append_note
                )
        await self.session.flush()
        await self.audit.record(
            AuditAction.UPDATED,
            organization_id=self.organization_id,
            actor_user_id=self.actor_user_id,
            entity_type="trade",
            summary=f"Bulk edited {len(trades)} trades",
        )
        return len(trades)

    async def bulk_delete(self, trade_ids: list[uuid.UUID]) -> int:
        trades = await self.trades.get_many(trade_ids)
        for trade in trades:
            await self.delete(trade.id)
        return len(trades)

    # ------------------------------------------------------------------
    # Marks
    # ------------------------------------------------------------------

    async def apply_marks(self, prices: dict[str, Decimal]) -> int:
        """Set mark prices on open positions and recompute unrealised P&L.

        Symbols not supplied keep their previous mark; nothing is invented.
        """
        normalized = {
            symbol.strip().upper(): quantize_price(price) for symbol, price in prices.items()
        }
        positions = await self.positions.open_positions()
        updated = 0
        for position in positions:
            price = normalized.get(position.symbol.upper())
            if price is None:
                continue
            delta = price - position.average_price
            position.last_price = price
            position.unrealized_pnl = quantize_money(
                delta
                * position.quantity
                * position.contract_multiplier
                * Decimal(position.direction.sign)
            )
            position.marked_at = utcnow()
            updated += 1
        await self.session.flush()
        return updated

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _require_account(self, account_id: uuid.UUID) -> Account:
        account = await self.accounts.get(account_id)
        if account is None:
            raise NotFoundError("Account not found.")
        return account

    async def _validate_references(
        self,
        strategy_id: uuid.UUID | None,
        setup_id: uuid.UUID | None,
        tag_ids: list[uuid.UUID],
    ) -> None:
        """Every referenced id must belong to this tenant.

        Client-supplied ids are never trusted.
        """
        if strategy_id is not None and not await self.strategies.exists(strategy_id):
            raise NotFoundError("Strategy not found.")
        if setup_id is not None and not await self.setups.exists(setup_id):
            raise NotFoundError("Setup not found.")
        if tag_ids:
            found = await self.tags.get_many(list(tag_ids))
            if len(found) != len(set(tag_ids)):
                raise NotFoundError("One or more tags were not found.")


def _zone(name: str):  # type: ignore[no-untyped-def]
    from tradeloom.core.timeutil import get_zone

    return get_zone(name)


def _aggregate_from_trade(trade: Trade, multiplier: Decimal) -> TradeAggregate:
    """Rehydrate an in-progress trade so new fills continue it instead of starting fresh."""
    aggregate = TradeAggregate(
        direction=trade.direction,
        contract_multiplier=trade.contract_multiplier or multiplier,
        entry_timestamp=trade.entry_timestamp,
        exit_timestamp=trade.exit_timestamp,
        average_entry_price=trade.entry_price,
        average_exit_price=trade.exit_price,
        total_quantity=trade.quantity,
        closed_quantity=trade.closed_quantity,
        open_quantity=trade.remaining_quantity,
        gross_pnl=trade.gross_pnl,
        commission=trade.commission,
        fees=trade.fees,
    )
    return aggregate


__all__ = ["SORTABLE_FIELDS", "TradeService"]
