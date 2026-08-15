"""Strategy endpoints.

``GET /strategies/engine`` exposes the *registry* — the closed set of strategies the backtester
can actually execute, with their declared parameter bounds. The backtester UI builds its
parameter form from this response, so the form and the server-side validation can never drift.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from tradeloom.api.deps import Paging, Tenant, WritableTenant
from tradeloom.engine.registry import list_strategies
from tradeloom.schemas.catalog import (
    EngineStrategyInfo,
    ParameterSpec,
    StrategyCreate,
    StrategyDetail,
    StrategyRead,
    StrategyUpdate,
    StrategyVersionCreate,
    StrategyVersionRead,
)
from tradeloom.schemas.common import DataResponse, ListResponse, MessageResponse, PageMeta
from tradeloom.services.catalog import StrategyService

router = APIRouter(prefix="/strategies", tags=["strategies"])


@router.get(
    "/engine",
    response_model=DataResponse[list[EngineStrategyInfo]],
    summary="Built-in strategies available to the backtester",
)
async def engine_strategies(tenant: Tenant) -> DataResponse[list[EngineStrategyInfo]]:
    return DataResponse(
        data=[EngineStrategyInfo.model_validate(item) for item in list_strategies()]
    )


@router.get("", response_model=ListResponse[StrategyRead], summary="List strategies")
async def list_all(tenant: Tenant, paging: Paging) -> ListResponse[StrategyRead]:
    service = StrategyService(tenant.session, tenant.organization_id)
    page, stats = await service.list_with_stats(paging)
    items = []
    for strategy in page.items:
        model = StrategyRead.model_validate(strategy)
        payload = stats.get(strategy.id)
        if payload:
            model.trade_count = int(payload["count"])
            model.net_pnl = payload["net_pnl"]
            model.win_rate = payload.get("win_rate")
        items.append(model)
    return ListResponse(data=items, meta=PageMeta(**page.meta()))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[StrategyRead],
    summary="Create a strategy",
)
async def create_strategy(
    payload: StrategyCreate, tenant: WritableTenant
) -> DataResponse[StrategyRead]:
    strategy = await StrategyService(tenant.session, tenant.organization_id, tenant.user_id).create(
        payload
    )
    await tenant.session.commit()
    return DataResponse(data=StrategyRead.model_validate(strategy))


@router.get(
    "/{strategy_id}", response_model=DataResponse[StrategyDetail], summary="Strategy detail"
)
async def get_strategy(strategy_id: uuid.UUID, tenant: Tenant) -> DataResponse[StrategyDetail]:
    service = StrategyService(tenant.session, tenant.organization_id)
    strategy = await service.get(strategy_id)
    versions = await service.repo.versions(strategy.id)
    stats = await service._performance_by_strategy()

    model = StrategyRead.model_validate(strategy)
    payload = stats.get(strategy.id)
    if payload:
        model.trade_count = int(payload["count"])
        model.net_pnl = payload["net_pnl"]
        model.win_rate = payload.get("win_rate")

    specs: list[ParameterSpec] = []
    if strategy.current_version_id:
        for spec in await service.parameter_specs(strategy.current_version_id):
            specs.append(
                ParameterSpec(
                    name=spec.name,
                    label=spec.label,
                    param_type=spec.param_type,
                    default_value=spec.default_value,
                    minimum=spec.minimum,
                    maximum=spec.maximum,
                    step=spec.step,
                    choices=list((spec.choices or {}).get("values", [])),
                    description=spec.description,
                    display_order=spec.display_order,
                )
            )

    return DataResponse(
        data=StrategyDetail(
            strategy=model,
            versions=[StrategyVersionRead.model_validate(v) for v in versions],
            parameter_specs=specs,
        )
    )


@router.patch(
    "/{strategy_id}", response_model=DataResponse[StrategyRead], summary="Update a strategy"
)
async def update_strategy(
    strategy_id: uuid.UUID, payload: StrategyUpdate, tenant: WritableTenant
) -> DataResponse[StrategyRead]:
    strategy = await StrategyService(tenant.session, tenant.organization_id, tenant.user_id).update(
        strategy_id, payload
    )
    await tenant.session.commit()
    return DataResponse(data=StrategyRead.model_validate(strategy))


@router.post(
    "/{strategy_id}/versions",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[StrategyVersionRead],
    summary="Publish a new parameter version",
)
async def create_version(
    strategy_id: uuid.UUID, payload: StrategyVersionCreate, tenant: WritableTenant
) -> DataResponse[StrategyVersionRead]:
    service = StrategyService(tenant.session, tenant.organization_id, tenant.user_id)
    strategy = await service.get(strategy_id)
    version = await service.create_version(strategy, payload)
    await tenant.session.commit()
    return DataResponse(data=StrategyVersionRead.model_validate(version))


@router.get(
    "/{strategy_id}/versions",
    response_model=DataResponse[list[StrategyVersionRead]],
    summary="List strategy versions",
)
async def list_versions(
    strategy_id: uuid.UUID, tenant: Tenant
) -> DataResponse[list[StrategyVersionRead]]:
    service = StrategyService(tenant.session, tenant.organization_id)
    await service.get(strategy_id)
    versions = await service.repo.versions(strategy_id)
    return DataResponse(data=[StrategyVersionRead.model_validate(v) for v in versions])


@router.delete("/{strategy_id}", response_model=MessageResponse, summary="Delete a strategy")
async def delete_strategy(strategy_id: uuid.UUID, tenant: WritableTenant) -> MessageResponse:
    await StrategyService(tenant.session, tenant.organization_id, tenant.user_id).delete(
        strategy_id
    )
    await tenant.session.commit()
    return MessageResponse(message="Strategy deleted. Existing trades keep their history.")


__all__ = ["router"]
