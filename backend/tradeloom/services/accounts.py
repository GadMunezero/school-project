"""Account lifecycle and the cash ledger.

The balance rule, stated once and enforced here:

    current_balance = initial_balance + Σ cash_transactions.amount + Σ closed-trade net P&L

``current_balance`` on the account row is a *cache* of that expression. It is recomputed by
:meth:`AccountService.recalculate` whenever the inputs change (a trade closes, a deposit is
recorded, an import commits or reverts) — never incremented blindly, so a double-applied event
cannot drift the balance permanently.
"""

from __future__ import annotations

import builtins
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core.enums import AccountStatus, AuditAction, TradeStatus
from tradeloom.core.errors import ConflictError, NotFoundError, UnprocessableStateError
from tradeloom.core.money import ZERO, quantize_money, safe_div, settle
from tradeloom.core.pagination import Page, PageParams
from tradeloom.core.timeutil import utcnow
from tradeloom.models.account import Account, CashTransaction
from tradeloom.models.trading import Position, Trade
from tradeloom.repositories.trading import (
    AccountRepository,
    CashTransactionRepository,
    PositionRepository,
)
from tradeloom.schemas.account import AccountCreate, AccountStats, AccountUpdate
from tradeloom.services.audit import AuditService, diff_changes
from tradeloom.services.entitlements import EntitlementService

#: Cash kinds that reduce the balance. Everything else increases it.
_NEGATIVE_KINDS = {"withdrawal", "fee", "payout"}


