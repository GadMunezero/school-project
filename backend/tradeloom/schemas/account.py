"""Account and cash-ledger contracts."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_validator

from tradeloom.core.enums import AccountStatus, AccountType, CommissionModelType
from tradeloom.core.timeutil import is_valid_timezone
from tradeloom.schemas.common import TradeloomModel

CASH_KINDS = ("deposit", "withdrawal", "fee", "interest", "adjustment", "payout")


class AccountCreate(TradeloomModel):
    name: str = Field(min_length=1, max_length=120)
    broker: str | None = Field(default=None, max_length=120)
    account_type: AccountType = AccountType.LIVE
    currency: str = Field(default="USD", min_length=3, max_length=8)
    initial_balance: Decimal = Field(default=Decimal(0), ge=0)
    leverage: Decimal = Field(default=Decimal(1), gt=0, le=Decimal(500))
    timezone: str = "UTC"
    commission_model: CommissionModelType = CommissionModelType.NONE
    commission_config: dict[str, Any] = Field(default_factory=dict)
    default_risk_percent: Decimal | None = Field(default=None, ge=0, le=100)
    external_reference: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)
    is_default: bool = False

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str) -> str:
        if not is_valid_timezone(value):
            raise ValueError("Unknown timezone")
        return value


class AccountUpdate(TradeloomModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    broker: str | None = Field(default=None, max_length=120)
    account_type: AccountType | None = None
    leverage: Decimal | None = Field(default=None, gt=0, le=Decimal(500))
    timezone: str | None = None
    commission_model: CommissionModelType | None = None
    commission_config: dict[str, Any] | None = None
    default_risk_percent: Decimal | None = Field(default=None, ge=0, le=100)
    external_reference: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)
    status: AccountStatus | None = None
    is_default: bool | None = None
    #: Changing the starting balance rewrites the whole equity curve, so it is explicit.
    initial_balance: Decimal | None = Field(default=None, ge=0)

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str | None) -> str | None:
        if value is not None and not is_valid_timezone(value):
            raise ValueError("Unknown timezone")
        return value


class AccountRead(TradeloomModel):
    id: Any
    name: str
    broker: str | None
    account_type: AccountType
    currency: str
    initial_balance: Decimal
    current_balance: Decimal
    realized_pnl: Decimal
    total_deposits: Decimal
    total_withdrawals: Decimal
    total_commission: Decimal
    total_fees: Decimal
    leverage: Decimal
    timezone: str
    commission_model: CommissionModelType
    commission_config: dict[str, Any]
    default_risk_percent: Decimal | None
    status: AccountStatus
    is_default: bool
    external_reference: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime
    last_recalculated_at: datetime | None


class AccountStats(TradeloomModel):
    """Live figures derived from trades, not stored on the account row."""

    open_trade_count: int
    closed_trade_count: int
    open_quantity_notional: Decimal | None
    unrealized_pnl: Decimal | None
    #: current_balance + unrealized_pnl when marks are available, else current_balance.
    equity: Decimal
    win_rate: Decimal | None
    profit_factor: Decimal | None
    net_pnl: Decimal
    first_trade_at: datetime | None
    last_trade_at: datetime | None


class AccountDetail(TradeloomModel):
    account: AccountRead
    stats: AccountStats


class CashTransactionCreate(TradeloomModel):
    kind: Literal["deposit", "withdrawal", "fee", "interest", "adjustment", "payout"]
    #: Always positive; the sign is derived from ``kind`` server-side so a client cannot turn a
    #: withdrawal into a deposit by sending a negative number.
    amount: Decimal = Field(gt=0)
    occurred_at: datetime
    description: str | None = Field(default=None, max_length=255)


class CashTransactionRead(TradeloomModel):
    id: Any
    account_id: Any
    kind: str
    amount: Decimal
    currency: str
    occurred_at: datetime
    description: str | None
    created_at: datetime


class AccountSnapshotRead(TradeloomModel):
    as_of_date: date
    opening_balance: Decimal
    closing_balance: Decimal
    equity: Decimal
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    commission: Decimal
    fees: Decimal
    net_cash_flow: Decimal
    trade_count: int
    peak_equity: Decimal
    drawdown: Decimal
    drawdown_percent: Decimal


__all__ = [
    "CASH_KINDS",
    "AccountCreate",
    "AccountDetail",
    "AccountRead",
    "AccountSnapshotRead",
    "AccountStats",
    "AccountUpdate",
    "CashTransactionCreate",
    "CashTransactionRead",
]
