"""Authentication, sessions and workspace bootstrap.

Session design (see ``docs/SECURITY.md``):

* The browser holds an opaque 256-bit token in an HTTP-only, SameSite=Lax cookie. The server
  stores only its SHA-256 digest.
* Sessions have three clocks: an absolute expiry, an idle timeout, and a rotation interval. On
  every authenticated request the session is touched; past the rotation interval the token is
  regenerated and the cookie replaced, which limits the value of a stolen cookie.
* Signing in rotates the session id (no fixation), and changing a password revokes every other
  session.
* Failed sign-ins are counted per user and per IP; past the threshold the account is locked for a
  cooling-off period. The response is identical whether the account exists, is locked, or the
  password was simply wrong.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core import security
from tradeloom.core.config import get_settings
from tradeloom.core.enums import (
    AuditAction,
    MemberRole,
    MemberStatus,
    SubscriptionPlan,
    SubscriptionStatus,
    UserRole,
    UserStatus,
)
from tradeloom.core.errors import (
    AuthenticationError,
    ConflictError,
    ForbiddenError,
    InvalidCredentialsError,
    NotFoundError,
    RateLimitedError,
    ValidationError,
)
from tradeloom.core.logging import get_logger
from tradeloom.core.timeutil import utcnow
from tradeloom.models.identity import EmailToken, LoginAttempt, User, UserSession
from tradeloom.models.organization import Organization, OrganizationMember
from tradeloom.models.platform import Subscription
from tradeloom.schemas.auth import LoginRequest, SignupRequest
from tradeloom.services.audit import AuditService
from tradeloom.services.email import EmailService

logger = get_logger(__name__)

PURPOSE_VERIFY_EMAIL = "verify_email"
PURPOSE_RESET_PASSWORD = "reset_password"

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def slugify(value: str) -> str:
    slug = _SLUG_STRIP.sub("-", value.strip().lower()).strip("-")
    return slug[:100] or "workspace"


@dataclass(slots=True)
class IssuedSession:
    """What the API layer needs to set cookies. The plaintext token exists only here and in the
    response cookie — it is never returned in a JSON body or written to a log."""

    session: UserSession
    token: str
    csrf_token: str


@dataclass(slots=True)
class RequestContext:
    ip_address: str | None = None
    user_agent: str | None = None


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        email_service: EmailService | None = None,
        audit: AuditService | None = None,
    ) -> None:
        self.session = session
        self.settings = get_settings()
        self.email_service = email_service or EmailService()
        self.audit = audit or AuditService(session)

    # -- lookups -------------------------------------------------------------

    async def get_user_by_email(self, email: str) -> User | None:
        result = await self.session.execute(
            select(User).where(User.email == normalize_email(email), User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def get_user(self, user_id: uuid.UUID) -> User | None:
        result = await self.session.execute(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    # -- signup --------------------------------------------------------------

    async def signup(self, payload: SignupRequest, context: RequestContext) -> User:
        email = normalize_email(payload.email)

        # The invite is claimed before anything is created. Checking it afterwards would leave a
        # user row behind whenever the code turned out to be spent.
        invite = None
        if get_settings().signup_is_invite_only:
            from tradeloom.services.invites import InviteService

            invite = await InviteService(self.session).redeem(
                payload.invite_code or "", email=email
            )

        existing = await self.get_user_by_email(email)
        if existing is not None:
            # Registration is not an enumeration oracle in the UI copy, but returning 409 here is
            # unavoidable for a usable signup form. Login and password reset stay non-enumerable.
            raise ConflictError("An account with that email already exists.")

        user = User(
            email=email,
            password_hash=security.hash_password(payload.password),
            full_name=payload.full_name.strip(),
            display_name=payload.full_name.strip().split(" ")[0][:80],
            role=UserRole.USER,
            status=UserStatus.ACTIVE,
            timezone=payload.timezone,
            password_changed_at=utcnow(),
        )
        self.session.add(user)
        await self.session.flush()

        organization = await self.create_personal_organization(
            user, payload.organization_name, timezone=payload.timezone
        )

        if invite is not None:
            from tradeloom.services.invites import InviteService

            await InviteService(self.session).attach_user(invite, user.id, email)

        # What they agreed to, and when. A boolean on the user could not answer that question
        # once the text changed, which is the only time anyone asks it.
        from tradeloom.core.legal import VERSIONS
        from tradeloom.models.platform import PolicyAcceptance

        accepted_at = utcnow()
        for document, version in VERSIONS.items():
            self.session.add(
                PolicyAcceptance(
                    user_id=user.id,
                    document=document,
                    version=version,
                    accepted_at=accepted_at,
                    ip_address=context.ip_address,
                    user_agent=context.user_agent,
                )
            )
        await self.session.flush()

        token = await self.issue_email_token(user, PURPOSE_VERIFY_EMAIL, context)
        self.email_service.send_verification(user.email, user.display_name, token)

        await self.audit.record(
            AuditAction.SIGNUP,
            organization_id=organization.id,
            actor_user_id=user.id,
            actor_email=user.email,
            entity_type="user",
            entity_id=user.id,
            summary="Account created",
            ip_address=context.ip_address,
            user_agent=context.user_agent,
        )
        logger.info("user_signed_up", user_id=str(user.id))
        return user

    async def create_personal_organization(
        self, user: User, name: str | None, *, timezone: str = "UTC"
    ) -> Organization:
        base_name = (name or f"{(user.display_name or 'My')} workspace").strip()[:120]
        slug = await self._unique_slug(slugify(base_name))
        organization = Organization(
            name=base_name,
            slug=slug,
            owner_user_id=user.id,
            is_personal=True,
            timezone=timezone,
        )
        self.session.add(organization)
        await self.session.flush()

        self.session.add(
            OrganizationMember(
                organization_id=organization.id,
                user_id=user.id,
                role=MemberRole.OWNER,
                status=MemberStatus.ACTIVE,
                joined_at=utcnow(),
            )
        )
        # Every workspace gets an explicit Free subscription row so entitlement resolution has a
        # single code path and billing never has to special-case "no row yet".
        self.session.add(
            Subscription(
                organization_id=organization.id,
                plan=SubscriptionPlan.FREE,
                status=SubscriptionStatus.ACTIVE,
            )
        )
        await self.session.flush()
        return organization

    async def _unique_slug(self, base: str) -> str:
        candidate = base
        for suffix in range(0, 50):
            if suffix:
                candidate = f"{base}-{suffix}"
            exists = await self.session.scalar(
                select(func.count()).select_from(Organization).where(Organization.slug == candidate)
            )
            if not exists:
                return candidate
        return f"{base}-{uuid.uuid4().hex[:8]}"

    # -- login ---------------------------------------------------------------

    async def authenticate(self, payload: LoginRequest, context: RequestContext) -> User:
        email = normalize_email(payload.email)
        user = await self.get_user_by_email(email)

        if user is None:
            security.dummy_verify()  # equalise timing so absent accounts are indistinguishable
            await self._record_attempt(email, context, succeeded=False, reason="unknown_user")
            await self._persist_security_record()
            raise InvalidCredentialsError()

        if user.locked_until and user.locked_until > utcnow():
            retry_after = int((user.locked_until - utcnow()).total_seconds())
            await self._record_attempt(email, context, succeeded=False, reason="locked")
            await self._persist_security_record()
            raise RateLimitedError(
                retry_after,
                "Too many failed sign-in attempts. Try again shortly or reset your password.",
            )

        if not user.password_hash or not security.verify_password(
            payload.password, user.password_hash
        ):
            await self._register_failure(user, email, context)
            raise InvalidCredentialsError()

        if user.status is UserStatus.SUSPENDED:
            await self._record_attempt(email, context, succeeded=False, reason="suspended")
            raise ForbiddenError("This account has been suspended. Contact support.")
        if user.status is UserStatus.DELETED or user.deleted_at is not None:
            await self._record_attempt(email, context, succeeded=False, reason="deleted")
            raise InvalidCredentialsError()

        if security.needs_rehash(user.password_hash):
            user.password_hash = security.hash_password(payload.password)

        user.failed_login_count = 0
        user.locked_until = None
        user.last_login_at = utcnow()
        await self.session.flush()

        await self._record_attempt(email, context, succeeded=True)
        await self.audit.record(
            AuditAction.LOGIN_SUCCEEDED,
            actor_user_id=user.id,
            actor_email=user.email,
            entity_type="user",
            entity_id=user.id,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
        )
        return user

    async def _register_failure(self, user: User, email: str, context: RequestContext) -> None:
        user.failed_login_count += 1
        if user.failed_login_count >= self.settings.login_lockout_threshold:
            user.locked_until = utcnow() + timedelta(seconds=self.settings.login_lockout_seconds)
            user.failed_login_count = 0
            logger.warning("account_locked", user_id=str(user.id))
        await self.session.flush()
        await self._record_attempt(email, context, succeeded=False, reason="bad_password")
        await self.audit.record(
            AuditAction.LOGIN_FAILED,
            actor_user_id=user.id,
            actor_email=email,
            entity_type="user",
            entity_id=user.id,
            ip_address=context.ip_address,
            user_agent=context.user_agent,
        )
        await self._persist_security_record()

    async def _persist_security_record(self) -> None:
        """Commit failed-login bookkeeping before an authentication error propagates.

        Services normally leave committing to the caller, but a failed sign-in raises, and the
        request-scoped session rolls back on the way out — which would discard the very counters
        the lockout depends on. Persisting here is what makes throttling actually work.
        """
        await self.session.commit()

    async def _record_attempt(
        self, email: str, context: RequestContext, *, succeeded: bool, reason: str | None = None
    ) -> None:
        self.session.add(
            LoginAttempt(
                email=email,
                ip_address=context.ip_address,
                succeeded=succeeded,
                reason=reason,
            )
        )
        await self.session.flush()

    # -- sessions ------------------------------------------------------------

    async def create_session(
        self, user: User, context: RequestContext, *, organization_id: uuid.UUID | None = None
    ) -> IssuedSession:
        token = security.generate_token()
        csrf_token = security.generate_token()
        now = utcnow()

        active_org = organization_id or await self.default_organization_id(user.id)
        record = UserSession(
            user_id=user.id,
            token_hash=security.hash_token(token),
            csrf_token=csrf_token,
            active_organization_id=active_org,
            ip_address=context.ip_address,
            user_agent=(context.user_agent or "")[:320] or None,
            expires_at=now + timedelta(seconds=self.settings.session_ttl_seconds),
            last_seen_at=now,
            rotated_at=now,
        )
        self.session.add(record)
        await self.session.flush()
        return IssuedSession(session=record, token=token, csrf_token=csrf_token)

    async def resolve_session(self, token: str) -> UserSession | None:
        """Validate a cookie token. Returns ``None`` for anything unusable."""
        if not token:
            return None
        result = await self.session.execute(
            select(UserSession).where(UserSession.token_hash == security.hash_token(token))
        )
        record = result.scalar_one_or_none()
        if record is None or record.revoked_at is not None:
            return None

        now = utcnow()
        if record.expires_at <= now:
            return None
        idle_limit = timedelta(seconds=self.settings.session_idle_timeout_seconds)
        if now - record.last_seen_at > idle_limit:
            record.revoked_at = now
            record.revoked_reason = "idle_timeout"
            await self.session.flush()
            return None
        return record

    async def touch_session(self, record: UserSession) -> IssuedSession | None:
        """Update last-seen and rotate the token when it is old enough.

        Returns a new :class:`IssuedSession` when the caller must replace the cookie, ``None``
        when the existing cookie remains valid.
        """
        now = utcnow()
        record.last_seen_at = now

        rotate_after = timedelta(seconds=self.settings.session_rotate_after_seconds)
        if now - record.rotated_at < rotate_after:
            await self.session.flush()
            return None

        new_token = security.generate_token()
        record.token_hash = security.hash_token(new_token)
        record.rotated_at = now
        await self.session.flush()
        return IssuedSession(session=record, token=new_token, csrf_token=record.csrf_token)

    async def revoke_session(self, session_id: uuid.UUID, reason: str = "logout") -> None:
        await self.session.execute(
            update(UserSession)
            .where(UserSession.id == session_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=utcnow(), revoked_reason=reason)
        )

    async def revoke_all_sessions(
        self, user_id: uuid.UUID, *, except_session_id: uuid.UUID | None = None, reason: str
    ) -> int:
        stmt = (
            update(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .values(revoked_at=utcnow(), revoked_reason=reason)
        )
        if except_session_id is not None:
            stmt = stmt.where(UserSession.id != except_session_id)
        result = await self.session.execute(stmt)
        return int(result.rowcount or 0)

    async def list_sessions(self, user_id: uuid.UUID) -> list[UserSession]:
        result = await self.session.execute(
            select(UserSession)
            .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
            .where(UserSession.expires_at > utcnow())
            .order_by(UserSession.last_seen_at.desc())
        )
        return list(result.scalars().all())

    # -- organizations -------------------------------------------------------

    async def default_organization_id(self, user_id: uuid.UUID) -> uuid.UUID | None:
        result = await self.session.execute(
            select(OrganizationMember.organization_id)
            .join(Organization, Organization.id == OrganizationMember.organization_id)
            .where(
                OrganizationMember.user_id == user_id,
                OrganizationMember.status == MemberStatus.ACTIVE,
                Organization.deleted_at.is_(None),
            )
            .order_by(Organization.is_personal.desc(), Organization.created_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_membership(
        self, user_id: uuid.UUID, organization_id: uuid.UUID
    ) -> OrganizationMember | None:
        result = await self.session.execute(
            select(OrganizationMember).where(
                OrganizationMember.user_id == user_id,
                OrganizationMember.organization_id == organization_id,
                OrganizationMember.status == MemberStatus.ACTIVE,
            )
        )
        return result.scalar_one_or_none()

    async def list_memberships(
        self, user_id: uuid.UUID
    ) -> list[tuple[OrganizationMember, Organization]]:
        result = await self.session.execute(
            select(OrganizationMember, Organization)
            .join(Organization, Organization.id == OrganizationMember.organization_id)
            .where(
                OrganizationMember.user_id == user_id,
                OrganizationMember.status == MemberStatus.ACTIVE,
                Organization.deleted_at.is_(None),
            )
            .order_by(Organization.is_personal.desc(), Organization.name.asc())
        )
        return [(member, org) for member, org in result.all()]

    async def switch_organization(
        self, record: UserSession, organization_id: uuid.UUID
    ) -> OrganizationMember:
        """Change the session's active workspace after verifying membership server-side."""
        membership = await self.get_membership(record.user_id, organization_id)
        if membership is None:
            # Not a 403: the caller must not learn whether the workspace exists.
            raise NotFoundError("Workspace not found.")
        record.active_organization_id = organization_id
        await self.session.flush()
        return membership

    # -- email tokens --------------------------------------------------------

    async def issue_email_token(self, user: User, purpose: str, context: RequestContext) -> str:
        ttl = (
            self.settings.email_verify_ttl_seconds
            if purpose == PURPOSE_VERIFY_EMAIL
            else self.settings.password_reset_ttl_seconds
        )
        # Outstanding tokens for the same purpose are consumed, so only the newest link works.
        await self.session.execute(
            update(EmailToken)
            .where(
                EmailToken.user_id == user.id,
                EmailToken.purpose == purpose,
                EmailToken.consumed_at.is_(None),
            )
            .values(consumed_at=utcnow())
        )
        token = security.generate_token()
        self.session.add(
            EmailToken(
                user_id=user.id,
                purpose=purpose,
                token_hash=security.hash_token(token),
                expires_at=utcnow() + timedelta(seconds=ttl),
                requested_ip=context.ip_address,
            )
        )
        await self.session.flush()
        return token

    async def consume_email_token(self, token: str, purpose: str) -> User:
        result = await self.session.execute(
            select(EmailToken).where(
                EmailToken.token_hash == security.hash_token(token),
                EmailToken.purpose == purpose,
            )
        )
        record = result.scalar_one_or_none()
        if record is None or record.consumed_at is not None or record.expires_at <= utcnow():
            raise ValidationError("This link is invalid or has expired. Request a new one.")

        record.consumed_at = utcnow()
        user = await self.get_user(record.user_id)
        if user is None:
            raise ValidationError("This link is invalid or has expired. Request a new one.")
        await self.session.flush()
        return user

    async def verify_email(self, token: str, context: RequestContext) -> User:
        user = await self.consume_email_token(token, PURPOSE_VERIFY_EMAIL)
        if user.email_verified_at is None:
            user.email_verified_at = utcnow()
        if user.status is UserStatus.PENDING:
            user.status = UserStatus.ACTIVE
        await self.session.flush()
        await self.audit.record(
            AuditAction.EMAIL_VERIFIED,
            actor_user_id=user.id,
            actor_email=user.email,
            entity_type="user",
            entity_id=user.id,
            ip_address=context.ip_address,
        )
        return user

    async def request_password_reset(self, email: str, context: RequestContext) -> None:
        """Always succeeds from the caller's point of view — no account enumeration."""
        user = await self.get_user_by_email(email)
        if user is None or not user.is_active:
            logger.info("password_reset_requested_unknown_account")
            return
        token = await self.issue_email_token(user, PURPOSE_RESET_PASSWORD, context)
        self.email_service.send_password_reset(user.email, user.display_name, token)
        await self.audit.record(
            AuditAction.PASSWORD_RESET_REQUESTED,
            actor_user_id=user.id,
            actor_email=user.email,
            entity_type="user",
            entity_id=user.id,
            ip_address=context.ip_address,
        )

    async def reset_password(self, token: str, new_password: str, context: RequestContext) -> User:
        user = await self.consume_email_token(token, PURPOSE_RESET_PASSWORD)
        user.password_hash = security.hash_password(new_password)
        user.password_changed_at = utcnow()
        user.failed_login_count = 0
        user.locked_until = None
        if user.email_verified_at is None:
            # Proving control of the inbox is exactly what verification asks for.
            user.email_verified_at = utcnow()
        await self.session.flush()

        await self.revoke_all_sessions(user.id, reason="password_reset")
        self.email_service.send_password_changed(user.email, user.display_name)
        await self.audit.record(
            AuditAction.PASSWORD_RESET_COMPLETED,
            actor_user_id=user.id,
            actor_email=user.email,
            entity_type="user",
            entity_id=user.id,
            ip_address=context.ip_address,
        )
        return user

    async def change_password(
        self,
        user: User,
        current_password: str,
        new_password: str,
        *,
        current_session_id: uuid.UUID | None,
        context: RequestContext,
    ) -> None:
        if not user.password_hash or not security.verify_password(
            current_password, user.password_hash
        ):
            raise AuthenticationError("Your current password is incorrect.")
        user.password_hash = security.hash_password(new_password)
        user.password_changed_at = utcnow()
        await self.session.flush()
        await self.revoke_all_sessions(
            user.id, except_session_id=current_session_id, reason="password_changed"
        )
        self.email_service.send_password_changed(user.email, user.display_name)
        await self.audit.record(
            AuditAction.PASSWORD_CHANGED,
            actor_user_id=user.id,
            actor_email=user.email,
            entity_type="user",
            entity_id=user.id,
            ip_address=context.ip_address,
        )

    async def resend_verification(self, user: User, context: RequestContext) -> None:
        if user.email_verified_at is not None:
            raise ConflictError("This email address is already verified.")
        token = await self.issue_email_token(user, PURPOSE_VERIFY_EMAIL, context)
        self.email_service.send_verification(user.email, user.display_name, token)


__all__ = [
    "PURPOSE_RESET_PASSWORD",
    "PURPOSE_VERIFY_EMAIL",
    "AuthService",
    "IssuedSession",
    "RequestContext",
    "normalize_email",
    "slugify",
]
