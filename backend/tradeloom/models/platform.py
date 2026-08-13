"""Platform-level records: analytics snapshots, billing, notifications, jobs and audit logs."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from tradeloom.core.enums import (
    AuditAction,
    JobStatus,
    NotificationKind,
    NotificationSeverity,
    SubscriptionPlan,
    SubscriptionStatus,
)
from tradeloom.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from tradeloom.db.types import GUID, EnumType, JSONDict, TZDateTime, money_column


class AnalyticsSnapshot(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Cached aggregate for a (scope, period) pair.

    Analytics are always *computable* from trades on demand; snapshots exist so common dashboard
    queries do not rescan the trade table. A snapshot is invalidated by ``source_revision``, which
    is bumped whenever the underlying trades change.
    """

    __tablename__ = "analytics_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "scope",
            "scope_id",
            "period",
            "period_start",
            name="uq_analytics_snapshots_scope_period",
        ),
        Index("ix_analytics_snapshots_org_period", "organization_id", "period", "period_start"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: "organization" | "account" | "strategy" | "setup" | "symbol"
    scope: Mapped[str] = mapped_column(String(24), nullable=False)
    scope_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    #: "day" | "week" | "month" | "year" | "all"
    period: Mapped[str] = mapped_column(String(12), nullable=False)
    period_start: Mapped[date] = mapped_column(nullable=False)
    period_end: Mapped[date | None] = mapped_column(nullable=True)

    metrics: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    trade_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    net_pnl: Mapped[Decimal] = mapped_column(money_column(), nullable=False, default=0)
    computed_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    source_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class Subscription(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Server-side entitlement record. The browser never dictates plan state."""

    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("organization_id", name="uq_subscriptions_organization"),
        Index("ix_subscriptions_customer", "stripe_customer_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    plan: Mapped[SubscriptionPlan] = mapped_column(
        EnumType(SubscriptionPlan, 16), nullable=False, default=SubscriptionPlan.FREE
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        EnumType(SubscriptionStatus, 16), nullable=False, default=SubscriptionStatus.ACTIVE
    )

    stripe_customer_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    stripe_price_id: Mapped[str | None] = mapped_column(String(80), nullable=True)

    current_period_start: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    canceled_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    #: Per-organization overrides layered over the plan defaults (enterprise deals, comped seats).
    entitlement_overrides: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)


class SubscriptionEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Append-only billing event log, doubling as the webhook idempotency table.

    ``external_event_id`` is unique, so a Stripe redelivery is recognised and skipped rather than
    applied twice.
    """

    __tablename__ = "subscription_events"
    __table_args__ = (
        UniqueConstraint("external_event_id", name="uq_subscription_events_external_id"),
        Index("ix_subscription_events_org_created", "organization_id", "created_at"),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    external_event_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: Redacted payload subset — never the full Stripe object, which can include PII.
    payload: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    processed_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    processing_error: Mapped[str | None] = mapped_column(String(500), nullable=True)


class Notification(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_read", "user_id", "read_at"),
        Index("ix_notifications_org_created", "organization_id", "created_at"),
        # Created in migration b1d4e7a90c22; declared so `alembic check` sees no drift.
        Index(
            "ix_notifications_unread",
            "user_id",
            "created_at",
            postgresql_where=text("read_at IS NULL"),
            sqlite_where=text("read_at IS NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[NotificationKind] = mapped_column(EnumType(NotificationKind, 40), nullable=False)
    severity: Mapped[NotificationSeverity] = mapped_column(
        EnumType(NotificationSeverity, 12), nullable=False, default=NotificationSeverity.INFO
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: In-app deep link, e.g. ``/backtester/<id>``. Always relative — never an external URL.
    link: Mapped[str | None] = mapped_column(String(255), nullable=True)
    data: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)


class JobRecord(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Tracks a background job's lifecycle independently of the Celery result backend.

    Celery results expire; this table is the durable, queryable record the admin dashboard and the
    UI's progress indicators read from.
    """

    __tablename__ = "job_records"
    __table_args__ = (
        Index("ix_job_records_org_status", "organization_id", "status"),
        Index("ix_job_records_kind_created", "kind", "created_at"),
        Index(
            "ix_job_records_active",
            "kind",
            "created_at",
            postgresql_where=text("status IN ('queued', 'running')"),
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
        UniqueConstraint("idempotency_key", name="uq_job_records_idempotency_key"),
    )

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True, index=True
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        EnumType(JobStatus, 16), nullable=False, default=JobStatus.QUEUED
    )
    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    queue: Mapped[str] = mapped_column(String(32), nullable=False, default="default")
    #: Set by callers that must not enqueue the same work twice (webhooks, retried submissions).
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)

    payload: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    result: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_message: Mapped[str | None] = mapped_column(String(255), nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    queued_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    #: What the user is shown.
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Internal diagnostics (exception type, traceback digest). Never serialised to API responses.
    error_detail: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)


class AuditLog(Base, UUIDPrimaryKeyMixin):
    """Append-only security and change log.

    No ``updated_at`` and no update path: rows are written once. Retention is handled by the
    scheduled cleanup job described in ``docs/DEPLOYMENT.md``.
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_org_created", "organization_id", "created_at"),
        Index("ix_audit_logs_actor_created", "actor_user_id", "created_at"),
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
    )

    created_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False, index=True)

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Retained even when the user row is deleted, so the log stays meaningful after erasure.
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    action: Mapped[AuditAction] = mapped_column(EnumType(AuditAction, 48), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(48), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(GUID(), nullable=True)
    summary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Field-level before/after for updates, with sensitive keys already redacted by the service.
    changes: Mapped[dict] = mapped_column(JSONDict(), nullable=False, default=dict)

    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(320), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


__all__ = [
    "AnalyticsSnapshot",
    "AuditLog",
    "JobRecord",
    "Notification",
    "Subscription",
    "SubscriptionEvent",
]
