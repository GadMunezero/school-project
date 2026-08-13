"""Analytics endpoints.

One filterable engine rather than a hardcoded dashboard per chart: every endpoint here takes the
same filter set the journal uses, so "win rate for setup X on Tuesdays in Q2" is a query, not a
feature request.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from tradeloom.api.deps import CurrentLimits, Entitlements, Tenant
from tradeloom.api.v1.routers.trades import trade_filters
from tradeloom.core.errors import EntitlementError
from tradeloom.schemas.common import DataResponse
from tradeloom.schemas.trade import TradeFilters
from tradeloom.services.analytics import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"])

Filters = Annotated[TradeFilters, Depends(trade_filters)]


@router.get("/overview", response_model=DataResponse[dict], summary="Full analytics for a filter")
async def overview(tenant: Tenant, filters: Filters) -> DataResponse[dict]:
    result = await AnalyticsService(tenant.session, tenant.organization_id).analyse(filters)
    return DataResponse(data=result.to_dict())


@router.get("/dashboard", response_model=DataResponse[dict], summary="Dashboard payload")
async def dashboard(
    tenant: Tenant,
    filters: Filters,
    days: Annotated[int, Query(ge=1, le=3650)] = 90,
) -> DataResponse[dict]:
    service = AnalyticsService(tenant.session, tenant.organization_id)
    return DataResponse(data=await service.dashboard(filters, days=days))


@router.get(
    "/compare",
    response_model=DataResponse[dict],
    summary="Compare two filter sets (period, strategy, or account)",
)
async def compare(
    tenant: Tenant,
    entitlements: Entitlements,
    limits: CurrentLimits,
    left: Filters,
    right_account_id: Annotated[list[str] | None, Query()] = None,
    right_strategy_id: Annotated[list[str] | None, Query()] = None,
    right_date_from: str | None = None,
    right_date_to: str | None = None,
    left_label: Annotated[str, Query(max_length=60)] = "A",
    right_label: Annotated[str, Query(max_length=60)] = "B",
) -> DataResponse[dict]:
    if not limits.comparison_enabled:
        raise EntitlementError(
            "Comparison is a Pro feature.", feature="comparison", required_plan="pro"
        )

    from datetime import datetime

    right = TradeFilters(**left.model_dump())
    if right_account_id:
        right.account_ids = right_account_id  # type: ignore[assignment]
    if right_strategy_id:
        right.strategy_ids = right_strategy_id  # type: ignore[assignment]
    if right_date_from:
        right.date_from = datetime.fromisoformat(right_date_from.replace("Z", "+00:00"))
    if right_date_to:
        right.date_to = datetime.fromisoformat(right_date_to.replace("Z", "+00:00"))

    service = AnalyticsService(tenant.session, tenant.organization_id)
    payload = await service.compare(left, right, left_label=left_label, right_label=right_label)
    return DataResponse(data=payload)


__all__ = ["router"]
