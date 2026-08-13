"""Analytics aggregation tasks.

Snapshots are a cache, never a source of truth: every figure they hold can be recomputed from
trades on demand. That is why a failed snapshot job degrades performance but never correctness.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from celery import shared_task
from sqlalchemy import select

from tradeloom.core.enums import TradeStatus
from tradeloom.core.logging import get_logger
from tradeloom.core.money import ZERO, quantize_money, safe_div
from tradeloom.core.timeutil import local_date
from tradeloom.db.session import session_scope
from tradeloom.models.account import Account, AccountSnapshot
from tradeloom.models.organization import Organization
from tradeloom.models.trading import Trade
from worker.runtime import run_async

logger = get_logger(__name__)


@shared_task(name="tradeloom.analytics.refresh_snapshots", acks_late=True)
def refresh_snapshots_task() -> dict[str, Any]:
    """Rebuild end-of-day account snapshots for every active workspace."""
    return run_async(_refresh_all())


async def _refresh_all() -> dict[str, Any]:
    async with session_scope() as session:
        organizations = await session.execute(
            select(Organization.id).where(Organization.deleted_at.is_(None))
        )
        total = 0
        for (organization_id,) in organizations.all():
            total += await _refresh_organization(session, organization_id)
        logger.info("analytics_snapshots_refreshed", accounts=total)
        return {"accounts_refreshed": total}


@shared_task(name="tradeloom.analytics.refresh_account", acks_late=True)
def refresh_account_task(organization_id: str, account_id: str) -> dict[str, Any]:
    return run_async(_refresh_one(uuid.UUID(organization_id), uuid.UUID(account_id)))


async def _refresh_one(organization_id: uuid.UUID, account_id: uuid.UUID) -> dict[str, Any]:
    async with session_scope() as session:
        count = await _rebuild_account(session, organization_id, account_id)
        return {"snapshots": count}


async def _refresh_organization(session: Any, organization_id: uuid.UUID) -> int:
    accounts = await session.execute(
        select(Account.id).where(
            Account.organization_id == organization_id, Account.deleted_at.is_(None)
        )
    )
    count = 0
    for (account_id,) in accounts.all():
        await _rebuild_account(session, organization_id, account_id)
        count += 1
    return count


async def _rebuild_account(session: Any, organization_id: uuid.UUID, account_id: uuid.UUID) -> int:
    """Recompute daily snapshots from closed trades.

    Days with no activity are not written: a gap means "nothing happened", which the chart draws
    as a flat line rather than a fabricated data point.
    """
    account = await session.get(Account, account_id)
    if account is None or account.organization_id != organization_id:
        return 0

    result = await session.execute(
        select(Trade)
        .where(
            Trade.organization_id == organization_id,
            Trade.account_id == account_id,
            Trade.deleted_at.is_(None),
            Trade.status == TradeStatus.CLOSED,
        )
        .order_by(Trade.exit_timestamp.asc())
    )
    trades = list(result.scalars().all())
    if not trades:
        return 0

    by_day: dict[Any, list[Trade]] = {}
    for trade in trades:
        day = local_date(trade.exit_timestamp or trade.entry_timestamp, account.timezone)
        by_day.setdefault(day, []).append(trade)

    existing = await session.execute(
        select(AccountSnapshot).where(
            AccountSnapshot.organization_id == organization_id,
            AccountSnapshot.account_id == account_id,
        )
    )
    by_date = {row.as_of_date: row for row in existing.scalars().all()}

    running = quantize_money(account.initial_balance)
    peak = running
    written = 0

    for day in sorted(by_day):
        bucket = by_day[day]
        realized = quantize_money(sum((t.net_pnl for t in bucket), ZERO))
        commission = quantize_money(sum((t.commission for t in bucket), ZERO))
        fees = quantize_money(sum((t.fees for t in bucket), ZERO))
        opening = running
        running = quantize_money(running + realized)
        peak = max(peak, running)
        drawdown = quantize_money(running - peak)
        drawdown_percent = safe_div(drawdown * Decimal(100), peak) if peak > 0 else ZERO

        snapshot = by_date.get(day)
        if snapshot is None:
            snapshot = AccountSnapshot(
                organization_id=organization_id, account_id=account_id, as_of_date=day
            )
            session.add(snapshot)

        snapshot.opening_balance = opening
        snapshot.closing_balance = running
        snapshot.equity = running
        snapshot.realized_pnl = realized
        snapshot.unrealized_pnl = ZERO
        snapshot.commission = commission
        snapshot.fees = fees
        snapshot.net_cash_flow = ZERO
        snapshot.trade_count = len(bucket)
        snapshot.peak_equity = peak
        snapshot.drawdown = drawdown
        snapshot.drawdown_percent = drawdown_percent or ZERO
        written += 1

    return written


__all__ = ["refresh_account_task", "refresh_snapshots_task"]
