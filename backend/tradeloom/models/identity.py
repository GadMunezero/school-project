"""Users, sessions, OAuth links and one-time email tokens."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tradeloom.core.enums import UserRole, UserStatus
from tradeloom.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from tradeloom.db.types import GUID, EnumType, JSONDict, TZDateTime

if TYPE_CHECKING:  # avoids a circular import at runtime
    from tradeloom.models.organization import OrganizationMember


class User(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_users_email"),
        Index("ix_users_status_created", "status", "created_at"),
    )

    #: Stored lowercase; the service layer normalises before writing or querying.
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    #: Null for accounts created purely through OAuth. Such users must set a password (or keep
    #: using OAuth) before password sign-in works — we never invent a guessable placeholder.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    #: ``use_alter`` breaks the users -> file_objects -> organizations -> users foreign-key cycle.
    #: Without it PostgreSQL cannot order the CREATE TABLE statements in the initial migration.
    avatar_file_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(),
        ForeignKey(
            "file_objects.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_users_avatar_file_id_file_objects",
        ),
        nullable=True,
    )

    role: Mapped[UserRole] = mapped_column(
        EnumType(UserRole, 20), nullable=False, default=UserRole.USER
    )
    status: Mapped[UserStatus] = mapped_column(
        EnumType(UserStatus, 20), nullable=False, default=UserStatus.PENDING, index=True
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)

    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en-US")
    theme: Mapped[str] = mapped_column(String(16), nullable=False, default="system")
    preferences: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)

    last_login_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    failed_login_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)

    #: Set when the user requests erasure; the retention job performs the deletion.
    deletion_requested_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)

    sessions: Mapped[list[UserSession]] = relationship(
        back_populates="user", cascade="all, delete-orphan", lazy="noload"
    )
    memberships: Mapped[list[OrganizationMember]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        lazy="noload",
        # Disambiguates from ``organization_members.invited_by_user_id``, the other FK to users.
        foreign_keys="OrganizationMember.user_id",
    )

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE and self.deleted_at is None

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None


class UserSession(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A server-side session record.

    Only ``token_hash`` is stored. The plaintext token lives exclusively in the user's HTTP-only
    cookie, so a database compromise cannot be replayed as a live session.
    """

    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_sessions_token_hash"),
        Index("ix_sessions_user_revoked", "user_id", "revoked_at"),
        Index("ix_sessions_expires", "expires_at"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Companion token for double-submit CSRF validation on unsafe methods.
    csrf_token: Mapped[str] = mapped_column(String(64), nullable=False)

    #: The organization this session is currently acting within. Changing workspace rewrites this
    #: server-side; the client cannot select a tenant simply by sending a different id.
    active_organization_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )

    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(320), nullable=True)

    expires_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    rotated_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    revoked_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions", lazy="joined")


class OAuthAccount(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "oauth_accounts"
    __table_args__ = (
        UniqueConstraint("provider", "provider_account_id", name="uq_oauth_accounts_provider_uid"),
        Index("ix_oauth_accounts_user", "user_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_account_id: Mapped[str] = mapped_column(String(191), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    #: Provider profile snapshot. Access/refresh tokens are deliberately NOT persisted — Tradeloom
    #: only uses OAuth for identity, never to call provider APIs on the user's behalf.
    profile: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    linked_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)


class EmailToken(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Single-use tokens for email verification and password reset.

    Only the token digest is stored, never the token itself.
    """

    __tablename__ = "email_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_email_tokens_token_hash"),
        Index("ix_email_tokens_user_purpose", "user_id", "purpose"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # verify_email | reset_password
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    requested_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None


class LoginAttempt(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Persisted so lockout survives a Redis flush and so admins can review auth failures."""

    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_email_created", "email", "created_at"),)

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)


__all__ = ["EmailToken", "LoginAttempt", "OAuthAccount", "User", "UserSession"]
