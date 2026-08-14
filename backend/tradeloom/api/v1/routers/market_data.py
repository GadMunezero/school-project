"""Market-data endpoints.

Charts request only the window they render. A candle request is always bounded — by an explicit
range, or by ``limit`` (capped at 5000) — so a chart can never pull an instrument's entire history
into the browser.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Query, UploadFile

from tradeloom.api.deps import Tenant, WritableTenant
from tradeloom.core.enums import Timeframe
from tradeloom.core.errors import NotFoundError, ValidationError
from tradeloom.models.market_data import MarketDataSource
from tradeloom.schemas.common import DataResponse
from tradeloom.services import market_import
from tradeloom.services.catalog import InstrumentService
from tradeloom.services.market_data import MarketDataService

router = APIRouter(prefix="/market-data", tags=["market-data"])

MAX_CANDLES = 5000

#: Every user-uploaded series shares one source per workspace, so imported candles are always
#: distinguishable from generated or vendor data.
IMPORT_SOURCE_KEY = "imported"


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


@router.post(
    "/import/inspect",
    response_model=DataResponse[dict],
    summary="Read a candle CSV's headers and suggest a column mapping",
)
async def inspect_candle_csv(
    tenant: WritableTenant,
    file: Annotated[UploadFile, File()],
) -> DataResponse[dict]:
    """Step one: show the user what is in the file and what we think each column means."""
    data = await file.read()
    return DataResponse(data=market_import.inspect(data))


@router.post(
    "/import",
    response_model=DataResponse[dict],
    summary="Import OHLCV candles from a CSV",
)
async def import_candles(
    tenant: WritableTenant,
    file: Annotated[UploadFile, File()],
    instrument_id: Annotated[uuid.UUID, Form()],
    timeframe: Annotated[Timeframe, Form()],
    column_mapping: Annotated[str, Form()],
    source_timezone: Annotated[str, Form()] = "UTC",
    dry_run: Annotated[bool, Form()] = False,
) -> DataResponse[dict]:
    """Parse a candle file and store what survives validation.

    Rows that cannot become a valid candle are returned with their row number and reason rather
    than repaired — a silently corrected bar would propagate into every report and backtest
    computed from the series.

    ``dry_run`` parses and reports without writing, so the user can check the mapping produced
    sensible candles before committing to it.
    """
    instruments = InstrumentService(tenant.session, tenant.organization_id)
    instrument = await instruments.get(instrument_id)
    if instrument is None:
        raise NotFoundError("Instrument not found.")

    try:
        mapping = json.loads(column_mapping)
    except json.JSONDecodeError as error:
        raise ValidationError("column_mapping must be a JSON object.") from error
    if not isinstance(mapping, dict):
        raise ValidationError("column_mapping must be a JSON object.")

    data = await file.read()
    parsed = market_import.parse_candles(data, mapping=mapping, timezone=source_timezone)
    summary = market_import.summarise(parsed, timeframe)
    summary["dry_run"] = dry_run
    summary["instrument"] = {"id": str(instrument.id), "symbol": instrument.symbol}

    if dry_run or not parsed.bars:
        summary["stored"] = 0
        return DataResponse(data=summary)

    service = MarketDataService(tenant.session)
    source = await service.source_by_key(IMPORT_SOURCE_KEY)
    if source is None:
        # One source per workspace for user-supplied candles, flagged as not real-time so
        # nothing downstream can present imported history as a live feed.
        source = MarketDataSource(
            key=IMPORT_SOURCE_KEY,
            name="Imported candles",
            description="OHLCV uploaded by a user from a CSV export.",
            provider_type="static",
            is_realtime=False,
        )
        tenant.session.add(source)
        await tenant.session.flush()

    report = await service.ingest(
        source=source,
        instrument=instrument,
        timeframe=timeframe,
        bars=parsed.bars,
    )
    await tenant.session.commit()

    summary["stored"] = parsed.accepted
    summary["source"] = {"id": str(source.id), "name": source.name}
    # The series-level view: gaps, duplicates and out-of-order bars across what is now stored.
    summary["quality"] = report.to_dict()
    return DataResponse(data=summary)
