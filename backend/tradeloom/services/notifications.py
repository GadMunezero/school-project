"""In-app notifications.

One entry point (:meth:`NotificationService.notify`) used by every producer — imports,
backtests, billing, security events. Adding a new notification kind means adding an enum member
and a template here, not a new delivery path.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core.enums import NotificationKind, NotificationSeverity
from tradeloom.core.pagination import Page, PageParams
from tradeloom.core.timeutil import utcnow
from tradeloom.models.platform import Notification


@dataclass(frozen=True, slots=True)
class NotificationTemplate:
    severity: NotificationSeverity
    title: str
    body: str


#: ``{placeholders}`` are filled from the ``data`` dict passed to :meth:`notify`.
TEMPLATES: dict[NotificationKind, NotificationTemplate] = {
    NotificationKind.IMPORT_COMPLETED: NotificationTemplate(
        NotificationSeverity.SUCCESS,
        "Import finished",
        "{imported} of {total} rows imported into {account_name}.",
    ),
    NotificationKind.IMPORT_FAILED: NotificationTemplate(
        NotificationSeverity.ERROR,
        "Import failed",
        "The import of {filename} could not be completed: {reason}",
    ),
    NotificationKind.BACKTEST_COMPLETED: NotificationTemplate(
        NotificationSeverity.SUCCESS,
        "Backtest finished",
        "{backtest_name} completed with {trade_count} trades and a {total_return} return.",
    ),
    NotificationKind.BACKTEST_FAILED: NotificationTemplate(
        NotificationSeverity.ERROR,
        "Backtest failed",
        "{backtest_name} could not be completed: {reason}",
    ),
    NotificationKind.SUBSCRIPTION_UPDATED: NotificationTemplate(
        NotificationSeverity.INFO,
        "Subscription updated",
        "Your workspace is now on the {plan} plan.",
    ),
    NotificationKind.SUBSCRIPTION_PAYMENT_FAILED: NotificationTemplate(
        NotificationSeverity.WARNING,
        "Payment failed",
        "We could not charge your card. Update your payment method to keep Pro features.",
    ),
    NotificationKind.EXPORT_READY: NotificationTemplate(
        NotificationSeverity.SUCCESS,
        "Export ready",
        "Your {export_type} export is ready to download.",
    ),
    NotificationKind.ACCOUNT_SECURITY: NotificationTemplate(
        NotificationSeverity.WARNING,
        "Security alert",
        "{message}",
    ),
}


class NotificationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def notify(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        kind: NotificationKind,
        data: dict[str, Any] | None = None,
        link: str | None = None,
        title_override: str | None = None,
        body_override: str | None = None,
    ) -> Notification:
        template = TEMPLATES[kind]
        payload = data or {}
        try:
            body = body_override or template.body.format(**payload)
        except KeyError:
            # A missing placeholder must not lose the notification; fall back to the raw template.
            body = body_override or template.body

        notification = Notification(
            organization_id=organization_id,
            user_id=user_id,
            kind=kind,
            severity=template.severity,
            title=(title_override or template.title)[:200],
            body=body,
            link=link,
            data=payload,
        )
        self.session.add(notification)
        await self.session.flush()
        return notification

    async def list_for_user(
        self, user_id: uuid.UUID, params: PageParams, *, unread_only: bool = False
    ) -> Page[Notification]:
        conditions = [Notification.user_id == user_id]
        if unread_only:
            conditions.append(Notification.read_at.is_(None))

        total = await self.session.scalar(
            select(func.count()).select_from(Notification).where(*conditions)
        )
        result = await self.session.execute(
            select(Notification)
            .where(*conditions)
            .order_by(Notification.created_at.desc())
            .offset(params.offset)
            .limit(params.limit)
        )
        return Page(
            items=list(result.scalars().all()),
            total=int(total or 0),
            page=params.page,
            page_size=params.page_size,
        )

    async def unread_count(self, user_id: uuid.UUID) -> int:
        total = await self.session.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        )
        return int(total or 0)

    async def mark_read(self, user_id: uuid.UUID, notification_ids: list[uuid.UUID]) -> int:
        if not notification_ids:
            return 0
        result = await self.session.execute(
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.id.in_(notification_ids),
                Notification.read_at.is_(None),
            )
            .values(read_at=utcnow())
        )
        return int(result.rowcount or 0)

    async def mark_all_read(self, user_id: uuid.UUID) -> int:
        result = await self.session.execute(
            update(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
            .values(read_at=utcnow())
        )
        return int(result.rowcount or 0)


__all__ = ["TEMPLATES", "NotificationService", "NotificationTemplate"]
