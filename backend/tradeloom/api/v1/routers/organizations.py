"""Workspace (organization) endpoints.

Membership changes require the manager role or above, checked server-side. The active workspace
is switched through ``/auth/switch-organization``, which rewrites the session — nothing here lets
a caller act on a workspace by naming its id.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, status
from pydantic import EmailStr, Field
from sqlalchemy import select

from tradeloom.api.deps import ManagerTenant, Tenant
from tradeloom.core.enums import AuditAction, MemberRole, MemberStatus
from tradeloom.core.errors import ConflictError, ForbiddenError, NotFoundError
from tradeloom.core.timeutil import is_valid_timezone, utcnow
from tradeloom.models.identity import User
from tradeloom.models.organization import OrganizationMember
from tradeloom.schemas.common import DataResponse, MessageResponse, TradeloomModel
from tradeloom.services.audit import AuditService

router = APIRouter(prefix="/organizations", tags=["organizations"])


class OrganizationUpdate(TradeloomModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_currency: str | None = Field(default=None, min_length=3, max_length=8)
    timezone: str | None = None
    settings: dict[str, Any] | None = None


class MemberRead(TradeloomModel):
    id: Any
    user_id: Any
    email: str
    full_name: str | None
    role: MemberRole
    status: MemberStatus
    joined_at: Any | None
    created_at: Any


class MemberInvite(TradeloomModel):
    email: EmailStr
    role: MemberRole = MemberRole.MEMBER


class MemberRoleUpdate(TradeloomModel):
    role: MemberRole


class OrganizationRead(TradeloomModel):
    id: Any
    name: str
    slug: str
    is_personal: bool
    base_currency: str
    timezone: str
    settings: dict[str, Any]
    owner_user_id: Any
    created_at: Any
    member_count: int = 0
    your_role: MemberRole | None = None


@router.get("/current", response_model=DataResponse[OrganizationRead], summary="Active workspace")
async def current(tenant: Tenant) -> DataResponse[OrganizationRead]:
    count = len(await _members(tenant, tenant.organization_id))
    model = OrganizationRead.model_validate(tenant.organization)
    model.member_count = count
    model.your_role = tenant.role
    return DataResponse(data=model)


@router.patch(
    "/current", response_model=DataResponse[OrganizationRead], summary="Update the workspace"
)
async def update_current(
    payload: OrganizationUpdate, tenant: ManagerTenant
) -> DataResponse[OrganizationRead]:
    data = payload.model_dump(exclude_unset=True)
    if "timezone" in data and data["timezone"] and not is_valid_timezone(data["timezone"]):
        raise ConflictError("Unknown timezone.")
    if data.get("base_currency"):
        data["base_currency"] = data["base_currency"].upper()

    before = tenant.organization.to_dict()
    for field, value in data.items():
        setattr(tenant.organization, field, value)

    from tradeloom.services.audit import diff_changes

    await AuditService(tenant.session).record(
        AuditAction.UPDATED,
        organization_id=tenant.organization_id,
        actor_user_id=tenant.user_id,
        entity_type="organization",
        entity_id=tenant.organization_id,
        changes=diff_changes(before, tenant.organization.to_dict()),
    )
    await tenant.session.commit()

    model = OrganizationRead.model_validate(tenant.organization)
    model.your_role = tenant.role
    return DataResponse(data=model)


async def _members(tenant: Tenant, organization_id: uuid.UUID) -> list[tuple]:
    result = await tenant.session.execute(
        select(OrganizationMember, User)
        .join(User, User.id == OrganizationMember.user_id)
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.status != MemberStatus.REMOVED,
        )
        .order_by(OrganizationMember.created_at.asc())
    )
    return list(result.all())


@router.get(
    "/current/members", response_model=DataResponse[list[MemberRead]], summary="List members"
)
async def list_members(tenant: Tenant) -> DataResponse[list[MemberRead]]:
    rows = await _members(tenant, tenant.organization_id)
    return DataResponse(
        data=[
            MemberRead(
                id=member.id,
                user_id=user.id,
                email=user.email,
                full_name=user.full_name,
                role=member.role,
                status=member.status,
                joined_at=member.joined_at,
                created_at=member.created_at,
            )
            for member, user in rows
        ]
    )


@router.post(
    "/current/members",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[MemberRead],
    summary="Add an existing user to the workspace",
)
async def add_member(payload: MemberInvite, tenant: ManagerTenant) -> DataResponse[MemberRead]:
    """Adds a user who already has a Tradeloom account.

    Email invitations for people without an account are not implemented; rather than pretend
    otherwise, this endpoint tells the caller plainly when the address is unknown.
    """
    if payload.role is MemberRole.OWNER:
        raise ForbiddenError("Transfer ownership from the workspace settings instead.")

    email = payload.email.strip().lower()
    result = await tenant.session.execute(
        select(User).where(User.email == email, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError(
            "No Tradeloom account exists for that email address. Ask them to sign up first, "
            "then add them here."
        )

    existing = await tenant.session.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == tenant.organization_id,
            OrganizationMember.user_id == user.id,
        )
    )
    member = existing.scalar_one_or_none()
    if member is not None and member.status is MemberStatus.ACTIVE:
        raise ConflictError("That user is already a member of this workspace.")

    if member is None:
        member = OrganizationMember(
            organization_id=tenant.organization_id,
            user_id=user.id,
            invited_by_user_id=tenant.user_id,
            invited_at=utcnow(),
        )
        tenant.session.add(member)
    member.role = payload.role
    member.status = MemberStatus.ACTIVE
    member.joined_at = utcnow()

    await AuditService(tenant.session).record(
        AuditAction.CREATED,
        organization_id=tenant.organization_id,
        actor_user_id=tenant.user_id,
        entity_type="organization_member",
        summary=f"Added {email} as {payload.role.value}",
    )
    await tenant.session.commit()
    return DataResponse(
        data=MemberRead(
            id=member.id,
            user_id=user.id,
            email=user.email,
            full_name=user.full_name,
            role=member.role,
            status=member.status,
            joined_at=member.joined_at,
            created_at=member.created_at,
        )
    )


@router.patch(
    "/current/members/{member_id}",
    response_model=MessageResponse,
    summary="Change a member's role",
)
async def update_member_role(
    member_id: uuid.UUID, payload: MemberRoleUpdate, tenant: ManagerTenant
) -> MessageResponse:
    member = await _get_member(tenant, member_id)
    if member.user_id == tenant.organization.owner_user_id:
        raise ForbiddenError("The workspace owner's role cannot be changed.")
    if payload.role is MemberRole.OWNER:
        raise ForbiddenError("Transfer ownership from the workspace settings instead.")

    member.role = payload.role
    await AuditService(tenant.session).record(
        AuditAction.UPDATED,
        organization_id=tenant.organization_id,
        actor_user_id=tenant.user_id,
        entity_type="organization_member",
        entity_id=member.id,
        summary=f"Role changed to {payload.role.value}",
    )
    await tenant.session.commit()
    return MessageResponse(message="Member role updated.")


@router.delete(
    "/current/members/{member_id}", response_model=MessageResponse, summary="Remove a member"
)
async def remove_member(member_id: uuid.UUID, tenant: ManagerTenant) -> MessageResponse:
    member = await _get_member(tenant, member_id)
    if member.user_id == tenant.organization.owner_user_id:
        raise ForbiddenError("The workspace owner cannot be removed.")

    member.status = MemberStatus.REMOVED
    await AuditService(tenant.session).record(
        AuditAction.DELETED,
        organization_id=tenant.organization_id,
        actor_user_id=tenant.user_id,
        entity_type="organization_member",
        entity_id=member.id,
        summary="Member removed",
    )
    await tenant.session.commit()
    return MessageResponse(message="Member removed from this workspace.")


async def _get_member(tenant: Tenant, member_id: uuid.UUID) -> OrganizationMember:
    result = await tenant.session.execute(
        select(OrganizationMember).where(
            OrganizationMember.id == member_id,
            OrganizationMember.organization_id == tenant.organization_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise NotFoundError("Member not found.")
    return member


__all__ = ["router"]