class AccountService:
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
        self.accounts = AccountRepository(session, organization_id)
        self.cash = CashTransactionRepository(session, organization_id)
        self.positions = PositionRepository(session, organization_id)
        self.audit = AuditService(session)

    # -- reads ---------------------------------------------------------------

    async def get(self, account_id: uuid.UUID) -> Account:
        account = await self.accounts.get(account_id)
        if account is None:
            # 404 rather than 403: a foreign account must be indistinguishable from a missing one.
            raise NotFoundError("Account not found.")
        return account

    async def list(self, params: PageParams, *, include_archived: bool = False) -> Page[Account]:
        filters = []
        if not include_archived:
            filters.append(Account.status == AccountStatus.ACTIVE)
        return await self.accounts.paginate(
            params,
            *filters,
            order_by=[Account.is_default.desc(), Account.name.asc()],
        )

    async def list_all(self) -> builtins.list[Account]:
        return await self.accounts.list(order_by=[Account.is_default.desc(), Account.name.asc()])

    # -- writes --------------------------------------------------------------

    async def create(
        self, payload: AccountCreate, *, entitlements: EntitlementService | None = None
    ) -> Account:
        existing = await self.accounts.by_name(payload.name)
        if existing is not None:
            raise ConflictError("An account with that name already exists in this workspace.")

        if entitlements is not None:
            current = await self.accounts.count()
            await entitlements.require_within_limit(self.organization_id, "max_accounts", current)

        account = Account(
            organization_id=self.organization_id,
            created_by_user_id=self.actor_user_id,
            name=payload.name.strip(),
            broker=payload.broker,
            account_type=payload.account_type,
            currency=payload.currency,
            initial_balance=quantize_money(payload.initial_balance),
            current_balance=quantize_money(payload.initial_balance),
            leverage=payload.leverage,
            timezone=payload.timezone,
            commission_model=payload.commission_model,
            commission_config=payload.commission_config,
            default_risk_percent=payload.default_risk_percent,
            external_reference=payload.external_reference,
            notes=payload.notes,
            status=AccountStatus.ACTIVE,
            is_default=payload.is_default,
            last_recalculated_at=utcnow(),
        )
        if payload.is_default:
            await self.accounts.clear_default_flag()
        await self.accounts.add(account)

        # The first account in a workspace is the default whether or not the client asked.
        if await self.accounts.count() == 1:
            account.is_default = True

        await self.audit.record(
            AuditAction.CREATED,
            organization_id=self.organization_id,
            actor_user_id=self.actor_user_id,
            entity_type="account",
            entity_id=account.id,
            summary=f"Created account {account.name}",
        )
        return account

    async def update(self, account_id: uuid.UUID, payload: AccountUpdate) -> Account:
        account = await self.get(account_id)
        before = account.to_dict()

        data = payload.model_dump(exclude_unset=True)
        if data.get("name"):
            clash = await self.accounts.by_name(data["name"])
            if clash is not None and clash.id != account.id:
                raise ConflictError("An account with that name already exists in this workspace.")
        if data.get("is_default"):
            await self.accounts.clear_default_flag(except_id=account.id)

        rebuild_needed = "initial_balance" in data
        for field, value in data.items():
            if field == "initial_balance":
                value = quantize_money(value)
            setattr(account, field, value)
        await self.session.flush()

        if rebuild_needed:
            await self.recalculate(account.id)

        await self.audit.record(
            AuditAction.UPDATED,
            organization_id=self.organization_id,
            actor_user_id=self.actor_user_id,
            entity_type="account",
            entity_id=account.id,
            summary=f"Updated account {account.name}",
            changes=diff_changes(before, account.to_dict()),
        )
        return account

    async def archive(self, account_id: uuid.UUID) -> Account:
        """Archiving keeps history intact. Only fully-closed accounts can be archived."""
        account = await self.get(account_id)
        open_positions = await self.positions.count(
            Position.account_id == account.id, Position.status == "open"
        )
        if open_positions:
            raise UnprocessableStateError(
                "Close or remove the open positions on this account before archiving it."
            )
        account.status = AccountStatus.ARCHIVED
        account.is_default = False
        await self.session.flush()
        await self.audit.record(
            AuditAction.UPDATED,
            organization_id=self.organization_id,
            actor_user_id=self.actor_user_id,
            entity_type="account",
            entity_id=account.id,
            summary=f"Archived account {account.name}",
        )
        return account

    async def delete(self, account_id: uuid.UUID) -> None:
        """Soft delete. Trades stay in the database until the retention job removes them, so an
        accidental deletion is recoverable by support."""
        account = await self.get(account_id)
        await self.accounts.soft_delete(account.id)
        await self.audit.record(
            AuditAction.DELETED,
            organization_id=self.organization_id,
            actor_user_id=self.actor_user_id,
            entity_type="account",
            entity_id=account.id,
            summary=f"Deleted account {account.name}",
        )

    # -- cash ledger ---------------------------------------------------------

    async def add_cash_transaction(
        self,
        account_id: uuid.UUID,
        *,
        kind: str,
        amount: Decimal,
        occurred_at,
        description: str | None = None,
        import_id: uuid.UUID | None = None,
    ) -> CashTransaction:
        account = await self.get(account_id)
        magnitude = abs(quantize_money(amount))
        if magnitude == 0:
            raise UnprocessableStateError("Amount must be greater than zero.")

        # The sign comes from the kind, never from the client's number.
        signed = -magnitude if kind in _NEGATIVE_KINDS else magnitude
        transaction = CashTransaction(
            organization_id=self.organization_id,
            account_id=account.id,
            kind=kind,
            amount=settle(signed, account.currency),
            currency=account.currency,
            occurred_at=occurred_at,
            description=description,
            import_id=import_id,
        )
        await self.cash.add(transaction)
        await self.recalculate(account.id)
        await self.audit.record(
            AuditAction.CREATED,
            organization_id=self.organization_id,
            actor_user_id=self.actor_user_id,
            entity_type="cash_transaction",
            entity_id=transaction.id,
            summary=f"{kind} of {transaction.amount} {account.currency}",
        )
        return transaction

    async def list_cash_transactions(
        self, account_id: uuid.UUID, params: PageParams
    ) -> Page[CashTransaction]:
        await self.get(account_id)
        return await self.cash.paginate(
            params,
            CashTransaction.account_id == account_id,
            order_by=[CashTransaction.occurred_at.desc()],
        )

    async def delete_cash_transaction(
        self, account_id: uuid.UUID, transaction_id: uuid.UUID
    ) -> None:
        await self.get(account_id)
        transaction = await self.cash.get(transaction_id)
        if transaction is None or transaction.account_id != account_id:
            raise NotFoundError("Transaction not found.")
        await self.cash.hard_delete(transaction_id)
        await self.recalculate(account_id)

    # -- projection ----------------------------------------------------------

    async def recalculate(self, account_id: uuid.UUID) -> Account:
        """Rebuild the cached balance columns from the ledger and closed trades."""
        account = await self.get(account_id)

        cash_total = await self.session.scalar(
            select(func.coalesce(func.sum(CashTransaction.amount), 0)).where(
                CashTransaction.organization_id == self.organization_id,
                CashTransaction.account_id == account_id,
            )
        )
        deposits = await self.session.scalar(
            select(func.coalesce(func.sum(CashTransaction.amount), 0)).where(
                CashTransaction.organization_id == self.organization_id,
                CashTransaction.account_id == account_id,
                CashTransaction.kind.in_(["deposit", "interest", "adjustment"]),
                CashTransaction.amount > 0,
            )
        )
        withdrawals = await self.session.scalar(
            select(func.coalesce(func.sum(CashTransaction.amount), 0)).where(
                CashTransaction.organization_id == self.organization_id,
                CashTransaction.account_id == account_id,
                CashTransaction.amount < 0,
            )
        )

        trade_totals = await self.session.execute(
            select(
                func.coalesce(func.sum(Trade.net_pnl), 0),
                func.coalesce(func.sum(Trade.commission), 0),
                func.coalesce(func.sum(Trade.fees), 0),
            ).where(
                Trade.organization_id == self.organization_id,
                Trade.account_id == account_id,
                Trade.deleted_at.is_(None),
                Trade.status == TradeStatus.CLOSED,
            )
        )
        net_pnl, commission, fees = trade_totals.one()

        account.realized_pnl = quantize_money(net_pnl)
        account.total_commission = quantize_money(commission)
        account.total_fees = quantize_money(fees)
        account.total_deposits = quantize_money(deposits or 0)
        account.total_withdrawals = quantize_money(abs(withdrawals or 0))
        account.current_balance = settle(
            quantize_money(account.initial_balance)
            + quantize_money(cash_total or 0)
            + account.realized_pnl,
            account.currency,
        )
        account.last_recalculated_at = utcnow()
        await self.session.flush()
        return account

    async def recalculate_all(self) -> int:
        accounts = await self.accounts.list()
        for account in accounts:
            await self.recalculate(account.id)
        return len(accounts)

    # -- stats ---------------------------------------------------------------

    async def stats(self, account_id: uuid.UUID) -> AccountStats:
        account = await self.get(account_id)

        counts = await self.session.execute(
            select(Trade.status, func.count())
            .where(
                Trade.organization_id == self.organization_id,
                Trade.account_id == account_id,
                Trade.deleted_at.is_(None),
            )
            .group_by(Trade.status)
        )
        by_status = dict(counts.all())
        open_count = int(by_status.get(TradeStatus.OPEN, 0)) + int(
            by_status.get(TradeStatus.PARTIALLY_CLOSED, 0)
        )
        closed_count = int(by_status.get(TradeStatus.CLOSED, 0))

        aggregates = await self.session.execute(
            select(
                func.coalesce(func.sum(Trade.net_pnl), 0),
                func.count(),
                func.min(Trade.entry_timestamp),
                func.max(Trade.exit_timestamp),
            ).where(
                Trade.organization_id == self.organization_id,
                Trade.account_id == account_id,
                Trade.deleted_at.is_(None),
                Trade.status == TradeStatus.CLOSED,
            )
        )
        net_pnl, _closed, first_at, last_at = aggregates.one()

        wins = await self.session.scalar(
            select(func.count()).where(
                Trade.organization_id == self.organization_id,
                Trade.account_id == account_id,
                Trade.deleted_at.is_(None),
                Trade.status == TradeStatus.CLOSED,
                Trade.net_pnl > 0,
            )
        )
        gross_profit = await self.session.scalar(
            select(func.coalesce(func.sum(Trade.net_pnl), 0)).where(
                Trade.organization_id == self.organization_id,
                Trade.account_id == account_id,
                Trade.deleted_at.is_(None),
                Trade.status == TradeStatus.CLOSED,
                Trade.net_pnl > 0,
            )
        )
        gross_loss = await self.session.scalar(
            select(func.coalesce(func.sum(Trade.net_pnl), 0)).where(
                Trade.organization_id == self.organization_id,
                Trade.account_id == account_id,
                Trade.deleted_at.is_(None),
                Trade.status == TradeStatus.CLOSED,
                Trade.net_pnl < 0,
            )
        )

        win_rate = (
            safe_div(Decimal(int(wins or 0)) * 100, Decimal(closed_count)) if closed_count else None
        )
        profit_factor = safe_div(
            quantize_money(gross_profit or 0), abs(quantize_money(gross_loss or 0))
        )

        positions = await self.positions.open_positions([account_id])
        unrealized = ZERO
        has_mark = False
        notional = ZERO
        for position in positions:
            if position.unrealized_pnl is not None:
                unrealized += position.unrealized_pnl
                has_mark = True
            if position.last_price is not None:
                notional += abs(
                    position.quantity * position.last_price * position.contract_multiplier
                )

        equity = account.current_balance + (unrealized if has_mark else ZERO)
        return AccountStats(
            open_trade_count=open_count,
            closed_trade_count=closed_count,
            open_quantity_notional=quantize_money(notional) if has_mark else None,
            unrealized_pnl=quantize_money(unrealized) if has_mark else None,
            equity=settle(equity, account.currency),
            win_rate=win_rate,
            profit_factor=profit_factor,
            net_pnl=quantize_money(net_pnl),
            first_trade_at=first_at,
            last_trade_at=last_at,
        )


__all__ = ["AccountService"]
