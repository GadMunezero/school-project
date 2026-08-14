"""Edge report endpoints.

A report is computed on demand from stored candles — nothing is precomputed and cached, because a
stale statistic is worse than a slow one. The response carries the headline rate *and* every
session behind it, so the client can offer a drill-down into any single day without a second
round trip.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query

from tradeloom.api.deps import Tenant
from tradeloom.core.enums import Timeframe
from tradeloom.core.errors import NotFoundError, UnprocessableStateError
from tradeloom.engine.reports import available_conditions, list_reports, run_report
from tradeloom.schemas.common import DataResponse
from tradeloom.services.catalog import InstrumentService
from tradeloom.services.market_data import MarketDataService

router = APIRouter(prefix="/reports", tags=["reports"])

#: Below this there is no statistic worth quoting, only noise dressed up as one.
MIN_SESSIONS = 5


@router.get(
    "",
    response_model=DataResponse[list[dict]],
    summary="Reports the engine can run, with their parameters",
)
async def available(tenant: Tenant) -> DataResponse[list[dict]]:
    return DataResponse(
        data=[
            {
                "key": spec.key,
                "name": spec.name,
                "question": spec.question,
                "description": spec.description,
                "parameters": list(spec.parameters),
            }
            for spec in list_reports()
        ]
    )


@router.get(
    "/{report_key}",
    response_model=DataResponse[dict],
    summary="Run a report over an instrument's history",
)
async def run(
    report_key: str,
    tenant: Tenant,
    instrument_id: uuid.UUID,
    timeframe: Timeframe = Timeframe.H1,
    start: datetime | None = None,
    end: datetime | None = None,
    session_timezone: Annotated[str, Query(max_length=64)] = "America/New_York",
    minutes: Annotated[int | None, Query(ge=5, le=240)] = None,
    minimum_percent: Annotated[float | None, Query(ge=0.01, le=10)] = None,
) -> DataResponse[dict]:
    """Compute a report and return it with every session that produced it.

    The report key is looked up in the engine's registry; an unknown key is a 404 rather than an
    empty result, because "this report does not exist" and "this never happened" are different
    answers and must not look the same.
    """
    if report_key not in {spec.key for spec in list_reports()}:
        raise NotFoundError("Report not found.")

    instruments = InstrumentService(tenant.session, tenant.organization_id)
    instrument = await instruments.get(instrument_id)
    if instrument is None:
        raise NotFoundError("Instrument not found.")

    market_data = MarketDataService(tenant.session)
    series, source = await market_data.get_bars(instrument.id, timeframe, start=start, end=end)
    if len(series) == 0 or source is None:
        raise UnprocessableStateError(
            f"No {timeframe.value} candles are stored for {instrument.symbol}."
        )

    parameters: dict[str, Any] = {}
    if minutes is not None:
        parameters["minutes"] = minutes
    if minimum_percent is not None:
        parameters["minimum_percent"] = str(minimum_percent)

    result = run_report(
        report_key,
        series,
        timezone=session_timezone,
        parameters=parameters,
        timeframe=timeframe.value,
    )
    payload = result.to_dict()
    payload["instrument"] = {
        "id": str(instrument.id),
        "symbol": instrument.symbol,
        "name": instrument.name,
    }
    payload["timeframe"] = timeframe.value
    payload["session_timezone"] = session_timezone
    payload["source"] = {"id": str(source.id), "name": source.name}
    # The client shows the rate differently when the sample is too small to lean on.
    payload["sufficient_sample"] = result.sample_size >= MIN_SESSIONS
    # The same rate, split by what was already knowable when each session opened. A flat headline
    # describes the average day; these describe the days you can actually recognise in advance.
    payload["conditions"] = available_conditions(result)
    payload["minimum_sample"] = MIN_SESSIONS
    return DataResponse(data=payload)


__all__ = ["MIN_SESSIONS", "router"]
