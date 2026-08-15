"""Authentication endpoints.

Handlers here do three things and nothing else: parse input, call :class:`AuthService`, and set
or clear cookies. Every rule (lockout, token lifetime, session rotation, workspace membership)
lives in the service so the same rules apply to any future non-HTTP caller.
"""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from tradeloom.api.cookies import clear_session_cookies, set_session_cookies
from tradeloom.api.deps import (
    AppSettings,
    AuthServiceDep,
    CurrentPrincipal,
    DbSession,
    Entitlements,
    LoginRateLimit,
    OptionalPrincipal,
    ReqContext,
    SignupRateLimit,
)
from tradeloom.core.errors import NotFoundError
from tradeloom.core.timeutil import utcnow
from tradeloom.schemas.auth import (
    ActiveSessionInfo,
    EmailVerificationRequest,
    LoginRequest,
    OrganizationSummary,
    PasswordChangeRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    SessionInfo,
    SignupRequest,
    SwitchOrganizationRequest,
    UserProfile,
)
from tradeloom.schemas.common import DataResponse, MessageResponse
from tradeloom.services.auth import AuthService
from tradeloom.services.entitlements import EntitlementService

router = APIRouter(prefix="/auth", tags=["auth"])


async def build_session_info(
    auth: AuthService,
    entitlements: EntitlementService,
    principal: CurrentPrincipal,
) -> SessionInfo:
    """Assemble everything the client shell needs, all of it server-derived."""
    memberships = await auth.list_memberships(principal.user_id)
    active_id = principal.session_record.active_organization_id

    summaries: list[OrganizationSummary] = []
    active_summary: OrganizationSummary | None = None
    for member, organization in memberships:
        plan = await entitlements.resolve_plan(organization.id)
        summary = OrganizationSummary(
            id=organization.id,
            name=organization.name,
            slug=organization.slug,
            role=member.role,
            is_personal=organization.is_personal,
            base_currency=organization.base_currency,
            timezone=organization.timezone,
            plan=plan,
        )
        summaries.append(summary)
        if organization.id == active_id:
            active_summary = summary

    entitlement_snapshot: dict = {}
    if active_summary is not None:
        entitlement_snapshot = await entitlements.snapshot(active_summary.id)

    return SessionInfo(
        user=UserProfile.model_validate(
            {
                **{
                    field: getattr(principal.user, field)
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
                "email_verified": principal.user.email_verified,
            }
        ),
        active_organization=active_summary,
        organizations=summaries,
        entitlements=entitlement_snapshot,
        csrf_token=principal.session_record.csrf_token,
        expires_at=principal.session_record.expires_at,
    )


@router.get(
    "/signup-policy",
    response_model=DataResponse[dict],
    summary="Whether this deployment accepts open registration",
)
async def signup_policy(settings: AppSettings) -> DataResponse[dict]:
    """Lets the signup form ask for a code only when one is needed.

    Public by necessity — it is read before anyone has an account. It reveals only whether the
    door is open, which is apparent from trying to register anyway, and the answer is advisory:
    the server enforces the gate whatever the form decides to show.
    """
    return DataResponse(data={"invite_required": settings.signup_is_invite_only})


@router.post(
    "/signup",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[SessionInfo],
    dependencies=[SignupRateLimit],
    summary="Create an account and its personal workspace",
)
async def signup(
    payload: SignupRequest,
    response: Response,
    session: DbSession,
    auth: AuthServiceDep,
    entitlements: Entitlements,
    settings: AppSettings,
    context: ReqContext,
) -> DataResponse[SessionInfo]:
    user = await auth.signup(payload, context)
    issued = await auth.create_session(user, context)
    await session.commit()

    set_session_cookies(response, issued, settings)
    from tradeloom.api.deps import Principal

    principal = Principal(user=user, session_record=issued.session)
    return DataResponse(data=await build_session_info(auth, entitlements, principal))


@router.post(
    "/login",
    response_model=DataResponse[SessionInfo],
    dependencies=[LoginRateLimit],
    summary="Sign in",
)
async def login(
    payload: LoginRequest,
    response: Response,
    session: DbSession,
    auth: AuthServiceDep,
    entitlements: Entitlements,
    settings: AppSettings,
    context: ReqContext,
) -> DataResponse[SessionInfo]:
    user = await auth.authenticate(payload, context)
    # A fresh session id on every sign-in defeats session fixation.
    issued = await auth.create_session(user, context)
    await session.commit()

    set_session_cookies(response, issued, settings)
    from tradeloom.api.deps import Principal

    principal = Principal(user=user, session_record=issued.session)
    return DataResponse(data=await build_session_info(auth, entitlements, principal))


@router.post("/logout", response_model=MessageResponse, summary="Sign out of this session")
async def logout(
    response: Response,
    session: DbSession,
    auth: AuthServiceDep,
    settings: AppSettings,
    principal: OptionalPrincipal,
) -> MessageResponse:
    if principal is not None:
        await auth.revoke_session(principal.session_id, reason="logout")
        await session.commit()
    clear_session_cookies(response, settings)
    return MessageResponse(message="Signed out.")


@router.get("/session", response_model=DataResponse[SessionInfo], summary="Current session")
async def read_session(
    principal: CurrentPrincipal,
    session: DbSession,
    auth: AuthServiceDep,
    entitlements: Entitlements,
) -> DataResponse[SessionInfo]:
    info = await build_session_info(auth, entitlements, principal)
    await session.commit()  # persists the session touch/rotation performed by the dependency
    return DataResponse(data=info)


@router.post(
    "/switch-organization",
    response_model=DataResponse[SessionInfo],
    summary="Change the active workspace",
)
async def switch_organization(
    payload: SwitchOrganizationRequest,
    principal: CurrentPrincipal,
    session: DbSession,
    auth: AuthServiceDep,
    entitlements: Entitlements,
) -> DataResponse[SessionInfo]:
    await auth.switch_organization(principal.session_record, payload.organization_id)
    info = await build_session_info(auth, entitlements, principal)
    await session.commit()
    return DataResponse(data=info)


@router.post("/verify-email", response_model=MessageResponse, summary="Confirm an email address")
async def verify_email(
    payload: EmailVerificationRequest,
    session: DbSession,
    auth: AuthServiceDep,
    context: ReqContext,
) -> MessageResponse:
    await auth.verify_email(payload.token, context)
    await session.commit()
    return MessageResponse(message="Email address confirmed.")


@router.post(
    "/resend-verification",
    response_model=MessageResponse,
    summary="Send a new verification email",
)
async def resend_verification(
    principal: CurrentPrincipal,
    session: DbSession,
    auth: AuthServiceDep,
    context: ReqContext,
) -> MessageResponse:
    await auth.resend_verification(principal.user, context)
    await session.commit()
    return MessageResponse(message="Verification email sent.")


@router.post(
    "/password-reset",
    response_model=MessageResponse,
    dependencies=[LoginRateLimit],
    summary="Request a password reset link",
)
async def request_password_reset(
    payload: PasswordResetRequest,
    session: DbSession,
    auth: AuthServiceDep,
    context: ReqContext,
) -> MessageResponse:
    await auth.request_password_reset(payload.email, context)
    await session.commit()
    # Deliberately identical whether or not the address exists.
    return MessageResponse(
        message="If an account exists for that address, a reset link is on its way."
    )


@router.post(
    "/password-reset/confirm",
    response_model=MessageResponse,
    dependencies=[LoginRateLimit],
    summary="Set a new password using a reset token",
)
async def confirm_password_reset(
    payload: PasswordResetConfirm,
    response: Response,
    session: DbSession,
    auth: AuthServiceDep,
    settings: AppSettings,
    context: ReqContext,
) -> MessageResponse:
    await auth.reset_password(payload.token, payload.new_password, context)
    await session.commit()
    clear_session_cookies(response, settings)
    return MessageResponse(message="Password updated. Sign in with your new password.")


@router.post("/password", response_model=MessageResponse, summary="Change password")
async def change_password(
    payload: PasswordChangeRequest,
    principal: CurrentPrincipal,
    session: DbSession,
    auth: AuthServiceDep,
    context: ReqContext,
) -> MessageResponse:
    await auth.change_password(
        principal.user,
        payload.current_password,
        payload.new_password,
        current_session_id=principal.session_id,
        context=context,
    )
    await session.commit()
    return MessageResponse(message="Password changed. Other sessions were signed out.")


@router.get(
    "/sessions",
    response_model=DataResponse[list[ActiveSessionInfo]],
    summary="List active sessions",
)
async def list_sessions(
    principal: CurrentPrincipal, auth: AuthServiceDep, session: DbSession
) -> DataResponse[list[ActiveSessionInfo]]:
    records = await auth.list_sessions(principal.user_id)
    await session.commit()
    return DataResponse(
        data=[
            ActiveSessionInfo(
                id=record.id,
                ip_address=record.ip_address,
                user_agent=record.user_agent,
                created_at=record.created_at,
                last_seen_at=record.last_seen_at,
                expires_at=record.expires_at,
                is_current=record.id == principal.session_id,
            )
            for record in records
        ]
    )


@router.delete("/sessions/{session_id}", response_model=MessageResponse, summary="Revoke a session")
async def revoke_session(
    session_id: str,
    principal: CurrentPrincipal,
    auth: AuthServiceDep,
    session: DbSession,
) -> MessageResponse:
    import uuid as uuid_module

    try:
        target_id = uuid_module.UUID(session_id)
    except ValueError as exc:
        raise NotFoundError("Session not found.") from exc

    owned = [record.id for record in await auth.list_sessions(principal.user_id)]
    if target_id not in owned:
        # Never confirm the existence of another user's session.
        raise NotFoundError("Session not found.")

    await auth.revoke_session(target_id, reason="revoked_by_user")
    await session.commit()
    return MessageResponse(message="Session revoked.")


@router.post(
    "/sessions/revoke-all",
    response_model=MessageResponse,
    summary="Sign out everywhere else",
)
async def revoke_other_sessions(
    principal: CurrentPrincipal, auth: AuthServiceDep, session: DbSession
) -> MessageResponse:
    count = await auth.revoke_all_sessions(
        principal.user_id, except_session_id=principal.session_id, reason="revoke_all"
    )
    await session.commit()
    return MessageResponse(
        message=f"Signed out of {count} other session{'s' if count != 1 else ''}.",
        data={"revoked": count, "at": utcnow().isoformat()},
    )


__all__ = ["build_session_info", "router"]
