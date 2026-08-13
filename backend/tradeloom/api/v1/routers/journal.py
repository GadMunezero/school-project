"""Journal entry endpoints (trade reviews and daily notes)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import Field

from tradeloom.api.deps import Paging, Tenant, WritableTenant
from tradeloom.schemas.common import (
    DataResponse,
    ListResponse,
    MessageResponse,
    PageMeta,
    TradeloomModel,
)
from tradeloom.services.journal import JournalService

router = APIRouter(prefix="/journal-entries", tags=["journal"])


class JournalEntryWrite(TradeloomModel):
    entry_date: date | None = None
    title: str | None = Field(default=None, max_length=200)
    body: str = ""
    entry_type: str = Field(default="note", pattern="^(trade_review|daily|weekly|note)$")
    mood: str | None = Field(default=None, max_length=24)
    discipline_rating: int | None = Field(default=None, ge=1, le=5)
    lessons: dict[str, Any] = Field(default_factory=dict)
    trade_id: Any | None = None
    account_id: Any | None = None


class JournalEntryPatch(TradeloomModel):
    entry_date: date | None = None
    title: str | None = Field(default=None, max_length=200)
    body: str | None = None
    entry_type: str | None = Field(default=None, pattern="^(trade_review|daily|weekly|note)$")
    mood: str | None = Field(default=None, max_length=24)
    discipline_rating: int | None = Field(default=None, ge=1, le=5)
    lessons: dict[str, Any] | None = None
    pinned: bool | None = None


class JournalEntryRead(TradeloomModel):
    id: Any
    entry_date: date
    title: str | None
    body: str
    entry_type: str
    mood: str | None
    discipline_rating: int | None
    lessons: dict[str, Any]
    trade_id: Any | None
    account_id: Any | None
    pinned_at: Any | None
    created_at: Any
    updated_at: Any


@router.get("", response_model=ListResponse[JournalEntryRead], summary="List journal entries")
async def list_entries(
    tenant: Tenant,
    paging: Paging,
    trade_id: uuid.UUID | None = None,
    entry_type: Annotated[str | None, Query(max_length=24)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    search: Annotated[str | None, Query(max_length=120)] = None,
) -> ListResponse[JournalEntryRead]:
    page = await JournalService(tenant.session, tenant.organization_id).list(
        paging,
        trade_id=trade_id,
        entry_type=entry_type,
        date_from=date_from,
        date_to=date_to,
        search=search,
    )
    return ListResponse(
        data=[JournalEntryRead.model_validate(item) for item in page.items],
        meta=PageMeta(**page.meta()),
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[JournalEntryRead],
    summary="Create a journal entry",
)
async def create_entry(
    payload: JournalEntryWrite, tenant: WritableTenant
) -> DataResponse[JournalEntryRead]:
    entry = await JournalService(
        tenant.session, tenant.organization_id, actor_user_id=tenant.user_id
    ).create(payload.model_dump())
    await tenant.session.commit()
    return DataResponse(data=JournalEntryRead.model_validate(entry))


@router.get("/{entry_id}", response_model=DataResponse[JournalEntryRead], summary="Entry detail")
async def get_entry(entry_id: uuid.UUID, tenant: Tenant) -> DataResponse[JournalEntryRead]:
    entry = await JournalService(tenant.session, tenant.organization_id).get(entry_id)
    return DataResponse(data=JournalEntryRead.model_validate(entry))


@router.patch(
    "/{entry_id}", response_model=DataResponse[JournalEntryRead], summary="Update an entry"
)
async def update_entry(
    entry_id: uuid.UUID, payload: JournalEntryPatch, tenant: WritableTenant
) -> DataResponse[JournalEntryRead]:
    entry = await JournalService(
        tenant.session, tenant.organization_id, actor_user_id=tenant.user_id
    ).update(entry_id, payload.model_dump(exclude_unset=True))
    await tenant.session.commit()
    return DataResponse(data=JournalEntryRead.model_validate(entry))


@router.delete("/{entry_id}", response_model=MessageResponse, summary="Delete an entry")
async def delete_entry(entry_id: uuid.UUID, tenant: WritableTenant) -> MessageResponse:
    await JournalService(tenant.session, tenant.organization_id).delete(entry_id)
    await tenant.session.commit()
    return MessageResponse(message="Journal entry deleted.")


__all__ = ["router"]
