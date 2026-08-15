"""Setup endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from tradeloom.api.deps import Tenant, WritableTenant
from tradeloom.schemas.catalog import SetupCreate, SetupRead, SetupUpdate
from tradeloom.schemas.common import DataResponse, MessageResponse
from tradeloom.services.catalog import SetupService

router = APIRouter(prefix="/setups", tags=["setups"])


@router.get("", response_model=DataResponse[list[SetupRead]], summary="List setups")
async def list_setups(tenant: Tenant) -> DataResponse[list[SetupRead]]:
    rows = await SetupService(tenant.session, tenant.organization_id).list_with_counts()
    items = []
    for setup, count in rows:
        model = SetupRead.model_validate(setup)
        model.trade_count = count
        items.append(model)
    return DataResponse(data=items)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[SetupRead],
    summary="Create a setup",
)
async def create_setup(payload: SetupCreate, tenant: WritableTenant) -> DataResponse[SetupRead]:
    setup = await SetupService(tenant.session, tenant.organization_id, tenant.user_id).create(
        payload
    )
    await tenant.session.commit()
    return DataResponse(data=SetupRead.model_validate(setup))


@router.patch("/{setup_id}", response_model=DataResponse[SetupRead], summary="Update a setup")
async def update_setup(
    setup_id: uuid.UUID, payload: SetupUpdate, tenant: WritableTenant
) -> DataResponse[SetupRead]:
    setup = await SetupService(tenant.session, tenant.organization_id, tenant.user_id).update(
        setup_id, payload
    )
    await tenant.session.commit()
    return DataResponse(data=SetupRead.model_validate(setup))


@router.delete("/{setup_id}", response_model=MessageResponse, summary="Delete a setup")
async def delete_setup(setup_id: uuid.UUID, tenant: WritableTenant) -> MessageResponse:
    await SetupService(tenant.session, tenant.organization_id, tenant.user_id).delete(setup_id)
    await tenant.session.commit()
    return MessageResponse(message="Setup deleted.")


__all__ = ["router"]
