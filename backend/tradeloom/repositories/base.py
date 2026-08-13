"""Tenant-scoped repository base.

**The single most important rule in this codebase lives here.** Every read and write of a
tenant-owned entity goes through :class:`TenantRepository`, which is constructed with an
``organization_id`` and injects ``WHERE organization_id = :org`` into every statement. A service
cannot "forget" the filter because it never writes the ``WHERE`` clause itself.

Consequences that the tenant-isolation tests assert:

* ``get(id)`` for another tenant's row returns ``None`` -> the service raises ``NotFoundError``
  (404, never 403 — a 403 would confirm the row exists).
* ``update``/``delete`` are no-ops across tenants because they re-select through the same filter.
* Listing never returns foreign rows even if a filter value is attacker-controlled.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from tradeloom.core.pagination import Page, PageParams
from tradeloom.core.timeutil import utcnow
from tradeloom.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class Repository(Generic[ModelT]):
    """Non-tenant repository, for global tables (instrument catalogue, market data sources)."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _base_select(self) -> Select[tuple[ModelT]]:
        return select(self.model)

    async def get(self, entity_id: uuid.UUID) -> ModelT | None:
        result = await self.session.execute(
            self._base_select().where(self.model.id == entity_id)  # type: ignore[attr-defined]
        )
        return result.scalar_one_or_none()

    async def add(self, entity: ModelT) -> ModelT:
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def add_all(self, entities: Sequence[ModelT]) -> Sequence[ModelT]:
        self.session.add_all(list(entities))
        await self.session.flush()
        return entities

    async def list_all(self, *filters: ColumnElement[bool]) -> list[ModelT]:
        stmt = self._base_select()
        for condition in filters:
            stmt = stmt.where(condition)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class TenantRepository(Repository[ModelT]):
    """Repository bound to exactly one organization."""

    #: Set False for models that are tenant-scoped but have no soft-delete column.
    supports_soft_delete: bool = True

    def __init__(self, session: AsyncSession, organization_id: uuid.UUID) -> None:
        super().__init__(session)
        if organization_id is None:  # pragma: no cover - programming error guard
            raise ValueError("TenantRepository requires an organization_id")
        self.organization_id = organization_id

    # -- statement construction --------------------------------------------

    def _tenant_filter(self) -> ColumnElement[bool]:
        return self.model.organization_id == self.organization_id  # type: ignore[attr-defined]

    def _base_select(self, *, include_deleted: bool = False) -> Select[tuple[ModelT]]:
        stmt = select(self.model).where(self._tenant_filter())
        if self.supports_soft_delete and not include_deleted:
            stmt = stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        return stmt

    def select(self, *, include_deleted: bool = False) -> Select[tuple[ModelT]]:
        """Escape hatch for services that need custom joins — still tenant-filtered."""
        return self._base_select(include_deleted=include_deleted)

    # -- reads ---------------------------------------------------------------

    async def get(self, entity_id: uuid.UUID, *, include_deleted: bool = False) -> ModelT | None:
        result = await self.session.execute(
            self._base_select(include_deleted=include_deleted).where(
                self.model.id == entity_id  # type: ignore[attr-defined]
            )
        )
        return result.scalar_one_or_none()

    async def get_many(self, entity_ids: Sequence[uuid.UUID]) -> list[ModelT]:
        if not entity_ids:
            return []
        result = await self.session.execute(
            self._base_select().where(self.model.id.in_(list(entity_ids)))  # type: ignore[attr-defined]
        )
        return list(result.scalars().all())

    async def exists(self, entity_id: uuid.UUID) -> bool:
        return await self.get(entity_id) is not None

    async def count(self, *filters: ColumnElement[bool]) -> int:
        stmt = select(func.count()).select_from(self.model).where(self._tenant_filter())
        if self.supports_soft_delete:
            stmt = stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        for condition in filters:
            stmt = stmt.where(condition)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def list(
        self,
        *filters: ColumnElement[bool],
        order_by: Sequence[Any] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        include_deleted: bool = False,
    ) -> list[ModelT]:
        stmt = self._base_select(include_deleted=include_deleted)
        for condition in filters:
            stmt = stmt.where(condition)
        if order_by:
            stmt = stmt.order_by(*order_by)
        if offset is not None:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def paginate(
        self,
        params: PageParams,
        *filters: ColumnElement[bool],
        order_by: Sequence[Any] | None = None,
    ) -> Page[ModelT]:
        total = await self.count(*filters)
        items = await self.list(
            *filters, order_by=order_by, limit=params.limit, offset=params.offset
        )
        return Page(items=items, total=total, page=params.page, page_size=params.page_size)

    # -- writes --------------------------------------------------------------

    async def add(self, entity: ModelT) -> ModelT:
        """Force the tenant id rather than trusting whatever the caller set on the object."""
        entity.organization_id = self.organization_id
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def add_all(self, entities: Sequence[ModelT]) -> Sequence[ModelT]:
        for entity in entities:
            entity.organization_id = self.organization_id
        self.session.add_all(list(entities))
        await self.session.flush()
        return entities

    async def soft_delete(self, entity_id: uuid.UUID) -> bool:
        if not self.supports_soft_delete:
            raise NotImplementedError(f"{self.model.__name__} has no soft-delete column")
        result = await self.session.execute(
            update(self.model)
            .where(self.model.id == entity_id, self._tenant_filter())  # type: ignore[attr-defined]
            .where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
            .values(deleted_at=utcnow())
        )
        return bool(result.rowcount)

    async def hard_delete(self, entity_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            delete(self.model).where(
                self.model.id == entity_id,  # type: ignore[attr-defined]
                self._tenant_filter(),
            )
        )
        return bool(result.rowcount)

    async def bulk_update(self, entity_ids: Sequence[uuid.UUID], values: dict[str, Any]) -> int:
        """Update many rows in one statement — still confined to this tenant."""
        if not entity_ids or not values:
            return 0
        stmt = (
            update(self.model)
            .where(self.model.id.in_(list(entity_ids)), self._tenant_filter())  # type: ignore[attr-defined]
            .values(**values)
        )
        if self.supports_soft_delete:
            stmt = stmt.where(self.model.deleted_at.is_(None))  # type: ignore[attr-defined]
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)


__all__ = ["Repository", "TenantRepository"]
