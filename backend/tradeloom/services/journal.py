"""Journal entries: trade reviews and daily/weekly notes."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core.errors import NotFoundError
from tradeloom.core.pagination import Page, PageParams
from tradeloom.core.timeutil import utcnow
from tradeloom.models.journal import JournalEntry
from tradeloom.repositories.base import TenantRepository
from tradeloom.repositories.trading import TradeRepository


class JournalRepository(TenantRepository[JournalEntry]):
    model = JournalEntry


class JournalService:
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
        self.repo = JournalRepository(session, organization_id)
        self.trades = TradeRepository(session, organization_id)

    async def get(self, entry_id: uuid.UUID) -> JournalEntry:
        entry = await self.repo.get(entry_id)
        if entry is None:
            raise NotFoundError("Journal entry not found.")
        return entry

    async def list(
        self,
        params: PageParams,
        *,
        trade_id: uuid.UUID | None = None,
        entry_type: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        search: str | None = None,
    ) -> Page[JournalEntry]:
        filters = []
        if trade_id:
            filters.append(JournalEntry.trade_id == trade_id)
        if entry_type:
            filters.append(JournalEntry.entry_type == entry_type)
        if date_from:
            filters.append(JournalEntry.entry_date >= date_from)
        if date_to:
            filters.append(JournalEntry.entry_date <= date_to)
        if search:
            needle = f"%{search.strip().lower()}%"
            filters.append(
                func.lower(func.coalesce(JournalEntry.title, "")).like(needle)
                | func.lower(JournalEntry.body).like(needle)
            )
        return await self.repo.paginate(
            params,
            *filters,
            order_by=[
                JournalEntry.pinned_at.desc().nullslast(),
                JournalEntry.entry_date.desc(),
                JournalEntry.created_at.desc(),
            ],
        )

    async def create(self, payload: dict[str, Any]) -> JournalEntry:
        trade_id = payload.get("trade_id")
        if trade_id and not await self.trades.exists(trade_id):
            # Client-supplied ids are never trusted; a foreign trade id 404s.
            raise NotFoundError("Trade not found.")

        entry = JournalEntry(
            organization_id=self.organization_id,
            author_user_id=self.actor_user_id,
            entry_date=payload.get("entry_date") or utcnow().date(),
            title=payload.get("title"),
            body=payload.get("body", ""),
            entry_type=payload.get("entry_type", "note"),
            mood=payload.get("mood"),
            discipline_rating=payload.get("discipline_rating"),
            lessons=payload.get("lessons") or {},
            trade_id=trade_id,
            account_id=payload.get("account_id"),
        )
        await self.repo.add(entry)
        return entry

    async def update(self, entry_id: uuid.UUID, payload: dict[str, Any]) -> JournalEntry:
        entry = await self.get(entry_id)
        for field, value in payload.items():
            if field == "pinned":
                entry.pinned_at = utcnow() if value else None
                continue
            setattr(entry, field, value)
        await self.session.flush()
        return entry

    async def delete(self, entry_id: uuid.UUID) -> None:
        entry = await self.get(entry_id)
        await self.repo.soft_delete(entry.id)


__all__ = ["JournalRepository", "JournalService"]
