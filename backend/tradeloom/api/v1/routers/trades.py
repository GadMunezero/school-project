"""Trade journal endpoints.

Filtering, sorting and pagination all happen in SQL. The journal is designed for accounts with
hundreds of thousands of trades, so no endpoint here can return an unbounded result set.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query, status

from tradeloom.api.deps import Paging, Tenant, WritableTenant
from tradeloom.api.v1.presenters import trade_list, trade_read
from tradeloom.core.enums import AssetType, Direction, TradeStatus, TradingSession
from tradeloom.schemas.common import (
    BulkResult,
    DataResponse,
    ListResponse,
    MessageResponse,
    PageMeta,
)
from tradeloom.schemas.trade import (
    BulkEditAction,
    BulkTagAction,
    BulkTradeAction,
    MarkPriceRequest,
    OrderRead,
    ScreenshotRead,
    TradeCreate,
    TradeDetail,
    TradeFilters,
    TradeRead,
    TradeUpdate,
)
from tradeloom.services.trades import TradeService
from tradeloom.services.trading import calculations

router = APIRouter(prefix="/trades", tags=["trades"])


def _service(tenant: Tenant) -> TradeService:
    return TradeService(tenant.session, tenant.organization_id, actor_user_id=tenant.user_id)


def trade_filters(
    account_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    symbol: Annotated[list[str] | None, Query()] = None,
    direction: Annotated[list[Direction] | None, Query()] = None,
    trade_status: Annotated[list[TradeStatus] | None, Query(alias="status")] = None,
    strategy_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    setup_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    tag_id: Annotated[list[uuid.UUID] | None, Query()] = None,
    session_bucket: Annotated[list[TradingSession] | None, Query(alias="session")] = None,
    asset_type: Annotated[list[AssetType] | None, Query()] = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    outcome: Annotated[str | None, Query(pattern="^(winners|losers|breakeven)$")] = None,
    pnl_min: Decimal | None = None,
    pnl_max: Decimal | None = None,
    r_min: Decimal | None = None,
    r_max: Decimal | None = None,
    weekday: Annotated[list[int] | None, Query(ge=0, le=6)] = None,
    hour: Annotated[list[int] | None, Query(ge=0, le=23)] = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
    has_notes: bool | None = None,
) -> TradeFilters:
    return TradeFilters(
        account_ids=account_id or [],
        symbols=symbol or [],
        directions=direction or [],
        statuses=trade_status or [],
        strategy_ids=strategy_id or [],
        setup_ids=setup_id or [],
        tag_ids=tag_id or [],
        sessions=session_bucket or [],
        asset_types=asset_type or [],
        date_from=date_from,
        date_to=date_to,
        outcome=outcome,
        pnl_min=pnl_min,
        pnl_max=pnl_max,
        r_min=r_min,
        r_max=r_max,
        weekdays=weekday or [],
        hours=hour or [],
        search=search,
        has_notes=has_notes,
    )


Filters = Annotated[TradeFilters, __import__("fastapi").Depends(trade_filters)]


@router.get("", response_model=ListResponse[TradeRead], summary="List trades")
async def list_trades(tenant: Tenant, filters: Filters, paging: Paging) -> ListResponse[TradeRead]:
    service = _service(tenant)
    page, tag_map = await service.list(filters, paging)
    labels = await service.labels_for(page.items)
    return ListResponse(data=trade_list(page.items, tag_map, labels), meta=PageMeta(**page.meta()))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[list[TradeRead]],
    summary="Record a trade",
)
async def create_trade(
    payload: TradeCreate, tenant: WritableTenant
) -> DataResponse[list[TradeRead]]:
    service = _service(tenant)
    trades = await service.create(payload)
    await tenant.session.commit()
    tag_map = await service.tags.for_trades([t.id for t in trades])
    labels = await service.labels_for(trades)
    return DataResponse(data=trade_list(trades, tag_map, labels))


@router.get("/{trade_id}", response_model=DataResponse[TradeDetail], summary="Trade detail")
async def get_trade(trade_id: uuid.UUID, tenant: Tenant) -> DataResponse[TradeDetail]:
    from tradeloom.services.files import FileService

    service = _service(tenant)
    trade = await service.get(trade_id)
    orders = await service.orders.for_trade(trade.id)
    tags = await service.tags.for_trade(trade.id)
    labels = await service.labels_for([trade])

    files = FileService(tenant.session, tenant.organization_id)
    screenshots = await files.list_trade_screenshots(trade.id)

    detail = TradeDetail(
        trade=trade_read(trade, tags=tags, labels=labels),
        orders=[OrderRead.model_validate(order) for order in orders],
        screenshots=[ScreenshotRead.model_validate(item) for item in screenshots],
        planned_reward_risk=calculations.planned_reward_risk(
            trade.entry_price,
            trade.initial_stop_loss or trade.stop_loss,
            trade.take_profit,
            trade.direction,
        ),
        efficiency=calculations.efficiency_ratio(trade.net_pnl, trade.mfe_amount),
    )
    return DataResponse(data=detail)


@router.patch("/{trade_id}", response_model=DataResponse[TradeRead], summary="Update a trade")
async def update_trade(
    trade_id: uuid.UUID, payload: TradeUpdate, tenant: WritableTenant
) -> DataResponse[TradeRead]:
    service = _service(tenant)
    trade = await service.update(trade_id, payload)
    await tenant.session.commit()
    tags = await service.tags.for_trade(trade.id)
    labels = await service.labels_for([trade])
    return DataResponse(data=trade_read(trade, tags=tags, labels=labels))


@router.delete("/{trade_id}", response_model=MessageResponse, summary="Delete a trade")
async def delete_trade(trade_id: uuid.UUID, tenant: WritableTenant) -> MessageResponse:
    await _service(tenant).delete(trade_id)
    await tenant.session.commit()
    return MessageResponse(message="Trade deleted.")


@router.post(
    "/{trade_id}/duplicate",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[TradeRead],
    summary="Duplicate a trade's plan",
)
async def duplicate_trade(trade_id: uuid.UUID, tenant: WritableTenant) -> DataResponse[TradeRead]:
    service = _service(tenant)
    trade = await service.duplicate(trade_id)
    await tenant.session.commit()
    return DataResponse(data=trade_read(trade))


@router.post(
    "/{trade_id}/fills",
    response_model=DataResponse[list[TradeRead]],
    summary="Add fills to an existing trade (scale in or out)",
)
async def add_fills(
    trade_id: uuid.UUID, payload: list[dict], tenant: WritableTenant
) -> DataResponse[list[TradeRead]]:
    from tradeloom.schemas.trade import FillInput

    service = _service(tenant)
    trade = await service.get(trade_id)
    account = await service._require_account(trade.account_id)
    instrument = await service.instruments.get(trade.instrument_id) if trade.instrument_id else None
    fills = [FillInput.model_validate(item) for item in payload]
    trades = await service.ingest_fills(
        account=account,
        symbol=trade.symbol,
        asset_type=trade.asset_type,
        fills=fills,
        source=trade.source,
        instrument=instrument,
    )
    await tenant.session.commit()
    tag_map = await service.tags.for_trades([t.id for t in trades])
    return DataResponse(data=trade_list(trades, tag_map))


@router.post("/bulk/tag", response_model=BulkResult, summary="Add or remove tags in bulk")
async def bulk_tag(payload: BulkTagAction, tenant: WritableTenant) -> BulkResult:
    count = await _service(tenant).bulk_tag(payload)
    await tenant.session.commit()
    return BulkResult(requested=len(payload.trade_ids), succeeded=count)


@router.post("/bulk/edit", response_model=BulkResult, summary="Edit trades in bulk")
async def bulk_edit(payload: BulkEditAction, tenant: WritableTenant) -> BulkResult:
    count = await _service(tenant).bulk_edit(payload)
    await tenant.session.commit()
    return BulkResult(requested=len(payload.trade_ids), succeeded=count)


@router.post("/bulk/delete", response_model=BulkResult, summary="Delete trades in bulk")
async def bulk_delete(payload: BulkTradeAction, tenant: WritableTenant) -> BulkResult:
    count = await _service(tenant).bulk_delete(payload.trade_ids)
    await tenant.session.commit()
    return BulkResult(requested=len(payload.trade_ids), succeeded=count)


@router.post("/marks", response_model=MessageResponse, summary="Set mark prices for open positions")
async def set_marks(payload: MarkPriceRequest, tenant: WritableTenant) -> MessageResponse:
    updated = await _service(tenant).apply_marks(payload.prices)
    await tenant.session.commit()
    return MessageResponse(
        message=f"Updated {updated} open position{'s' if updated != 1 else ''}.",
        data={"updated": updated},
    )


__all__ = ["router", "trade_filters"]
