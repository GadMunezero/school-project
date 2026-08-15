"""Organizations (workspaces), membership, and the role/permission catalogue.

Tenancy model: an *organization* owns all trading data. Every user has a personal organization
created at signup, and may belong to additional organizations. Ownership is enforced by carrying
``organization_id`` on every tenant-owned table and filtering on it in the repository layer —
see ``docs/SECURITY.md``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tradeloom.core.enums import MemberRole, MemberStatus
from tradeloom.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from tradeloom.db.types import GUID, EnumType, JSONDict, TZDateTime

if TYPE_CHECKING:  # avoids a circular import at runtime
    from tradeloom.models.identity import User


class Organization(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "organizations"
    __table_args__ = (UniqueConstraint("slug", name="uq_organizations_slug"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    #: True for the workspace auto-created at signup; it cannot be deleted while the user exists.
    is_personal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    base_currency: Mapped[str] = mapped_column(String(8), nullable=False, default="USD")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    settings: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)

    members: Mapped[list[OrganizationMember]] = relationship(
        back_populates="organization", cascade="all, delete-orphan", lazy="noload"
    )


class OrganizationMember(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "organization_members"
    __table_args__ = (
        UniqueConstraint("organization_id", "user_id", name="uq_organization_members_org_user"),
        Index("ix_organization_members_user_status", "user_id", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MemberRole] = mapped_column(
        EnumType(MemberRole, 20), nullable=False, default=MemberRole.MEMBER
    )
    status: Mapped[MemberStatus] = mapped_column(
        EnumType(MemberStatus, 20), nullable=False, default=MemberStatus.ACTIVE
    )
    invited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    invited_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    joined_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="members", lazy="joined")
    #: ``foreign_keys`` is required: this table has two FKs to ``users`` (the member and whoever
    #: invited them), so SQLAlchemy cannot infer which one defines the relationship.
    user: Mapped[User] = relationship(
        back_populates="memberships", lazy="joined", foreign_keys=[user_id]
    )


class Role(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Named role catalogue.

    Membership already carries a coarse :class:`MemberRole`; this table stores the human-readable
    catalogue and the permission bundle each role grants, so the mapping is data rather than code
    scattered across the service layer.
    """

    __tablename__ = "roles"
    __table_args__ = (UniqueConstraint("key", name="uq_roles_key"),)

    key: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rank: Mapped[int] = mapped_column(nullable=False, default=0)


class Permission(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "permissions"
    __table_args__ = (UniqueConstraint("key", name="uq_permissions_key"),)

    key: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)


class RolePermission(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("role_id", "permission_id", name="uq_role_permissions_role_permission"),
    )

    role_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("roles.id", ondelete="CASCADE"), nullable=False
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("permissions.id", ondelete="CASCADE"), nullable=False
    )


__all__ = ["Organization", "OrganizationMember", "Permission", "Role", "RolePermission"]
