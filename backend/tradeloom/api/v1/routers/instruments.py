"""Instrument catalogue endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from tradeloom.api.deps import Paging, Tenant, WritableTenant
from tradeloom.schemas.catalog import (
    InstrumentAliasCreate,
    InstrumentCreate,
    InstrumentRead,
    InstrumentUpdate,
)
from tradeloom.schemas.common import DataResponse, ListResponse, MessageResponse, PageMeta
from tradeloom.services.catalog import InstrumentService

router = APIRouter(prefix="/instruments", tags=["instruments"])


def _read(instrument) -> InstrumentRead:  # type: ignore[no-untyped-def]
    model = InstrumentRead.model_validate(instrument)
    model.is_global = instrument.organization_id is None
    return model


@router.get("", response_model=ListResponse[InstrumentRead], summary="List instruments")
async def list_instruments(
    tenant: Tenant,
    paging: Paging,
    search: Annotated[str | None, Query(max_length=60)] = None,
) -> ListResponse[InstrumentRead]:
    page = await InstrumentService(tenant.session, tenant.organization_id).list(paging, search)
    return ListResponse(data=[_read(item) for item in page.items], meta=PageMeta(**page.meta()))


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[InstrumentRead],
    summary="Create a workspace instrument",
)
async def create_instrument(
    payload: InstrumentCreate, tenant: WritableTenant
) -> DataResponse[InstrumentRead]:
    instrument = await InstrumentService(tenant.session, tenant.organization_id).create(payload)
    await tenant.session.commit()
    return DataResponse(data=_read(instrument))


@router.get(
    "/{instrument_id}", response_model=DataResponse[InstrumentRead], summary="Instrument detail"
)
async def get_instrument(instrument_id: uuid.UUID, tenant: Tenant) -> DataResponse[InstrumentRead]:
    instrument = await InstrumentService(tenant.session, tenant.organization_id).get(instrument_id)
    return DataResponse(data=_read(instrument))


@router.patch(
    "/{instrument_id}", response_model=DataResponse[InstrumentRead], summary="Update an instrument"
)
async def update_instrument(
    instrument_id: uuid.UUID, payload: InstrumentUpdate, tenant: WritableTenant
) -> DataResponse[InstrumentRead]:
    instrument = await InstrumentService(tenant.session, tenant.organization_id).update(
        instrument_id, payload
    )
    await tenant.session.commit()
    return DataResponse(data=_read(instrument))


@router.post(
    "/{instrument_id}/aliases",
    status_code=status.HTTP_201_CREATED,
    response_model=MessageResponse,
    summary="Add a broker symbol alias",
)
async def add_alias(
    instrument_id: uuid.UUID, payload: InstrumentAliasCreate, tenant: WritableTenant
) -> MessageResponse:
    service = InstrumentService(tenant.session, tenant.organization_id)
    alias = await service.add_alias(instrument_id, payload.alias, payload.source)
    await tenant.session.commit()
    return MessageResponse(message=f"Alias {alias.alias} added.", data={"id": str(alias.id)})


@router.get(
    "/{instrument_id}/aliases",
    response_model=DataResponse[list[dict]],
    summary="List symbol aliases",
)
async def list_aliases(instrument_id: uuid.UUID, tenant: Tenant) -> DataResponse[list[dict]]:
    service = InstrumentService(tenant.session, tenant.organization_id)
    await service.get(instrument_id)
    aliases = await service.aliases(instrument_id)
    return DataResponse(
        data=[
            {
                "id": str(a.id),
                "alias": a.alias,
                "source": a.source,
                "normalized": a.alias_normalized,
            }
            for a in aliases
        ]
    )


__all__ = ["router"]
