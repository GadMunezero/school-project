"""User profile, data export and account deletion.

Export and deletion exist because users are entitled to them, and both are implemented rather
than promised: the export assembles real rows, and deletion is a real, irreversible removal
scheduled through the retention job.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Response

from tradeloom.api.deps import (
    AuthServiceDep,
    CurrentPrincipal,
    DbSession,
    ReqContext,
    Tenant,
)
from tradeloom.core.enums import AuditAction
from tradeloom.core.errors import AuthenticationError
from tradeloom.core.security import verify_password
from tradeloom.core.timeutil import utcnow
from tradeloom.schemas.auth import AccountDeletionRequest, UpdateProfileRequest, UserProfile
from tradeloom.schemas.common import DataResponse, MessageResponse
from tradeloom.services.audit import AuditService
from tradeloom.services.export import ExportService

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=DataResponse[UserProfile], summary="Current user")
async def me(principal: CurrentPrincipal, session: DbSession) -> DataResponse[UserProfile]:
    user = principal.user
    await session.commit()
    return DataResponse(
        data=UserProfile.model_validate(
            {
                **{
                    field: getattr(user, field)
                    for field in (
                        "id",
                        "email",
                        "full_name",
                        "display_name",
                        "role",
                        "status",
                        "timezone",
                        "locale",
                        "theme",
                        "preferences",
                        "created_at",
                        "last_login_at",
                    )
                },
                "email_verified": user.email_verified,
            }
        )
    )


@router.patch("/me", response_model=DataResponse[UserProfile], summary="Update your profile")
async def update_me(
    payload: UpdateProfileRequest, principal: CurrentPrincipal, session: DbSession
) -> DataResponse[UserProfile]:
    user = principal.user
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await session.commit()
    return DataResponse(
        data=UserProfile.model_validate(
            {
                **{
                    field: getattr(user, field)
                    for field in (
                        "id",
                        "email",
                        "full_name",
                        "display_name",
                        "role",
                        "status",
                        "timezone",
                        "locale",
                        "theme",
                        "preferences",
                        "created_at",
                        "last_login_at",
                    )
                },
                "email_verified": user.email_verified,
            }
        )
    )


@router.get("/me/export", summary="Export everything in this workspace as JSON")
async def export_workspace(tenant: Tenant) -> Response:
    payload: dict[str, Any] = await ExportService(
        tenant.session, tenant.organization_id
    ).full_export()
    await AuditService(tenant.session).record(
        AuditAction.EXPORT_REQUESTED,
        organization_id=tenant.organization_id,
        actor_user_id=tenant.user_id,
        summary="Full workspace export",
    )
    await tenant.session.commit()

    body = json.dumps(payload, indent=2, default=str)
    filename = f"tradeloom-export-{utcnow().date().isoformat()}.json"
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/me/delete",
    response_model=MessageResponse,
    summary="Request permanent deletion of your account",
)
async def request_deletion(
    payload: AccountDeletionRequest,
    principal: CurrentPrincipal,
    session: DbSession,
    auth: AuthServiceDep,
    context: ReqContext,
) -> MessageResponse:
    user = principal.user
    if not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise AuthenticationError("Your password is incorrect.")

    user.deletion_requested_at = utcnow()
    await auth.revoke_all_sessions(user.id, reason="deletion_requested")
    await AuditService(session).record(
        AuditAction.ACCOUNT_DELETION_REQUESTED,
        actor_user_id=user.id,
        actor_email=user.email,
        entity_type="user",
        entity_id=user.id,
        summary="Account deletion requested",
        ip_address=context.ip_address,
    )
    await session.commit()
    return MessageResponse(
        message=(
            "Your account is scheduled for deletion. All workspaces you solely own and their "
            "trading data will be permanently removed within 7 days. Sign in again before then "
            "to cancel."
        )
    )


@router.post("/me/delete/cancel", response_model=MessageResponse, summary="Cancel deletion")
async def cancel_deletion(principal: CurrentPrincipal, session: DbSession) -> MessageResponse:
    principal.user.deletion_requested_at = None
    await session.commit()
    return MessageResponse(message="Account deletion cancelled.")


__all__ = ["router"]
