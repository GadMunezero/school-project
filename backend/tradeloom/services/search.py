"""Global search.

Server-side, tenant-scoped, and capped. Each entity type contributes at most a handful of hits so
the command palette stays fast and the response size is bounded regardless of workspace size.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.models.account import Account
from tradeloom.models.instrument import Instrument
from tradeloom.models.strategy import Setup, Strategy, Tag
from tradeloom.models.trading import Trade

PER_TYPE_LIMIT = 5
MIN_QUERY_LENGTH = 2


@dataclass(slots=True)
class SearchHit:
    type: str
    id: str
    title: str
    subtitle: str | None
    href: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "id": self.id,
            "title": self.title,
            "subtitle": self.subtitle,
            "href": self.href,
        }


class SearchService:
    def __init__(self, session: AsyncSession, organization_id: uuid.UUID) -> None:
        self.session = session
        self.organization_id = organization_id

    async def search(self, query: str, limit: int = PER_TYPE_LIMIT) -> list[dict[str, Any]]:
        text = query.strip()
        if len(text) < MIN_QUERY_LENGTH:
            return []
        needle = f"%{text.lower()}%"
        hits: list[SearchHit] = []

        trades = await self.session.execute(
            select(Trade)
            .where(
                Trade.organization_id == self.organization_id,
                Trade.deleted_at.is_(None),
                or_(
                    func.lower(Trade.symbol).like(needle),
                    func.lower(func.coalesce(Trade.notes, "")).like(needle),
                    func.lower(func.coalesce(Trade.external_id, "")).like(needle),
                ),
            )
            .order_by(Trade.entry_timestamp.desc())
            .limit(limit)
        )
        for trade in trades.scalars().all():
            hits.append(
                SearchHit(
                    type="trade",
                    id=str(trade.id),
                    title=f"{trade.symbol} {trade.direction.value}",
                    subtitle=(
                        f"{trade.entry_timestamp.date().isoformat()} · "
                        f"{format(trade.net_pnl.normalize(), 'f')} {trade.currency}"
                    ),
                    href=f"/journal/{trade.id}",
                )
            )

        hits.extend(
            await self._simple(
                Account, "account", "/accounts/{id}", needle, limit, subtitle_attr="broker"
            )
        )
        hits.extend(
            await self._simple(
                Strategy, "strategy", "/strategies/{id}", needle, limit, subtitle_attr="description"
            )
        )
        hits.extend(await self._simple(Setup, "setup", "/strategies?setup={id}", needle, limit))
        hits.extend(await self._simple(Tag, "tag", "/journal?tag_id={id}", needle, limit))
        hits.extend(await self._instruments(needle, limit))
        return [hit.to_dict() for hit in hits]

    async def _simple(
        self,
        model,  # type: ignore[no-untyped-def]
        type_name: str,
        href_template: str,
        needle: str,
        limit: int,
        subtitle_attr: str | None = None,
    ) -> list[SearchHit]:
        result = await self.session.execute(
            select(model)
            .where(
                model.organization_id == self.organization_id,
                model.deleted_at.is_(None),
                func.lower(model.name).like(needle),
            )
            .order_by(model.name.asc())
            .limit(limit)
        )
        hits: list[SearchHit] = []
        for row in result.scalars().all():
            subtitle = getattr(row, subtitle_attr, None) if subtitle_attr else None
            hits.append(
                SearchHit(
                    type=type_name,
                    id=str(row.id),
                    title=row.name,
                    subtitle=(subtitle or "")[:80] or None,
                    href=href_template.format(id=row.id),
                )
            )
        return hits

    async def _instruments(self, needle: str, limit: int) -> list[SearchHit]:
        result = await self.session.execute(
            select(Instrument)
            .where(
                or_(
                    Instrument.organization_id == self.organization_id,
                    Instrument.organization_id.is_(None),
                ),
                or_(
                    func.lower(Instrument.symbol).like(needle),
                    func.lower(func.coalesce(Instrument.name, "")).like(needle),
                ),
            )
            .order_by(Instrument.symbol.asc())
            .limit(limit)
        )
        return [
            SearchHit(
                type="instrument",
                id=str(row.id),
                title=row.symbol,
                subtitle=row.name or row.asset_type.value,
                href=f"/journal?symbol={row.symbol}",
            )
            for row in result.scalars().all()
        ]


__all__ = ["MIN_QUERY_LENGTH", "PER_TYPE_LIMIT", "SearchHit", "SearchService"]
