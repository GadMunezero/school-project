"""Data export.

CSV for the things a trader opens in a spreadsheet, JSON for a complete portable snapshot. Both
are generated from real rows and streamed as text — no placeholder columns, no "coming soon".
"""

from __future__ import annotations

import csv
import io
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core.timeutil import utcnow
from tradeloom.models.account import Account, CashTransaction
from tradeloom.models.journal import JournalEntry
from tradeloom.models.strategy import Setup, Strategy, Tag
from tradeloom.models.trading import Order, Trade
from tradeloom.schemas.trade import TradeFilters
from tradeloom.services.analytics import AnalyticsService
from tradeloom.services.trades import TradeService

TRADE_COLUMNS = [
    "id",
    "account",
    "symbol",
    "asset_type",
    "direction",
    "status",
    "entry_timestamp",
    "exit_timestamp",
    "entry_price",
    "exit_price",
    "quantity",
    "closed_quantity",
    "remaining_quantity",
    "stop_loss",
    "take_profit",
    "commission",
    "fees",
    "gross_pnl",
    "net_pnl",
    "risk_amount",
    "r_multiple",
    "return_percentage",
    "holding_seconds",
    "mfe_amount",
    "mae_amount",
    "session",
    "strategy",
    "setup",
    "tags",
    "notes",
    "external_id",
]


def _value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)


class ExportService:
    def __init__(self, session: AsyncSession, organization_id: uuid.UUID) -> None:
        self.session = session
        self.organization_id = organization_id
        self.trades = TradeService(session, organization_id)

    async def trades_csv(self, filters: TradeFilters, limit: int = 100_000) -> str:
        stmt = (
            self.trades._apply_filters(self.trades.trades.select(), filters)
            .order_by(Trade.entry_timestamp.asc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())

        labels = await self.trades.labels_for(rows)
        tag_map = await self.trades.tags.for_trades([trade.id for trade in rows])

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(TRADE_COLUMNS)
        for trade in rows:
            writer.writerow(
                [
                    trade.id,
                    labels.get("account", {}).get(trade.account_id, ""),
                    trade.symbol,
                    _value(trade.asset_type),
                    _value(trade.direction),
                    _value(trade.status),
                    _value(trade.entry_timestamp),
                    _value(trade.exit_timestamp),
                    _value(trade.entry_price),
                    _value(trade.exit_price),
                    _value(trade.quantity),
                    _value(trade.closed_quantity),
                    _value(trade.remaining_quantity),
                    _value(trade.stop_loss),
                    _value(trade.take_profit),
                    _value(trade.commission),
                    _value(trade.fees),
                    _value(trade.gross_pnl),
                    _value(trade.net_pnl),
                    _value(trade.risk_amount),
                    _value(trade.r_multiple),
                    _value(trade.return_percentage),
                    _value(trade.holding_seconds),
                    _value(trade.mfe_amount),
                    _value(trade.mae_amount),
                    _value(trade.session),
                    labels.get("strategy", {}).get(trade.strategy_id, ""),
                    labels.get("setup", {}).get(trade.setup_id, ""),
                    "; ".join(tag.name for tag in tag_map.get(trade.id, [])),
                    (trade.notes or "").replace("\n", " "),
                    trade.external_id or "",
                ]
            )
        return buffer.getvalue()

    async def orders_csv(self, limit: int = 200_000) -> str:
        result = await self.session.execute(
            select(Order)
            .where(Order.organization_id == self.organization_id, Order.deleted_at.is_(None))
            .order_by(Order.placed_at.asc())
            .limit(limit)
        )
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(
            [
                "id",
                "trade_id",
                "symbol",
                "side",
                "order_type",
                "status",
                "quantity",
                "filled_quantity",
                "average_fill_price",
                "commission",
                "fees",
                "placed_at",
                "filled_at",
                "is_entry",
                "external_id",
            ]
        )
        for order in result.scalars().all():
            writer.writerow(
                [
                    order.id,
                    order.trade_id or "",
                    order.symbol,
                    _value(order.side),
                    _value(order.order_type),
                    _value(order.status),
                    _value(order.quantity),
                    _value(order.filled_quantity),
                    _value(order.average_fill_price),
                    _value(order.commission),
                    _value(order.fees),
                    _value(order.placed_at),
                    _value(order.filled_at),
                    "" if order.is_entry is None else str(order.is_entry).lower(),
                    order.external_id or "",
                ]
            )
        return buffer.getvalue()

    async def full_export(self) -> dict[str, Any]:
        """Everything in the workspace, in one portable JSON document."""
        accounts = await self._rows(Account)
        trades = await self._rows(Trade)
        orders = await self._rows(Order)
        strategies = await self._rows(Strategy)
        setups = await self._rows(Setup)
        tags = await self._rows(Tag)
        journal = await self._rows(JournalEntry)
        cash = await self._rows(CashTransaction, soft_delete=False)

        analytics = await AnalyticsService(self.session, self.organization_id).analyse(
            TradeFilters()
        )

        return {
            "exported_at": utcnow().isoformat(),
            "format_version": 1,
            "organization_id": str(self.organization_id),
            "counts": {
                "accounts": len(accounts),
                "trades": len(trades),
                "orders": len(orders),
                "strategies": len(strategies),
                "setups": len(setups),
                "tags": len(tags),
                "journal_entries": len(journal),
                "cash_transactions": len(cash),
            },
            "accounts": accounts,
            "trades": trades,
            "orders": orders,
            "strategies": strategies,
            "setups": setups,
            "tags": tags,
            "journal_entries": journal,
            "cash_transactions": cash,
            "analytics_summary": analytics.metrics,
        }

    async def _rows(self, model, soft_delete: bool = True) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
        stmt = select(model).where(model.organization_id == self.organization_id)
        if soft_delete and hasattr(model, "deleted_at"):
            stmt = stmt.where(model.deleted_at.is_(None))
        result = await self.session.execute(stmt)
        return [
            {key: _value(value) for key, value in row.to_dict().items()}
            for row in result.scalars().all()
        ]


__all__ = ["TRADE_COLUMNS", "ExportService"]
