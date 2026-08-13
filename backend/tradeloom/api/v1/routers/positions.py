"""Open position endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from tradeloom.api.deps import Paging, Tenant
from tradeloom.core.enums import PositionStatus
from tradeloom.models.trading import Position
from tradeloom.repositories.trading import PositionRepository
from tradeloom.schemas.common import ListResponse, PageMeta
from tradeloom.schemas.trade import PositionRead

router = APIRouter(prefix="/positions", tags=["positions"])


@router.get("", response_model=ListResponse[PositionRead], summary="List positions")
async def list_positions(
    tenant: Tenant,
    paging: Paging,
    account_id: Annotated[uuid.UUID | None, Query()] = None,
    open_only: bool = Query(default=True),
) -> ListResponse[PositionRead]:
    repo = PositionRepository(tenant.session, tenant.organization_id)
    filters = []
    if account_id:
        filters.append(Position.account_id == account_id)
    if open_only:
        filters.append(Position.status == PositionStatus.OPEN)

    page = await repo.paginate(paging, *filters, order_by=[Position.opened_at.desc()])
    return ListResponse(
        data=[PositionRead.model_validate(item) for item in page.items],
        meta=PageMeta(**page.meta()),
    )


@router.get("/{position_id}", response_model=PositionRead, summary="Position detail")
async def get_position(position_id: uuid.UUID, tenant: Tenant) -> PositionRead:
    from tradeloom.core.errors import NotFoundError

    repo = PositionRepository(tenant.session, tenant.organization_id)
    position = await repo.get(position_id)
    if position is None:
        raise NotFoundError("Position not found.")
    return PositionRead.model_validate(position)


__all__ = ["router"]
