"""Order (execution) endpoints.

Orders are the immutable record of what actually filled. They are readable and creatable, but not
editable — correcting a mistake means deleting the trade and re-recording it, so P&L can never
silently disagree with the executions that produced it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query

from tradeloom.api.deps import Paging, Tenant
from tradeloom.core.enums import OrderSide
from tradeloom.core.timeutil import ensure_aware
from tradeloom.models.trading import Order
from tradeloom.repositories.trading import OrderRepository
from tradeloom.schemas.common import ListResponse, PageMeta
from tradeloom.schemas.trade import OrderRead

router = APIRouter(prefix="/orders", tags=["orders"])


@router.get("", response_model=ListResponse[OrderRead], summary="List executions")
async def list_orders(
    tenant: Tenant,
    paging: Paging,
    account_id: Annotated[uuid.UUID | None, Query()] = None,
    trade_id: Annotated[uuid.UUID | None, Query()] = None,
    symbol: Annotated[str | None, Query(max_length=40)] = None,
    side: Annotated[OrderSide | None, Query()] = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> ListResponse[OrderRead]:
    repo = OrderRepository(tenant.session, tenant.organization_id)
    filters = []
    if account_id:
        filters.append(Order.account_id == account_id)
    if trade_id:
        filters.append(Order.trade_id == trade_id)
    if symbol:
        filters.append(Order.symbol == symbol.strip().upper())
    if side:
        filters.append(Order.side == side)
    if date_from:
        filters.append(Order.placed_at >= ensure_aware(date_from))
    if date_to:
        filters.append(Order.placed_at <= ensure_aware(date_to))

    page = await repo.paginate(paging, *filters, order_by=[Order.placed_at.desc()])
    return ListResponse(
        data=[OrderRead.model_validate(order) for order in page.items],
        meta=PageMeta(**page.meta()),
    )


@router.get("/{order_id}", response_model=OrderRead, summary="Execution detail")
async def get_order(order_id: uuid.UUID, tenant: Tenant) -> OrderRead:
    from tradeloom.core.errors import NotFoundError

    repo = OrderRepository(tenant.session, tenant.organization_id)
    order = await repo.get(order_id)
    if order is None:
        raise NotFoundError("Order not found.")
    return OrderRead.model_validate(order)


__all__ = ["router"]
