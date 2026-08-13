"""FastAPI dependencies.

This module is the *only* place that turns a cookie into an identity, and the only place that
decides which organization a request may touch. Route handlers receive an already-authorised
:class:`TenantContext`; they never read an ``organization_id`` from the request body or query
string, which is what makes cross-tenant access structurally impossible rather than merely
unlikely.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core.config import Settings, get_settings
from tradeloom.core.enums import MemberRole
from tradeloom.core.errors import (
    AuthenticationError,
    CsrfError,
    EmailNotVerifiedError,
    ForbiddenError,
    RateLimitedError,
    SessionExpiredError,
)
from tradeloom.core.logging import org_id_ctx, user_id_ctx
from tradeloom.core.pagination import CursorParams, PageParams
from tradeloom.core.ratelimit import RateLimit, get_rate_limiter
from tradeloom.core.security import constant_time_equals
from tradeloom.db.session import get_db_session
from tradeloom.models.identity import User, UserSession
from tradeloom.models.organization import Organization, OrganizationMember
from tradeloom.services.auth import AuthService, RequestContext
from tradeloom.services.entitlements import EntitlementService, PlanLimits

#: Methods that require a valid CSRF token when authenticated by cookie.
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CSRF_HEADER = "X-CSRF-Token"


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async for session in get_db_session():
        yield session


DbSession = Annotated[AsyncSession, Depends(get_session)]


def get_app_settings() -> Settings:
    return get_settings()


AppSettings = Annotated[Settings, Depends(get_app_settings)]


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


def request_context(request: Request) -> RequestContext:
    return RequestContext(
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:320] or None,
    )


ReqContext = Annotated[RequestContext, Depends(request_context)]


def get_auth_service(session: DbSession) -> AuthService:
    return AuthService(session)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Principal:
    """An authenticated user plus the session that authenticated them."""

    user: User
    session_record: UserSession

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    @property
    def session_id(self) -> uuid.UUID:
        return self.session_record.id


async def _load_principal(
    request: Request,
    response: Response,
    auth: AuthService,
    settings: Settings,
) -> Principal | None:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        return None

    record = await auth.resolve_session(token)
    if record is None:
        return None

    user = await auth.get_user(record.user_id)
    if user is None or not user.is_active:
        await auth.revoke_session(record.id, reason="user_inactive")
        return None

    if request.method in UNSAFE_METHODS:
        supplied = request.headers.get(CSRF_HEADER, "")
        if not supplied or not constant_time_equals(supplied, record.csrf_token):
            raise CsrfError()

    rotated = await auth.touch_session(record)
    if rotated is not None:
        from tradeloom.api.cookies import set_session_cookies

        set_session_cookies(response, rotated, settings)

    user_id_ctx.set(str(user.id))
    if record.active_organization_id:
        org_id_ctx.set(str(record.active_organization_id))
    return Principal(user=user, session_record=record)


async def optional_principal(
    request: Request,
    response: Response,
    auth: AuthServiceDep,
    settings: AppSettings,
) -> Principal | None:
    return await _load_principal(request, response, auth, settings)


OptionalPrincipal = Annotated["Principal | None", Depends(optional_principal)]


async def current_principal(
    principal: OptionalPrincipal,
) -> Principal:
    if principal is None:
        raise SessionExpiredError()
    return principal


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


async def current_user(principal: CurrentPrincipal) -> User:
    return principal.user


CurrentUser = Annotated[User, Depends(current_user)]


async def verified_user(principal: CurrentPrincipal) -> User:
    """For actions that should not be available until the email is confirmed."""
    if not principal.user.email_verified:
        raise EmailNotVerifiedError()
    return principal.user


VerifiedUser = Annotated[User, Depends(verified_user)]


async def admin_user(principal: CurrentPrincipal) -> User:
    """Platform admin check — evaluated server-side on every admin request."""
    if not principal.user.is_admin:
        raise ForbiddenError("Administrator access is required.")
    return principal.user


AdminUser = Annotated[User, Depends(admin_user)]


# --------------------------------------------------------------------------
# Tenancy
# --------------------------------------------------------------------------


@dataclass(slots=True)
class TenantContext:
    """Everything a service needs to act on behalf of a user inside one workspace."""

    session: AsyncSession
    user: User
    session_record: UserSession
    organization: Organization
    membership: OrganizationMember
    request: RequestContext

    @property
    def organization_id(self) -> uuid.UUID:
        return self.organization.id

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    @property
    def role(self) -> MemberRole:
        return self.membership.role

    def require_role(self, minimum: MemberRole) -> None:
        if not self.role.at_least(minimum):
            raise ForbiddenError(
                f"This action requires the {minimum.value} role in this workspace."
            )

    @property
    def can_write(self) -> bool:
        return self.role.at_least(MemberRole.MEMBER)

    def require_write(self) -> None:
        self.require_role(MemberRole.MEMBER)


async def tenant_context(
    principal: CurrentPrincipal,
    session: DbSession,
    auth: AuthServiceDep,
    context: ReqContext,
) -> TenantContext:
    """Resolve the active workspace from the *session record*, never from client input.

    A client can ask to switch workspace through a dedicated endpoint, which verifies membership
    and rewrites the session. Ordinary requests cannot select a tenant at all.
    """
    organization_id = principal.session_record.active_organization_id
    if organization_id is None:
        organization_id = await auth.default_organization_id(principal.user_id)
        if organization_id is None:
            raise ForbiddenError("Your account is not a member of any workspace.")
        principal.session_record.active_organization_id = organization_id
        await session.flush()

    membership = await auth.get_membership(principal.user_id, organization_id)
    if membership is None:
        # Membership was revoked while the session was alive.
        principal.session_record.active_organization_id = None
        await session.flush()
        raise ForbiddenError("You no longer have access to this workspace.")

    organization = await session.get(Organization, organization_id)
    if organization is None or organization.deleted_at is not None:
        raise ForbiddenError("This workspace is no longer available.")

    org_id_ctx.set(str(organization.id))
    return TenantContext(
        session=session,
        user=principal.user,
        session_record=principal.session_record,
        organization=organization,
        membership=membership,
        request=context,
    )


Tenant = Annotated[TenantContext, Depends(tenant_context)]


async def writable_tenant(tenant: Tenant) -> TenantContext:
    tenant.require_write()
    return tenant


WritableTenant = Annotated[TenantContext, Depends(writable_tenant)]


async def manager_tenant(tenant: Tenant) -> TenantContext:
    tenant.require_role(MemberRole.MANAGER)
    return tenant


ManagerTenant = Annotated[TenantContext, Depends(manager_tenant)]


# --------------------------------------------------------------------------
# Entitlements
# --------------------------------------------------------------------------


def get_entitlements(session: DbSession) -> EntitlementService:
    return EntitlementService(session)


Entitlements = Annotated[EntitlementService, Depends(get_entitlements)]


async def current_limits(tenant: Tenant, entitlements: Entitlements) -> PlanLimits:
    return await entitlements.limits_for(tenant.organization_id)


CurrentLimits = Annotated[PlanLimits, Depends(current_limits)]


# --------------------------------------------------------------------------
# Pagination
# --------------------------------------------------------------------------


def page_params(
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200, alias="page_size")] = 25,
    sort_by: Annotated[str | None, Query(max_length=40)] = None,
    sort_dir: Annotated[str, Query(pattern="^(asc|desc)$")] = "desc",
) -> PageParams:
    return PageParams(page=page, page_size=page_size, sort_by=sort_by, sort_dir=sort_dir)


Paging = Annotated[PageParams, Depends(page_params)]


def cursor_params(
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> CursorParams:
    return CursorParams(cursor=cursor, limit=limit)


Cursoring = Annotated[CursorParams, Depends(cursor_params)]


# --------------------------------------------------------------------------
# Endpoint-level rate limiting
# --------------------------------------------------------------------------


def rate_limit(spec_name: str, bucket: str):  # type: ignore[no-untyped-def]
    """Build a dependency enforcing a named limit from settings.

    Keyed by client IP *and* bucket so a burst against ``/auth/login`` cannot exhaust the
    allowance for ``/auth/signup``.
    """

    async def _dependency(request: Request, settings: AppSettings) -> None:
        if not settings.rate_limit_enabled:
            return
        spec = getattr(settings, spec_name)
        limit = RateLimit.parse(spec)
        key = f"rl:{bucket}:{client_ip(request) or 'unknown'}"
        result = await get_rate_limiter().hit(key, limit)
        if not result.allowed:
            raise RateLimitedError(result.retry_after_seconds)

    return Depends(_dependency)


LoginRateLimit = rate_limit("rate_limit_login", "login")
SignupRateLimit = rate_limit("rate_limit_signup", "signup")


def require_authenticated_session(principal: CurrentPrincipal) -> Principal:
    if principal.session_record.revoked_at is not None:
        raise AuthenticationError()
    return principal


__all__ = [
    "CSRF_HEADER",
    "AdminUser",
    "AppSettings",
    "AuthServiceDep",
    "CurrentLimits",
    "CurrentPrincipal",
    "CurrentUser",
    "Cursoring",
    "DbSession",
    "Entitlements",
    "LoginRateLimit",
    "ManagerTenant",
    "OptionalPrincipal",
    "Paging",
    "Principal",
    "ReqContext",
    "SignupRateLimit",
    "Tenant",
    "TenantContext",
    "VerifiedUser",
    "WritableTenant",
    "client_ip",
    "rate_limit",
]
