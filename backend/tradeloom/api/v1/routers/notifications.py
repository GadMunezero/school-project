"""Notification endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query

from tradeloom.api.deps import CurrentPrincipal, DbSession, Paging
from tradeloom.schemas.common import (
    DataResponse,
    ListResponse,
    MessageResponse,
    PageMeta,
    TradeloomModel,
)
from tradeloom.services.notifications import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationRead(TradeloomModel):
    id: object
    kind: str
    severity: str
    title: str
    body: str | None
    link: str | None
    data: dict
    read_at: object | None
    created_at: object


class MarkReadRequest(TradeloomModel):
    notification_ids: list[uuid.UUID]


@router.get("", response_model=ListResponse[NotificationRead], summary="List notifications")
async def list_notifications(
    principal: CurrentPrincipal,
    session: DbSession,
    paging: Paging,
    unread_only: bool = Query(default=False),
) -> ListResponse[NotificationRead]:
    service = NotificationService(session)
    page = await service.list_for_user(principal.user_id, paging, unread_only=unread_only)
    await session.commit()
    return ListResponse(
        data=[NotificationRead.model_validate(item) for item in page.items],
        meta=PageMeta(**page.meta()),
    )


@router.get("/unread-count", response_model=DataResponse[dict], summary="Unread count")
async def unread_count(principal: CurrentPrincipal, session: DbSession) -> DataResponse[dict]:
    count = await NotificationService(session).unread_count(principal.user_id)
    await session.commit()
    return DataResponse(data={"unread": count})


@router.post("/read", response_model=MessageResponse, summary="Mark notifications as read")
async def mark_read(
    payload: MarkReadRequest, principal: CurrentPrincipal, session: DbSession
) -> MessageResponse:
    updated = await NotificationService(session).mark_read(
        principal.user_id, payload.notification_ids
    )
    await session.commit()
    return MessageResponse(message=f"{updated} marked as read.", data={"updated": updated})


@router.post("/read-all", response_model=MessageResponse, summary="Mark everything read")
async def mark_all_read(principal: CurrentPrincipal, session: DbSession) -> MessageResponse:
    updated = await NotificationService(session).mark_all_read(principal.user_id)
    await session.commit()
    return MessageResponse(message=f"{updated} marked as read.", data={"updated": updated})


__all__ = ["router"]
