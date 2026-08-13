"""Backtest endpoints.

``POST /backtests/{id}/run`` never blocks: it validates, queues a Celery job and returns a run id
plus a job id immediately. The client polls ``GET /backtests/runs/{run_id}`` for progress.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from tradeloom.api.deps import CurrentLimits, Entitlements, Paging, Tenant, WritableTenant
from tradeloom.core.errors import EntitlementError, NotFoundError
from tradeloom.schemas.backtest import (
    BacktestCreate,
    BacktestOrderRead,
    BacktestRead,
    BacktestResultRead,
    BacktestRunRead,
    BacktestTradeRead,
    CompareRunsRequest,
    DrawdownPointRead,
    EquityPointRead,
)
from tradeloom.schemas.common import DataResponse, ListResponse, MessageResponse, PageMeta
from tradeloom.services.backtests import BacktestService
from tradeloom.services.jobs import JobService

router = APIRouter(prefix="/backtests", tags=["backtests"])


def _service(tenant: Tenant) -> BacktestService:
    return BacktestService(tenant.session, tenant.organization_id, actor_user_id=tenant.user_id)


@router.get("", response_model=ListResponse[BacktestRead], summary="List backtests")
async def list_backtests(tenant: Tenant, paging: Paging) -> ListResponse[BacktestRead]:
    page = await _service(tenant).list(paging)
    return ListResponse(
        data=[BacktestRead.model_validate(item) for item in page.items],
        meta=PageMeta(**page.meta()),
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[BacktestRead],
    summary="Create a backtest configuration",
)
async def create_backtest(
    payload: BacktestCreate, tenant: WritableTenant, limits: CurrentLimits
) -> DataResponse[BacktestRead]:
    if "backtesting" not in limits.features:
        raise EntitlementError(
            "Backtesting is a Pro feature.", feature="backtesting", required_plan="pro"
        )
    record = await _service(tenant).create(payload)
    await tenant.session.commit()
    return DataResponse(data=BacktestRead.model_validate(record))


@router.get("/{backtest_id}", response_model=DataResponse[BacktestRead], summary="Backtest detail")
async def get_backtest(backtest_id: uuid.UUID, tenant: Tenant) -> DataResponse[BacktestRead]:
    record = await _service(tenant).get(backtest_id)
    return DataResponse(data=BacktestRead.model_validate(record))


@router.post(
    "/{backtest_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=DataResponse[dict],
    summary="Queue a run (returns immediately)",
)
async def run_backtest(
    backtest_id: uuid.UUID,
    tenant: WritableTenant,
    entitlements: Entitlements,
    limits: CurrentLimits,
) -> DataResponse[dict]:
    if "backtesting" not in limits.features:
        raise EntitlementError(
            "Backtesting is a Pro feature.", feature="backtesting", required_plan="pro"
        )

    service = _service(tenant)
    run, job = await service.submit(backtest_id)
    await tenant.session.commit()

    # Dispatch after the commit so the worker cannot pick up a job whose row is not yet visible.
    task_id: str | None = None
    try:
        from worker.tasks.backtests import run_backtest_task

        result = run_backtest_task.delay(str(run.id), str(job.id))
        task_id = result.id
        await JobService(tenant.session).attach_task(job.id, task_id)
        await tenant.session.commit()
    except Exception:
        # The broker is unavailable. The job stays queued and a worker will pick it up when the
        # broker recovers; the run is not lost and the user is told the truth.
        from tradeloom.core.logging import get_logger

        get_logger(__name__).warning("backtest_dispatch_failed", run_id=str(run.id))

    return DataResponse(
        data={
            "run_id": str(run.id),
            "job_id": str(job.id),
            "status": run.status.value,
            "task_id": task_id,
            "queued": True,
        }
    )


@router.get(
    "/{backtest_id}/runs",
    response_model=ListResponse[BacktestRunRead],
    summary="Runs of a backtest",
)
async def list_runs(
    backtest_id: uuid.UUID, tenant: Tenant, paging: Paging
) -> ListResponse[BacktestRunRead]:
    page = await _service(tenant).list_runs(backtest_id, paging)
    return ListResponse(
        data=[BacktestRunRead.model_validate(item) for item in page.items],
        meta=PageMeta(**page.meta()),
    )


@router.get("/runs/{run_id}", response_model=DataResponse[BacktestResultRead], summary="Run result")
async def get_run(
    run_id: uuid.UUID,
    tenant: Tenant,
    include_trades: bool = Query(default=True),
    equity_limit: Annotated[int, Query(ge=0, le=5000)] = 2000,
) -> DataResponse[BacktestResultRead]:
    service = _service(tenant)
    run = await service.get_run(run_id)

    equity = await service.run_equity(run_id) if equity_limit else []
    if equity_limit and len(equity) > equity_limit:
        step = len(equity) / equity_limit
        equity = [equity[int(i * step)] for i in range(equity_limit - 1)] + [equity[-1]]

    trades = await service.run_trades(run_id) if include_trades else []
    drawdowns = await service.run_drawdowns(run_id)

    return DataResponse(
        data=BacktestResultRead(
            run=BacktestRunRead.model_validate(run),
            metrics=run.metrics,
            breakdowns=run.breakdowns,
            equity_curve=[EquityPointRead.model_validate(point) for point in equity],
            drawdowns=[DrawdownPointRead.model_validate(point) for point in drawdowns],
            trades=[BacktestTradeRead.model_validate(trade) for trade in trades],
        )
    )


@router.get(
    "/runs/{run_id}/orders",
    response_model=DataResponse[list[BacktestOrderRead]],
    summary="Every order the simulation created",
)
async def run_orders(run_id: uuid.UUID, tenant: Tenant) -> DataResponse[list[BacktestOrderRead]]:
    service = _service(tenant)
    await service.get_run(run_id)
    orders = await service.run_orders(run_id)
    return DataResponse(data=[BacktestOrderRead.model_validate(order) for order in orders])


@router.post(
    "/runs/compare", response_model=DataResponse[dict], summary="Compare runs side by side"
)
async def compare_runs(
    payload: CompareRunsRequest, tenant: Tenant, limits: CurrentLimits
) -> DataResponse[dict]:
    if not limits.comparison_enabled:
        raise EntitlementError(
            "Comparison is a Pro feature.", feature="comparison", required_plan="pro"
        )
    return DataResponse(data=await _service(tenant).compare_runs(payload.run_ids))


@router.get("/jobs/{job_id}", response_model=DataResponse[dict], summary="Job status")
async def job_status(job_id: uuid.UUID, tenant: Tenant) -> DataResponse[dict]:
    service = JobService(tenant.session)
    record = await service.get_for_organization(job_id, tenant.organization_id)
    return DataResponse(data=JobService.to_dict(record))


@router.delete("/{backtest_id}", response_model=MessageResponse, summary="Delete a backtest")
async def delete_backtest(backtest_id: uuid.UUID, tenant: WritableTenant) -> MessageResponse:
    service = _service(tenant)
    record = await service.get(backtest_id)
    if not await service.backtests.soft_delete(record.id):
        raise NotFoundError("Backtest not found.")
    await tenant.session.commit()
    return MessageResponse(message="Backtest deleted.")


__all__ = ["router"]
