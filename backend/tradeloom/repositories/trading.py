"""Tenant-scoped repositories for the trading domain."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.sql.elements import ColumnElement

from tradeloom.core.enums import PositionStatus, TradeStatus
from tradeloom.models.account import Account, AccountSnapshot, CashTransaction
from tradeloom.models.instrument import Instrument, InstrumentAlias
from tradeloom.models.strategy import Setup, Strategy, StrategyVersion, Tag
from tradeloom.models.trading import Order, Position, Trade, TradeTag
from tradeloom.repositories.base import TenantRepository


class AccountRepository(TenantRepository[Account]):
    model = Account

    async def by_name(self, name: str) -> Account | None:
        result = await self.session.execute(
            self._base_select().where(func.lower(Account.name) == name.strip().lower())
        )
        return result.scalar_one_or_none()

    async def default_account(self) -> Account | None:
        result = await self.session.execute(
            self._base_select()
            .order_by(Account.is_default.desc(), Account.created_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def clear_default_flag(self, *, except_id: uuid.UUID | None = None) -> None:
        accounts = await self.list(Account.is_default.is_(True))
        for account in accounts:
            if except_id is None or account.id != except_id:
                account.is_default = False


class CashTransactionRepository(TenantRepository[CashTransaction]):
    model = CashTransaction
    supports_soft_delete = False

    async def for_account(self, account_id: uuid.UUID) -> list[CashTransaction]:
        return await self.list(
            CashTransaction.account_id == account_id,
            order_by=[CashTransaction.occurred_at.asc()],
        )

    async def totals(self, account_id: uuid.UUID) -> dict[str, Decimal]:
        result = await self.session.execute(
            select(CashTransaction.kind, func.count(), CashTransaction.amount).where(
                CashTransaction.organization_id == self.organization_id,
                CashTransaction.account_id == account_id,
            )
        )
        totals: dict[str, Decimal] = {}
        for kind, _count, amount in result.all():
            totals[kind] = totals.get(kind, Decimal(0)) + amount
        return totals


class AccountSnapshotRepository(TenantRepository[AccountSnapshot]):
    model = AccountSnapshot
    supports_soft_delete = False


class InstrumentRepository(TenantRepository[Instrument]):
    """Instruments are visible when they belong to this tenant *or* to the shared catalogue."""

    model = Instrument
    supports_soft_delete = False

    def _tenant_filter(self) -> ColumnElement[bool]:
        return or_(
            Instrument.organization_id == self.organization_id,
            Instrument.organization_id.is_(None),
        )

    async def by_symbol(self, symbol: str) -> Instrument | None:
        normalized = symbol.strip().upper()
        result = await self.session.execute(
            self._base_select()
            .where(func.upper(Instrument.symbol) == normalized)
            # Prefer a tenant-private override over the shared catalogue entry.
            .order_by(Instrument.organization_id.is_(None).asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def resolve_alias(self, alias: str, source: str = "*") -> Instrument | None:
        normalized = _normalize_alias(alias)
        result = await self.session.execute(
            select(Instrument)
            .join(InstrumentAlias, InstrumentAlias.instrument_id == Instrument.id)
            .where(
                InstrumentAlias.alias_normalized == normalized,
                or_(
                    InstrumentAlias.organization_id == self.organization_id,
                    InstrumentAlias.organization_id.is_(None),
                ),
                or_(InstrumentAlias.source == source, InstrumentAlias.source == "*"),
            )
            .order_by(InstrumentAlias.organization_id.is_(None).asc())
            .limit(1)
        )
        return result.scalar_one_or_none()


def _normalize_alias(alias: str) -> str:
    return "".join(ch for ch in alias.strip().upper() if ch.isalnum())


class TradeRepository(TenantRepository[Trade]):
    model = Trade

    async def open_trade_for(
        self, account_id: uuid.UUID, instrument_id: uuid.UUID | None, symbol: str
    ) -> Trade | None:
        """The single open round trip for an account/instrument, if any."""
        conditions = [
            Trade.account_id == account_id,
            Trade.status.in_([TradeStatus.OPEN, TradeStatus.PARTIALLY_CLOSED]),
        ]
        if instrument_id is not None:
            conditions.append(Trade.instrument_id == instrument_id)
        else:
            conditions.append(Trade.symbol == symbol)
        result = await self.session.execute(
            self._base_select().where(and_(*conditions)).order_by(Trade.entry_timestamp.asc())
        )
        return result.scalars().first()

    async def by_external_id(self, account_id: uuid.UUID, external_id: str) -> Trade | None:
        result = await self.session.execute(
            self._base_select().where(
                Trade.account_id == account_id, Trade.external_id == external_id
            )
        )
        return result.scalar_one_or_none()

    def closed_select(self) -> Select[tuple[Trade]]:
        return self._base_select().where(Trade.status == TradeStatus.CLOSED)

    async def created_by_import(self, import_id: uuid.UUID) -> list[Trade]:
        return await self.list(Trade.import_id == import_id, include_deleted=True)


class OrderRepository(TenantRepository[Order]):
    model = Order

    async def for_trade(self, trade_id: uuid.UUID) -> list[Order]:
        return await self.list(Order.trade_id == trade_id, order_by=[Order.placed_at.asc()])

    async def for_account(self, account_id: uuid.UUID) -> list[Order]:
        return await self.list(
            Order.account_id == account_id, order_by=[Order.placed_at.asc(), Order.created_at.asc()]
        )

    async def by_external_ids(
        self, account_id: uuid.UUID, external_ids: list[str]
    ) -> dict[str, Order]:
        """Bulk duplicate lookup used by the importer."""
        if not external_ids:
            return {}
        found: dict[str, Order] = {}
        chunk_size = 500
        for start in range(0, len(external_ids), chunk_size):
            chunk = external_ids[start : start + chunk_size]
            result = await self.session.execute(
                self._base_select(include_deleted=True).where(
                    Order.account_id == account_id, Order.external_id.in_(chunk)
                )
            )
            for order in result.scalars().all():
                if order.external_id:
                    found[order.external_id] = order
        return found


class PositionRepository(TenantRepository[Position]):
    model = Position
    supports_soft_delete = False

    async def open_for(
        self, account_id: uuid.UUID, instrument_id: uuid.UUID | None, symbol: str
    ) -> Position | None:
        conditions = [
            Position.account_id == account_id,
            Position.status == PositionStatus.OPEN,
        ]
        if instrument_id is not None:
            conditions.append(Position.instrument_id == instrument_id)
        else:
            conditions.append(Position.symbol == symbol)
        result = await self.session.execute(self._base_select().where(and_(*conditions)))
        return result.scalars().first()

    async def open_positions(self, account_ids: list[uuid.UUID] | None = None) -> list[Position]:
        conditions: list[ColumnElement[bool]] = [Position.status == PositionStatus.OPEN]
        if account_ids:
            conditions.append(Position.account_id.in_(account_ids))
        return await self.list(*conditions, order_by=[Position.opened_at.desc()])


class TagRepository(TenantRepository[Tag]):
    model = Tag

    async def by_slug(self, slug: str) -> Tag | None:
        result = await self.session.execute(self._base_select().where(Tag.slug == slug))
        return result.scalar_one_or_none()

    async def for_trade(self, trade_id: uuid.UUID) -> list[Tag]:
        result = await self.session.execute(
            select(Tag)
            .join(TradeTag, TradeTag.tag_id == Tag.id)
            .where(TradeTag.trade_id == trade_id, Tag.organization_id == self.organization_id)
            .order_by(Tag.name.asc())
        )
        return list(result.scalars().all())

    async def for_trades(self, trade_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[Tag]]:
        """Batched tag load so a trade table does not issue one query per row."""
        if not trade_ids:
            return {}
        result = await self.session.execute(
            select(TradeTag.trade_id, Tag)
            .join(Tag, Tag.id == TradeTag.tag_id)
            .where(
                TradeTag.trade_id.in_(trade_ids),
                Tag.organization_id == self.organization_id,
                Tag.deleted_at.is_(None),
            )
            .order_by(Tag.name.asc())
        )
        mapping: dict[uuid.UUID, list[Tag]] = {}
        for trade_id, tag in result.all():
            mapping.setdefault(trade_id, []).append(tag)
        return mapping


class SetupRepository(TenantRepository[Setup]):
    model = Setup


class StrategyRepository(TenantRepository[Strategy]):
    model = Strategy

    async def versions(self, strategy_id: uuid.UUID) -> list[StrategyVersion]:
        result = await self.session.execute(
            select(StrategyVersion)
            .where(
                StrategyVersion.strategy_id == strategy_id,
                StrategyVersion.organization_id == self.organization_id,
            )
            .order_by(StrategyVersion.version.desc())
        )
        return list(result.scalars().all())


class StrategyVersionRepository(TenantRepository[StrategyVersion]):
    model = StrategyVersion
    supports_soft_delete = False


__all__ = [
    "AccountRepository",
    "AccountSnapshotRepository",
    "CashTransactionRepository",
    "InstrumentRepository",
    "OrderRepository",
    "PositionRepository",
    "SetupRepository",
    "StrategyRepository",
    "StrategyVersionRepository",
    "TagRepository",
    "TradeRepository",
]
