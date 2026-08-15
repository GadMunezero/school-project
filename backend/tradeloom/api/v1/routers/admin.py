"""Platform administration.

Every route depends on :data:`~tradeloom.api.deps.AdminUser`, which checks the platform role
server-side. There is no client-supplied flag anywhere in this module, and admin routes are not
tenant-scoped — that is precisely why the authorisation check is unconditional.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, status
from pydantic import Field
from sqlalchemy import func, select

from tradeloom.api.deps import AdminUser, DbSession, Paging
from tradeloom.core.enums import (
    AuditAction,
    ImportStatus,
    JobStatus,
    SubscriptionPlan,
    UserStatus,
)
from tradeloom.core.errors import NotFoundError
from tradeloom.core.timeutil import utcnow
from tradeloom.models.identity import LoginAttempt, User
from tradeloom.models.imports import Import
from tradeloom.models.organization import Organization, OrganizationMember
from tradeloom.models.platform import (
    AuditLog,
    FeedbackReport,
    InviteRedemption,
    JobRecord,
    Subscription,
)
from tradeloom.models.trading import Trade
from tradeloom.schemas.common import (
    DataResponse,
    ListResponse,
    MessageResponse,
    PageMeta,
    TradeloomModel,
)
from tradeloom.services.audit import AuditService
from tradeloom.services.billing import BillingService
from tradeloom.services.invites import InviteService
from tradeloom.services.jobs import JobService

router = APIRouter(prefix="/admin", tags=["admin"])


class UserStatusUpdate(TradeloomModel):
    status: UserStatus
    reason: str = Field(min_length=3, max_length=255)


class PlanOverride(TradeloomModel):
    plan: SubscriptionPlan
    reason: str = Field(min_length=3, max_length=255)


class FeedbackStatusUpdate(TradeloomModel):
    status: str = Field(pattern="^(new|reviewed|closed)$")


class InviteCreate(TradeloomModel):
    #: Who it is for, so two outstanding invites can be told apart.
    note: str | None = Field(default=None, max_length=160)
    max_uses: int = Field(default=1, ge=1, le=500)
    #: Null never expires. A beta invite that works forever is a beta that never closed.
    expires_in_days: int | None = Field(default=30, ge=1, le=365)


@router.get("/overview", response_model=DataResponse[dict], summary="System overview")
async def overview(admin: AdminUser, session: DbSession) -> DataResponse[dict]:
    async def count(model, *conditions) -> int:  # type: ignore[no-untyped-def]
        stmt = select(func.count()).select_from(model)
        for condition in conditions:
            stmt = stmt.where(condition)
        return int(await session.scalar(stmt) or 0)

    jobs_by_status = await session.execute(
        select(JobRecord.status, func.count()).group_by(JobRecord.status)
    )
    failed_recent = await session.execute(
        select(JobRecord.kind, func.count())
        .where(JobRecord.status == JobStatus.FAILED)
        .group_by(JobRecord.kind)
    )

    return DataResponse(
        data={
            "users": {
                "total": await count(User, User.deleted_at.is_(None)),
                "active": await count(User, User.status == UserStatus.ACTIVE),
                "pending": await count(User, User.status == UserStatus.PENDING),
                "suspended": await count(User, User.status == UserStatus.SUSPENDED),
                "deletion_requested": await count(User, User.deletion_requested_at.isnot(None)),
            },
            "organizations": await count(Organization, Organization.deleted_at.is_(None)),
            "trades": await count(Trade, Trade.deleted_at.is_(None)),
            "jobs": {status.value: int(total) for status, total in jobs_by_status.all()},
            "failed_jobs_by_kind": {kind: int(total) for kind, total in failed_recent.all()},
            "failed_imports": await count(Import, Import.status == ImportStatus.FAILED),
            "failed_logins_24h": await count(
                LoginAttempt,
                LoginAttempt.succeeded.is_(False),
                LoginAttempt.created_at >= utcnow().replace(hour=0, minute=0, second=0),
            ),
            "generated_at": utcnow().isoformat(),
        }
    )


@router.get("/users", response_model=DataResponse[list[dict]], summary="List users")
async def list_users(
    admin: AdminUser,
    session: DbSession,
    paging: Paging,
    search: Annotated[str | None, Query(max_length=120)] = None,
) -> DataResponse[list[dict]]:
    stmt = select(User).where(User.deleted_at.is_(None))
    if search:
        needle = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            func.lower(User.email).like(needle)
            | func.lower(func.coalesce(User.full_name, "")).like(needle)
        )
    stmt = stmt.order_by(User.created_at.desc()).offset(paging.offset).limit(paging.limit)
    result = await session.execute(stmt)

    return DataResponse(
        data=[
            {
                "id": str(user.id),
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role.value,
                "status": user.status.value,
                "email_verified": user.email_verified,
                "created_at": user.created_at.isoformat(),
                "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
                "deletion_requested_at": (
                    user.deletion_requested_at.isoformat() if user.deletion_requested_at else None
                ),
            }
            for user in result.scalars().all()
        ]
    )


@router.patch(
    "/users/{user_id}/status", response_model=MessageResponse, summary="Suspend or reinstate a user"
)
async def set_user_status(
    user_id: uuid.UUID, payload: UserStatusUpdate, admin: AdminUser, session: DbSession
) -> MessageResponse:
    user = await session.get(User, user_id)
    if user is None or user.deleted_at is not None:
        raise NotFoundError("User not found.")

    user.status = payload.status
    if payload.status is UserStatus.SUSPENDED:
        from tradeloom.services.auth import AuthService

        await AuthService(session).revoke_all_sessions(user.id, reason="suspended_by_admin")

    await AuditService(session).record(
        AuditAction.ADMIN_ACTION,
        actor_user_id=admin.id,
        actor_email=admin.email,
        entity_type="user",
        entity_id=user.id,
        summary=f"Status set to {payload.status.value}: {payload.reason}"[:255],
    )
    await session.commit()
    return MessageResponse(message=f"User status set to {payload.status.value}.")


@router.get("/organizations", response_model=DataResponse[list[dict]], summary="List workspaces")
async def list_organizations(
    admin: AdminUser, session: DbSession, paging: Paging
) -> DataResponse[list[dict]]:
    result = await session.execute(
        select(Organization, Subscription)
        .outerjoin(Subscription, Subscription.organization_id == Organization.id)
        .where(Organization.deleted_at.is_(None))
        .order_by(Organization.created_at.desc())
        .offset(paging.offset)
        .limit(paging.limit)
    )
    rows = result.all()

    payload: list[dict[str, Any]] = []
    for organization, subscription in rows:
        members = await session.scalar(
            select(func.count())
            .select_from(OrganizationMember)
            .where(OrganizationMember.organization_id == organization.id)
        )
        trades = await session.scalar(
            select(func.count())
            .select_from(Trade)
            .where(Trade.organization_id == organization.id, Trade.deleted_at.is_(None))
        )
        payload.append(
            {
                "id": str(organization.id),
                "name": organization.name,
                "slug": organization.slug,
                "is_personal": organization.is_personal,
                "created_at": organization.created_at.isoformat(),
                "member_count": int(members or 0),
                "trade_count": int(trades or 0),
                "plan": subscription.plan.value if subscription else SubscriptionPlan.FREE.value,
                "subscription_status": subscription.status.value if subscription else None,
            }
        )
    return DataResponse(data=payload)


@router.post(
    "/organizations/{organization_id}/plan",
    response_model=MessageResponse,
    summary="Override a workspace's plan",
)
async def override_plan(
    organization_id: uuid.UUID, payload: PlanOverride, admin: AdminUser, session: DbSession
) -> MessageResponse:
    organization = await session.get(Organization, organization_id)
    if organization is None:
        raise NotFoundError("Workspace not found.")

    service = BillingService(session, actor_user_id=admin.id)
    await service.set_plan_manually(organization_id, payload.plan, reason=payload.reason)
    await session.commit()
    return MessageResponse(message=f"Plan set to {payload.plan.value}.")


@router.get("/jobs", response_model=ListResponse[dict], summary="Background jobs")
async def list_jobs(
    admin: AdminUser,
    session: DbSession,
    paging: Paging,
    job_status: Annotated[JobStatus | None, Query(alias="status")] = None,
    kind: Annotated[str | None, Query(max_length=48)] = None,
) -> ListResponse[dict]:
    conditions = []
    if job_status:
        conditions.append(JobRecord.status == job_status)
    if kind:
        conditions.append(JobRecord.kind == kind)

    total_stmt = select(func.count()).select_from(JobRecord)
    stmt = select(JobRecord)
    for condition in conditions:
        total_stmt = total_stmt.where(condition)
        stmt = stmt.where(condition)

    total = int(await session.scalar(total_stmt) or 0)
    result = await session.execute(
        stmt.order_by(JobRecord.created_at.desc()).offset(paging.offset).limit(paging.limit)
    )
    records = list(result.scalars().all())

    return ListResponse(
        data=[
            # Admins see the internal detail that ordinary users never do.
            {**JobService.to_dict(record), "error_detail": record.error_detail}
            for record in records
        ],
        meta=PageMeta(
            page=paging.page,
            page_size=paging.page_size,
            total=total,
            total_pages=(total + paging.page_size - 1) // paging.page_size,
            has_next=paging.offset + len(records) < total,
        ),
    )


@router.get("/imports/failed", response_model=DataResponse[list[dict]], summary="Failed imports")
async def failed_imports(admin: AdminUser, session: DbSession) -> DataResponse[list[dict]]:
    result = await session.execute(
        select(Import)
        .where(Import.status == ImportStatus.FAILED)
        .order_by(Import.created_at.desc())
        .limit(100)
    )
    return DataResponse(
        data=[
            {
                "id": str(record.id),
                "organization_id": str(record.organization_id),
                "filename": record.filename,
                "total_rows": record.total_rows,
                "invalid_rows": record.invalid_rows,
                "error_summary": record.error_summary,
                "created_at": record.created_at.isoformat(),
            }
            for record in result.scalars().all()
        ]
    )


@router.get("/audit-logs", response_model=ListResponse[dict], summary="Audit log")
async def audit_logs(
    admin: AdminUser,
    session: DbSession,
    paging: Paging,
    organization_id: uuid.UUID | None = None,
    action: Annotated[AuditAction | None, Query()] = None,
) -> ListResponse[dict]:
    conditions = []
    if organization_id:
        conditions.append(AuditLog.organization_id == organization_id)
    if action:
        conditions.append(AuditLog.action == action)

    total_stmt = select(func.count()).select_from(AuditLog)
    stmt = select(AuditLog)
    for condition in conditions:
        total_stmt = total_stmt.where(condition)
        stmt = stmt.where(condition)

    total = int(await session.scalar(total_stmt) or 0)
    result = await session.execute(
        stmt.order_by(AuditLog.created_at.desc()).offset(paging.offset).limit(paging.limit)
    )
    records = list(result.scalars().all())

    return ListResponse(
        data=[
            {
                "id": str(record.id),
                "created_at": record.created_at.isoformat(),
                "organization_id": (
                    str(record.organization_id) if record.organization_id else None
                ),
                "actor_email": record.actor_email,
                "action": record.action.value,
                "entity_type": record.entity_type,
                "entity_id": str(record.entity_id) if record.entity_id else None,
                "summary": record.summary,
                "changes": record.changes,
                "ip_address": record.ip_address,
                "request_id": record.request_id,
            }
            for record in records
        ],
        meta=PageMeta(
            page=paging.page,
            page_size=paging.page_size,
            total=total,
            total_pages=(total + paging.page_size - 1) // paging.page_size,
            has_next=paging.offset + len(records) < total,
        ),
    )


@router.get("/invites", response_model=DataResponse[list[dict]], summary="Outstanding invites")
async def list_invites(admin: AdminUser, session: DbSession) -> DataResponse[list[dict]]:
    service = InviteService(session, actor_user_id=admin.id)
    invites = await service.list()

    # Who each code let in, so an administrator can match an invite to a person.
    rows = await session.execute(
        select(InviteRedemption.invite_code_id, InviteRedemption.email).where(
            InviteRedemption.invite_code_id.in_([invite.id for invite in invites] or [None])
        )
    )
    by_invite: dict[Any, list[str]] = {}
    for invite_id, email in rows.all():
        by_invite.setdefault(invite_id, []).append(email or "—")

    return DataResponse(
        data=[
            InviteService.to_dict(invite, redeemed_by=by_invite.get(invite.id, []))
            for invite in invites
        ]
    )


@router.post(
    "/invites",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[dict],
    summary="Issue an invite code",
)
async def create_invite(
    payload: InviteCreate, admin: AdminUser, session: DbSession
) -> DataResponse[dict]:
    service = InviteService(session, actor_user_id=admin.id)
    invite = await service.create(
        note=payload.note, max_uses=payload.max_uses, expires_in_days=payload.expires_in_days
    )
    await AuditService(session).record(
        AuditAction.ADMIN_ACTION,
        actor_user_id=admin.id,
        actor_email=admin.email,
        entity_type="invite_code",
        entity_id=invite.id,
        summary=f"Issued invite for {payload.note or 'an unnamed recipient'}",
    )
    await session.commit()
    return DataResponse(data=InviteService.to_dict(invite))


@router.post(
    "/invites/{invite_id}/revoke",
    response_model=DataResponse[dict],
    summary="Revoke an invite code",
)
async def revoke_invite(
    invite_id: uuid.UUID, admin: AdminUser, session: DbSession
) -> DataResponse[dict]:
    service = InviteService(session, actor_user_id=admin.id)
    invite = await service.revoke(invite_id)
    await AuditService(session).record(
        AuditAction.ADMIN_ACTION,
        actor_user_id=admin.id,
        actor_email=admin.email,
        entity_type="invite_code",
        entity_id=invite.id,
        summary="Revoked an invite",
    )
    await session.commit()
    return DataResponse(data=InviteService.to_dict(invite))


@router.get("/feedback", response_model=ListResponse[dict], summary="Feedback from users")
async def list_feedback(
    admin: AdminUser,
    session: DbSession,
    paging: Paging,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> ListResponse[dict]:
    stmt = select(FeedbackReport)
    if status_filter:
        stmt = stmt.where(FeedbackReport.status == status_filter)

    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    result = await session.execute(
        stmt.order_by(FeedbackReport.created_at.desc())
        .offset(paging.offset)
        .limit(paging.page_size)
    )
    reports = list(result.scalars().all())
    total_count = int(total or 0)

    return ListResponse(
        data=[
            {
                "id": str(report.id),
                "kind": report.kind,
                "message": report.message,
                "page": report.page,
                "context": report.context,
                "status": report.status,
                "reporter_email": report.reporter_email,
                "organization_id": (
                    str(report.organization_id) if report.organization_id else None
                ),
                "created_at": report.created_at.isoformat(),
            }
            for report in reports
        ],
        meta=PageMeta(
            page=paging.page,
            page_size=paging.page_size,
            total=total_count,
            total_pages=(total_count + paging.page_size - 1) // paging.page_size,
            has_next=paging.offset + len(reports) < total_count,
        ),
    )


@router.post(
    "/feedback/{report_id}/status",
    response_model=DataResponse[dict],
    summary="Triage a feedback report",
)
async def set_feedback_status(
    report_id: uuid.UUID,
    payload: FeedbackStatusUpdate,
    admin: AdminUser,
    session: DbSession,
) -> DataResponse[dict]:
    report = await session.get(FeedbackReport, report_id)
    if report is None:
        raise NotFoundError("Feedback report not found.")

    report.status = payload.status
    report.reviewed_at = utcnow() if payload.status != "new" else None
    report.reviewed_by_user_id = admin.id if payload.status != "new" else None
    await session.commit()

    return DataResponse(data={"id": str(report.id), "status": report.status})


__all__ = ["router"]
