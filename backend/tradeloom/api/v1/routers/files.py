"""File upload and screenshot endpoints.

Uploads are validated three ways before a byte reaches storage: size, declared MIME against an
allow-list, and the file's magic bytes against its declared type. Downloads mint a short-lived
signed URL only after the file's ownership has been re-checked.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, UploadFile, status

from tradeloom.api.deps import AppSettings, Tenant, WritableTenant
from tradeloom.core.errors import ValidationError
from tradeloom.schemas.common import DataResponse, MessageResponse
from tradeloom.schemas.trade import ScreenshotRead
from tradeloom.services.files import FileService

router = APIRouter(prefix="/files", tags=["files"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[dict],
    summary="Upload a file",
)
async def upload_file(
    tenant: WritableTenant,
    settings: AppSettings,
    file: Annotated[UploadFile, File()],
    purpose: Annotated[str, Form(pattern="^(screenshot|import|avatar)$")] = "screenshot",
) -> DataResponse[dict]:
    data = await file.read()
    if len(data) > settings.upload_max_bytes:
        raise ValidationError(
            f"Files must be {settings.upload_max_bytes // (1024 * 1024)} MB or smaller."
        )

    service = FileService(tenant.session, tenant.organization_id, actor_user_id=tenant.user_id)
    record = await service.upload(
        data=data,
        declared_content_type=file.content_type or "application/octet-stream",
        original_filename=file.filename,
        purpose=purpose,
    )
    await tenant.session.commit()
    return DataResponse(
        data={
            "id": str(record.id),
            "content_type": record.content_type,
            "size_bytes": record.size_bytes,
            "original_filename": record.original_filename,
            "checksum_sha256": record.checksum_sha256,
        }
    )


@router.get("/{file_id}/url", response_model=DataResponse[dict], summary="Get a signed URL")
async def signed_url(
    file_id: uuid.UUID, tenant: Tenant, settings: AppSettings
) -> DataResponse[dict]:
    service = FileService(tenant.session, tenant.organization_id)
    url = await service.signed_url(file_id)
    return DataResponse(data={"url": url, "expires_in_seconds": settings.s3_signed_url_ttl_seconds})


@router.delete("/{file_id}", response_model=MessageResponse, summary="Delete a file")
async def delete_file(file_id: uuid.UUID, tenant: WritableTenant) -> MessageResponse:
    await FileService(tenant.session, tenant.organization_id).delete(file_id)
    await tenant.session.commit()
    return MessageResponse(message="File deleted.")


@router.get("/usage", response_model=DataResponse[dict], summary="Storage usage")
async def usage(tenant: Tenant) -> DataResponse[dict]:
    service = FileService(tenant.session, tenant.organization_id)
    return DataResponse(data=await service.storage_usage())


@router.post(
    "/screenshots",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[dict],
    summary="Upload and attach a screenshot in one request",
)
async def upload_screenshot(
    tenant: WritableTenant,
    file: Annotated[UploadFile, File()],
    trade_id: Annotated[uuid.UUID | None, Form()] = None,
    journal_entry_id: Annotated[uuid.UUID | None, Form()] = None,
    caption: Annotated[str | None, Form(max_length=255)] = None,
    phase: Annotated[str, Form(pattern="^(before|entry|management|exit|review)$")] = "review",
    timeframe: Annotated[str | None, Form(max_length=8)] = None,
) -> DataResponse[dict]:
    if trade_id is None and journal_entry_id is None:
        raise ValidationError("Attach the screenshot to a trade or a journal entry.")

    data = await file.read()
    service = FileService(tenant.session, tenant.organization_id, actor_user_id=tenant.user_id)
    record = await service.upload(
        data=data,
        declared_content_type=file.content_type or "application/octet-stream",
        original_filename=file.filename,
        purpose="screenshot",
    )
    screenshot = await service.attach_screenshot(
        file_id=record.id,
        trade_id=trade_id,
        journal_entry_id=journal_entry_id,
        caption=caption,
        phase=phase,
        timeframe=timeframe,
    )
    await tenant.session.commit()
    return DataResponse(
        data={
            "id": str(screenshot.id),
            "file_object_id": str(record.id),
            "url": await service.signed_url(record.id),
        }
    )


@router.get(
    "/screenshots/trade/{trade_id}",
    response_model=DataResponse[list[ScreenshotRead]],
    summary="Screenshots for a trade",
)
async def trade_screenshots(
    trade_id: uuid.UUID, tenant: Tenant
) -> DataResponse[list[ScreenshotRead]]:
    service = FileService(tenant.session, tenant.organization_id)
    views: list[Any] = await service.list_trade_screenshots(trade_id)
    return DataResponse(data=[ScreenshotRead.model_validate(view) for view in views])


@router.delete(
    "/screenshots/{screenshot_id}", response_model=MessageResponse, summary="Remove a screenshot"
)
async def delete_screenshot(screenshot_id: uuid.UUID, tenant: WritableTenant) -> MessageResponse:
    await FileService(tenant.session, tenant.organization_id).delete_screenshot(screenshot_id)
    await tenant.session.commit()
    return MessageResponse(message="Screenshot removed.")


__all__ = ["router"]
