"""Market-data endpoints.

Charts request only the window they render. A candle request is always bounded — by an explicit
range, or by ``limit`` (capped at 5000) — so a chart can never pull an instrument's entire history
into the browser.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query

from tradeloom.api.deps import Tenant
from tradeloom.core.enums import Timeframe
from tradeloom.schemas.common import DataResponse
from tradeloom.services.catalog import InstrumentService
from tradeloom.services.market_data import MarketDataService

router = APIRouter(prefix="/market-data", tags=["market-data"])

MAX_CANDLES = 5000


@router.get("/sources", response_model=DataResponse[list[dict]], summary="List data sources")
async def list_sources(tenant: Tenant) -> DataResponse[list[dict]]:
    sources = await MarketDataService(tenant.session).list_sources()
    return DataResponse(
        data=[
            {
                "id": str(source.id),
                "key": source.key,
                "name": source.name,
                "description": source.description,
                "provider_type": source.provider_type,
                # Surfaced verbatim: the UI labels a feed live only when this is true.
                "is_realtime": source.is_realtime,
                "last_synced_at": (
                    source.last_synced_at.isoformat() if source.last_synced_at else None
                ),
            }
            for source in sources
        ]
    )


@router.get(
    "/coverage/{instrument_id}",
    response_model=DataResponse[list[dict]],
    summary="Available series for an instrument",
)
async def coverage(instrument_id: uuid.UUID, tenant: Tenant) -> DataResponse[list[dict]]:
    instruments = InstrumentService(tenant.session, tenant.organization_id)
    await instruments.get(instrument_id)

    service = MarketDataService(tenant.session)
    rows = await service.available_series(instrument_id)
    return DataResponse(
        data=[
            {
                "source_id": str(row.source_id),
                "timeframe": row.timeframe,
                "bar_count": row.bar_count,
                "first_bar_at": row.first_bar_at.isoformat() if row.first_bar_at else None,
                "last_bar_at": row.last_bar_at.isoformat() if row.last_bar_at else None,
                "quality": row.quality_report,
            }
            for row in rows
        ]
    )


@router.get("/candles", response_model=DataResponse[dict], summary="Fetch OHLCV candles")
async def candles(
    tenant: Tenant,
    instrument_id: uuid.UUID,
    timeframe: Timeframe,
    start: datetime | None = None,
    end: datetime | None = None,
    source_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_CANDLES)] = 1000,
) -> DataResponse[dict]:
    instruments = InstrumentService(tenant.session, tenant.organization_id)
    instrument = await instruments.get(instrument_id)

    service = MarketDataService(tenant.session)
    source = await service.get_source(source_id) if source_id else await service.default_source()
    series, resolved = await service.get_bars(
        instrument.id, timeframe, source=source, start=start, end=end, limit=limit
    )

    payload: dict[str, Any] = {
        "instrument": {
            "id": str(instrument.id),
            "symbol": instrument.symbol,
            "price_precision": instrument.price_precision,
            "tick_size": format(instrument.tick_size.normalize(), "f"),
        },
        "source": {
            "id": str(resolved.id),
            "key": resolved.key,
            "name": resolved.name,
            "is_realtime": resolved.is_realtime,
        },
        "timeframe": timeframe.value,
        "candles": MarketDataService.to_chart_payload(series),
        "truncated": len(series) >= limit,
    }
    return DataResponse(data=payload)


@router.get(
    "/quality", response_model=DataResponse[dict], summary="Validate a series and report issues"
)
async def quality(
    tenant: Tenant,
    instrument_id: uuid.UUID,
    timeframe: Timeframe,
    source_id: uuid.UUID | None = None,
) -> DataResponse[dict]:
    instruments = InstrumentService(tenant.session, tenant.organization_id)
    await instruments.get(instrument_id)

    service = MarketDataService(tenant.session)
    source = await service.get_source(source_id) if source_id else await service.default_source()
    report = await service.validate_series(instrument_id, timeframe, source.id)
    await tenant.session.commit()
    return DataResponse(data=report.to_dict())


__all__ = ["router"]
