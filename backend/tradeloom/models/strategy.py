"""Strategies, versions, parameters, setups and tags.

A **strategy** is the durable identity ("ORB continuation"). A **strategy version** is an
immutable snapshot of its logic and default parameters — backtests reference a version, never the
mutable parent, so a result stays reproducible after the strategy is edited.

``kind`` distinguishes:

* ``builtin`` — logic supplied by :mod:`tradeloom.engine.strategies`, selected by
  ``engine_key``. Only registered keys can ever be executed; user input never becomes code.
* ``journal_only`` — a label used to classify manual trades, with no executable logic.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from tradeloom.core.enums import ParameterType, StrategyKind, StrategyStatus
from tradeloom.db.base import Base, SoftDeleteMixin, TimestampMixin, UUIDPrimaryKeyMixin
from tradeloom.db.types import GUID, DecimalType, EnumType, JSONDict


class Strategy(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "strategies"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_strategies_org_name"),
        Index("ix_strategies_org_status", "organization_id", "status"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[StrategyKind] = mapped_column(
        EnumType(StrategyKind, 20), nullable=False, default=StrategyKind.JOURNAL_ONLY
    )
    #: Registry key of the built-in engine strategy. Validated against the registry on write.
    engine_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    status: Mapped[StrategyStatus] = mapped_column(
        EnumType(StrategyStatus, 16), nullable=False, default=StrategyStatus.DRAFT
    )
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Free-form checklist/playbook notes shown in the journal.
    playbook: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)


class StrategyVersion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Immutable snapshot. Editing a strategy creates a new version rather than mutating one."""

    __tablename__ = "strategy_versions"
    __table_args__ = (
        UniqueConstraint("strategy_id", "version", name="uq_strategy_versions_strategy_version"),
        Index("ix_strategy_versions_org", "organization_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(nullable=False, default=1)
    engine_key: Mapped[str | None] = mapped_column(String(60), nullable=True)
    #: Parameter name -> value, already validated against the version's parameter schema.
    parameters: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class StrategyParameter(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Declared parameter schema for a strategy version.

    Bounds are enforced server-side before a backtest is queued, so a crafted request cannot push
    a parameter outside the range the strategy author declared.
    """

    __tablename__ = "strategy_parameters"
    __table_args__ = (
        UniqueConstraint("strategy_version_id", "name", name="uq_strategy_parameters_version_name"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    strategy_version_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("strategy_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    param_type: Mapped[ParameterType] = mapped_column(EnumType(ParameterType, 16), nullable=False)
    default_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    minimum: Mapped[Decimal | None] = mapped_column(DecimalType(28, 10), nullable=True)
    maximum: Mapped[Decimal | None] = mapped_column(DecimalType(28, 10), nullable=True)
    step: Mapped[Decimal | None] = mapped_column(DecimalType(28, 10), nullable=True)
    choices: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    display_order: Mapped[int] = mapped_column(nullable=False, default=0)


class Setup(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    """A recurring pattern the trader looks for ("failed breakout", "gap fill")."""

    __tablename__ = "setups"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_setups_org_name"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    strategy_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    checklist: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Tag(Base, UUIDPrimaryKeyMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_tags_org_slug"),
        Index("ix_tags_org_category", "organization_id", "category"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    slug: Mapped[str] = mapped_column(String(60), nullable=False)
    #: Grouping shown in the tag picker: "mistake", "emotion", "market", "custom".
    category: Mapped[str] = mapped_column(String(32), nullable=False, default="custom")
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)


__all__ = ["Setup", "Strategy", "StrategyParameter", "StrategyVersion", "Tag"]
