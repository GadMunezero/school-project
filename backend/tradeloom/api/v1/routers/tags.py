"""Tag endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from tradeloom.api.deps import Tenant, WritableTenant
from tradeloom.schemas.catalog import TagCreate, TagRead, TagUpdate
from tradeloom.schemas.common import DataResponse, MessageResponse
from tradeloom.services.catalog import TagService

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=DataResponse[list[TagRead]], summary="List tags with usage counts")
async def list_tags(tenant: Tenant) -> DataResponse[list[TagRead]]:
    rows = await TagService(tenant.session, tenant.organization_id).list_with_counts()
    items = []
    for tag, count in rows:
        model = TagRead.model_validate(tag)
        model.trade_count = count
        items.append(model)
    return DataResponse(data=items)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[TagRead],
    summary="Create a tag",
)
async def create_tag(payload: TagCreate, tenant: WritableTenant) -> DataResponse[TagRead]:
    tag = await TagService(tenant.session, tenant.organization_id, tenant.user_id).create(payload)
    await tenant.session.commit()
    return DataResponse(data=TagRead.model_validate(tag))


@router.patch("/{tag_id}", response_model=DataResponse[TagRead], summary="Update a tag")
async def update_tag(
    tag_id: uuid.UUID, payload: TagUpdate, tenant: WritableTenant
) -> DataResponse[TagRead]:
    tag = await TagService(tenant.session, tenant.organization_id, tenant.user_id).update(
        tag_id, payload
    )
    await tenant.session.commit()
    return DataResponse(data=TagRead.model_validate(tag))


@router.delete("/{tag_id}", response_model=MessageResponse, summary="Delete a tag")
async def delete_tag(tag_id: uuid.UUID, tenant: WritableTenant) -> MessageResponse:
    await TagService(tenant.session, tenant.organization_id, tenant.user_id).delete(tag_id)
    await tenant.session.commit()
    return MessageResponse(message="Tag deleted. Existing trades keep their history.")


__all__ = ["router"]
