"""File metadata and screenshot management.

Every read path re-checks ownership before minting a signed URL. Possession of a file id is never
sufficient: the row must belong to the requesting organization, which is what closes the classic
IDOR on "download my screenshot" endpoints.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core.config import get_settings
from tradeloom.core.errors import NotFoundError, ValidationError
from tradeloom.core.security import checksum_bytes
from tradeloom.core.timeutil import utcnow
from tradeloom.models.file import FileObject
from tradeloom.models.journal import Screenshot
from tradeloom.models.trading import Trade
from tradeloom.repositories.base import TenantRepository
from tradeloom.services.storage import (
    ObjectStorage,
    build_object_key,
    get_storage,
    validate_upload,
)


class FileRepository(TenantRepository[FileObject]):
    model = FileObject


class ScreenshotRepository(TenantRepository[Screenshot]):
    model = Screenshot


@dataclass(slots=True)
class ScreenshotView:
    """Screenshot joined with its file metadata and a freshly signed URL."""

    id: uuid.UUID
    file_object_id: uuid.UUID
    caption: str | None
    phase: str
    timeframe: str | None
    display_order: int
    content_type: str
    size_bytes: int
    original_filename: str | None
    created_at: Any
    url: str | None


class FileService:
    def __init__(
        self,
        session: AsyncSession,
        organization_id: uuid.UUID,
        *,
        actor_user_id: uuid.UUID | None = None,
        storage: ObjectStorage | None = None,
    ) -> None:
        self.session = session
        self.organization_id = organization_id
        self.actor_user_id = actor_user_id
        self.storage = storage or get_storage()
        self.files = FileRepository(session, organization_id)
        self.screenshots = ScreenshotRepository(session, organization_id)
        self.settings = get_settings()

    # -- upload --------------------------------------------------------

    async def upload(
        self,
        *,
        data: bytes,
        declared_content_type: str,
        original_filename: str | None,
        purpose: str = "screenshot",
    ) -> FileObject:
        content_type = validate_upload(data, declared_content_type, self.settings)
        await self._check_storage_quota(len(data))

        checksum = checksum_bytes(data)
        key = build_object_key(self.organization_id, purpose, content_type)
        stored = self.storage.put(key, data, content_type)

        record = FileObject(
            organization_id=self.organization_id,
            owner_user_id=self.actor_user_id,
            bucket=stored.bucket,
            object_key=stored.key,
            # The client's filename is stored for display only; it never influences the key.
            original_filename=(original_filename or "")[:255] or None,
            content_type=content_type,
            size_bytes=stored.size_bytes,
            checksum_sha256=checksum,
            purpose=purpose,
            is_available=True,
        )
        await self.files.add(record)
        return record

    async def _check_storage_quota(self, incoming_bytes: int) -> None:
        from tradeloom.services.entitlements import EntitlementService

        limits = await EntitlementService(self.session).limits_for(self.organization_id)
        if limits.max_storage_bytes is None:
            return
        used = await self.session.scalar(
            select(func.coalesce(func.sum(FileObject.size_bytes), 0)).where(
                FileObject.organization_id == self.organization_id,
                FileObject.deleted_at.is_(None),
            )
        )
        if int(used or 0) + incoming_bytes > limits.max_storage_bytes:
            from tradeloom.core.errors import EntitlementError

            raise EntitlementError(
                "You have reached your plan's storage limit.",
                feature="max_storage_bytes",
                limit=limits.max_storage_bytes,
                current=int(used or 0),
            )

    # -- access --------------------------------------------------------

    async def get(self, file_id: uuid.UUID) -> FileObject:
        record = await self.files.get(file_id)
        if record is None:
            raise NotFoundError("File not found.")
        return record

    async def signed_url(self, file_id: uuid.UUID) -> str:
        """Ownership is re-checked here; a signed URL is never minted from an id alone."""
        record = await self.get(file_id)
        return self.storage.signed_url(record.object_key, self.settings.s3_signed_url_ttl_seconds)

    async def download(self, file_id: uuid.UUID) -> tuple[bytes, FileObject]:
        record = await self.get(file_id)
        return self.storage.get(record.object_key), record

    async def delete(self, file_id: uuid.UUID) -> None:
        record = await self.get(file_id)
        await self.files.soft_delete(record.id)
        self.storage.delete(record.object_key)

    async def storage_usage(self) -> dict[str, int]:
        used = await self.session.scalar(
            select(func.coalesce(func.sum(FileObject.size_bytes), 0)).where(
                FileObject.organization_id == self.organization_id,
                FileObject.deleted_at.is_(None),
            )
        )
        count = await self.files.count()
        return {"bytes_used": int(used or 0), "file_count": count}

    # -- screenshots ---------------------------------------------------

    async def attach_screenshot(
        self,
        *,
        file_id: uuid.UUID,
        trade_id: uuid.UUID | None = None,
        journal_entry_id: uuid.UUID | None = None,
        caption: str | None = None,
        phase: str = "review",
        timeframe: str | None = None,
    ) -> Screenshot:
        record = await self.get(file_id)
        if not record.content_type.startswith("image/"):
            raise ValidationError("Only image files can be attached as screenshots.")

        if trade_id is not None:
            trade = await self.session.get(Trade, trade_id)
            if trade is None or trade.organization_id != self.organization_id:
                raise NotFoundError("Trade not found.")

        existing = await self.screenshots.count(Screenshot.trade_id == trade_id) if trade_id else 0
        screenshot = Screenshot(
            organization_id=self.organization_id,
            file_object_id=record.id,
            trade_id=trade_id,
            journal_entry_id=journal_entry_id,
            uploaded_by_user_id=self.actor_user_id,
            caption=caption,
            phase=phase,
            timeframe=timeframe,
            display_order=existing,
        )
        self.session.add(screenshot)
        await self.session.flush()
        return screenshot

    async def list_trade_screenshots(self, trade_id: uuid.UUID) -> list[ScreenshotView]:
        return await self._list_screenshots(Screenshot.trade_id == trade_id)

    async def list_journal_screenshots(self, entry_id: uuid.UUID) -> list[ScreenshotView]:
        return await self._list_screenshots(Screenshot.journal_entry_id == entry_id)

    async def _list_screenshots(self, condition) -> list[ScreenshotView]:  # type: ignore[no-untyped-def]
        result = await self.session.execute(
            select(Screenshot, FileObject)
            .join(FileObject, FileObject.id == Screenshot.file_object_id)
            .where(
                Screenshot.organization_id == self.organization_id,
                Screenshot.deleted_at.is_(None),
                condition,
            )
            .order_by(Screenshot.display_order.asc(), Screenshot.created_at.asc())
        )
        views: list[ScreenshotView] = []
        for screenshot, file_object in result.all():
            url: str | None
            try:
                url = self.storage.signed_url(
                    file_object.object_key, self.settings.s3_signed_url_ttl_seconds
                )
            except Exception:
                # A storage outage must not break the trade page; the image just cannot render.
                url = None
            views.append(
                ScreenshotView(
                    id=screenshot.id,
                    file_object_id=file_object.id,
                    caption=screenshot.caption,
                    phase=screenshot.phase,
                    timeframe=screenshot.timeframe,
                    display_order=screenshot.display_order,
                    content_type=file_object.content_type,
                    size_bytes=file_object.size_bytes,
                    original_filename=file_object.original_filename,
                    created_at=screenshot.created_at,
                    url=url,
                )
            )
        return views

    async def delete_screenshot(self, screenshot_id: uuid.UUID) -> None:
        screenshot = await self.screenshots.get(screenshot_id)
        if screenshot is None:
            raise NotFoundError("Screenshot not found.")
        await self.screenshots.soft_delete(screenshot.id)

    async def create_export(
        self, *, data: bytes, content_type: str, filename: str, ttl_days: int = 7
    ) -> FileObject:
        """Store a generated export and mark it for cleanup."""
        from datetime import timedelta

        record = await self.upload(
            data=data,
            declared_content_type=content_type,
            original_filename=filename,
            purpose="export",
        )
        record.expires_at = utcnow() + timedelta(days=ttl_days)
        await self.session.flush()
        return record


__all__ = ["FileRepository", "FileService", "ScreenshotRepository", "ScreenshotView"]
