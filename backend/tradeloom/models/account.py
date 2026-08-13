"""Trading accounts, cash movements and balance snapshots.

Balances are never simply overwritten. ``Account.current_balance`` is a cached projection of an
append-only ledger:

    current_balance = initial_balance
                    + sum(cash_transactions.amount)      # deposits, withdrawals, adjustments
                    + sum(closed trade net P&L)

``AccountSnapshot`` stores the end-of-day roll-up so equity curves and drawdowns can be produced
without replaying the whole ledger. See ``docs/FINANCIALS.md``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tradeloom.core.enums import AccountStatus, AccountType, CommissionModelType
from tradeloom.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from tradeloom.db.types import (
    GUID,
    EnumType,
    JSONDict,
    TZDateTime,
    money_column,
    percent_column,
    quantity_column,
)


class Account(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_accounts_org_name"),
        Index("ix_accounts_org_status", "organization_id", "status"),
        CheckConstraint("leverage > 0", name="accounts_leverage_positive"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    broker: Mapped[str | None] = mapped_column(String(120), nullable=True)
    account_type: Mapped[AccountType] = mapped_column(
        EnumType(AccountType, 24), nullable=False, default=AccountType.LIVE
    )
    external_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)

    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    initial_balance: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    #: Cached projection of the ledger; recomputed by AccountLedgerService, never blindly assigned.
    current_balance: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    #: Realised P&L only. Unrealised exposure is derived from open trades at read time.
    realized_pnl: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    total_deposits: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    total_withdrawals: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    total_commission: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    total_fees: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)

    leverage: Mapped[Decimal] = mapped_column(quantity_column(), nullable=False, default=1)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")

    commission_model: Mapped[CommissionModelType] = mapped_column(
        EnumType(CommissionModelType, 32), nullable=False, default=CommissionModelType.NONE
    )
    #: Parameters for the commission model, e.g. {"rate": "0.005", "minimum": "1.00"}.
    commission_config: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    default_risk_percent: Mapped[Decimal | None] = mapped_column(percent_column(), nullable=True)

    status: Mapped[AccountStatus] = mapped_column(
        EnumType(AccountStatus, 20), nullable=False, default=AccountStatus.ACTIVE
    )
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(String(2000), nullable=True)

    last_recalculated_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)


class CashTransaction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Append-only cash ledger: deposits, withdrawals, and non-trade adjustments.

    ``amount`` is signed: positive increases the balance, negative decreases it. Trade P&L is not
    recorded here — it lives on the trade and is summed separately, so the two sources can be
    reconciled independently.
    """

    __tablename__ = "cash_transactions"
    __table_args__ = (
        Index("ix_cash_transactions_account_occurred", "account_id", "occurred_at"),
        CheckConstraint(
            "kind IN ('deposit','withdrawal','fee','interest','adjustment','payout')",
            name="cash_transactions_kind_valid",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    amount: Mapped[Decimal] = mapped_column(money_column(), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    occurred_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("imports.id", ondelete="SET NULL"), nullable=True
    )


class AccountSnapshot(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """End-of-day account state, one row per account per calendar day (account timezone)."""

    __tablename__ = "account_snapshots"
    __table_args__ = (
        UniqueConstraint("account_id", "as_of_date", name="uq_account_snapshots_account_date"),
        Index("ix_account_snapshots_org_date", "organization_id", "as_of_date"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    as_of_date: Mapped[date] = mapped_column(nullable=False)

    opening_balance: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    closing_balance: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    #: closing_balance plus open-position unrealised P&L at the snapshot instant.
    equity: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    realized_pnl: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    unrealized_pnl: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    commission: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    fees: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    net_cash_flow: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    trade_count: Mapped[int] = mapped_column(nullable=False, default=0)
    #: Running peak equity, used to derive drawdown without a second pass.
    peak_equity: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    drawdown: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    drawdown_percent: Mapped[Decimal] = mapped_column(percent_column(), nullable=False, default=0)


__all__ = ["Account", "AccountSnapshot", "CashTransaction"]
