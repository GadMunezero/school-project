"""Plan entitlements.

Entitlements are resolved **server-side** from the organization's subscription row. The client
receives a resolved snapshot purely so it can grey out unavailable controls; every enforcement
point calls :meth:`EntitlementService.require` before doing the work, so a modified browser
payload buys nothing.

``None`` means unlimited. ``0`` means the feature is off.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core.enums import SubscriptionPlan, SubscriptionStatus
from tradeloom.core.errors import EntitlementError
from tradeloom.models.account import Account
from tradeloom.models.platform import Subscription
from tradeloom.models.trading import Trade


@dataclass(frozen=True, slots=True)
class PlanLimits:
    max_accounts: int | None
    max_open_trades: int | None
    max_trades: int | None
    max_backtests_per_day: int | None
    max_storage_bytes: int | None
    max_members: int | None
    replay_enabled: bool
    comparison_enabled: bool
    scheduled_reports: bool
    api_access: bool
    retention_days: int | None
    features: frozenset[str] = field(default_factory=frozenset)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_accounts": self.max_accounts,
            "max_open_trades": self.max_open_trades,
            "max_trades": self.max_trades,
            "max_backtests_per_day": self.max_backtests_per_day,
            "max_storage_bytes": self.max_storage_bytes,
            "max_members": self.max_members,
            "replay_enabled": self.replay_enabled,
            "comparison_enabled": self.comparison_enabled,
            "scheduled_reports": self.scheduled_reports,
            "api_access": self.api_access,
            "retention_days": self.retention_days,
            "features": sorted(self.features),
        }


PLAN_LIMITS: dict[SubscriptionPlan, PlanLimits] = {
    SubscriptionPlan.FREE: PlanLimits(
        max_accounts=2,
        max_open_trades=25,
        max_trades=1_000,
        max_backtests_per_day=5,
        max_storage_bytes=100 * 1024 * 1024,
        max_members=1,
        replay_enabled=False,
        comparison_enabled=False,
        scheduled_reports=False,
        api_access=False,
        retention_days=365,
        features=frozenset({"journal", "dashboard", "analytics_basic", "import", "export"}),
    ),
    SubscriptionPlan.PRO: PlanLimits(
        max_accounts=25,
        max_open_trades=None,
        max_trades=250_000,
        max_backtests_per_day=200,
        max_storage_bytes=10 * 1024 * 1024 * 1024,
        max_members=5,
        replay_enabled=True,
        comparison_enabled=True,
        scheduled_reports=True,
        api_access=True,
        retention_days=None,
        features=frozenset(
            {
                "journal",
                "dashboard",
                "analytics_basic",
                "analytics_advanced",
                "import",
                "export",
                "backtesting",
                "replay",
                "comparison",
                "strategies",
            }
        ),
    ),
    SubscriptionPlan.ENTERPRISE: PlanLimits(
        max_accounts=None,
        max_open_trades=None,
        max_trades=None,
        max_backtests_per_day=None,
        max_storage_bytes=None,
        max_members=None,
        replay_enabled=True,
        comparison_enabled=True,
        scheduled_reports=True,
        api_access=True,
        retention_days=None,
        features=frozenset(
            {
                "journal",
                "dashboard",
                "analytics_basic",
                "analytics_advanced",
                "import",
                "export",
                "backtesting",
                "replay",
                "comparison",
                "strategies",
                "team",
                "audit_export",
                "priority_support",
            }
        ),
    ),
}

#: Statuses that still grant paid features. `past_due` keeps access during the dunning window so a
#: temporary card failure does not lock a trader out of their own records mid-session.
_ENTITLED_STATUSES = {
    SubscriptionStatus.ACTIVE,
    SubscriptionStatus.TRIALING,
    SubscriptionStatus.PAST_DUE,
}


class EntitlementService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_subscription(self, organization_id: uuid.UUID) -> Subscription | None:
        result = await self.session.execute(
            select(Subscription).where(Subscription.organization_id == organization_id)
        )
        return result.scalar_one_or_none()

    async def resolve_plan(self, organization_id: uuid.UUID) -> SubscriptionPlan:
        subscription = await self.get_subscription(organization_id)
        if subscription is None:
            return SubscriptionPlan.FREE
        if subscription.status not in _ENTITLED_STATUSES:
            return SubscriptionPlan.FREE
        return subscription.plan

    async def limits_for(self, organization_id: uuid.UUID) -> PlanLimits:
        subscription = await self.get_subscription(organization_id)
        plan = SubscriptionPlan.FREE
        overrides: dict[str, Any] = {}
        if subscription is not None:
            if subscription.status in _ENTITLED_STATUSES:
                plan = subscription.plan
            overrides = subscription.entitlement_overrides or {}

        base = PLAN_LIMITS[plan]
        if not overrides:
            return base

        # Enterprise deals can widen (or explicitly narrow) any single limit without a code change.
        merged = base.to_dict()
        for key, value in overrides.items():
            if key in merged:
                merged[key] = value
        return PlanLimits(
            max_accounts=merged["max_accounts"],
            max_open_trades=merged["max_open_trades"],
            max_trades=merged["max_trades"],
            max_backtests_per_day=merged["max_backtests_per_day"],
            max_storage_bytes=merged["max_storage_bytes"],
            max_members=merged["max_members"],
            replay_enabled=bool(merged["replay_enabled"]),
            comparison_enabled=bool(merged["comparison_enabled"]),
            scheduled_reports=bool(merged["scheduled_reports"]),
            api_access=bool(merged["api_access"]),
            retention_days=merged["retention_days"],
            features=frozenset(merged["features"]),
        )

    async def snapshot(self, organization_id: uuid.UUID) -> dict[str, Any]:
        """Resolved limits plus current usage, for the billing page and UI affordances."""
        limits = await self.limits_for(organization_id)
        plan = await self.resolve_plan(organization_id)
        subscription = await self.get_subscription(organization_id)
        usage = await self.usage(organization_id)
        return {
            "plan": plan.value,
            "status": (
                subscription.status.value if subscription else SubscriptionStatus.ACTIVE.value
            ),
            "limits": limits.to_dict(),
            "usage": usage,
            "cancel_at_period_end": (
                bool(subscription.cancel_at_period_end) if subscription else False
            ),
            "current_period_end": (
                subscription.current_period_end.isoformat()
                if subscription and subscription.current_period_end
                else None
            ),
        }

    async def usage(self, organization_id: uuid.UUID) -> dict[str, int]:
        account_count = await self.session.scalar(
            select(func.count())
            .select_from(Account)
            .where(Account.organization_id == organization_id, Account.deleted_at.is_(None))
        )
        trade_count = await self.session.scalar(
            select(func.count())
            .select_from(Trade)
            .where(Trade.organization_id == organization_id, Trade.deleted_at.is_(None))
        )
        return {"accounts": int(account_count or 0), "trades": int(trade_count or 0)}

    # -- enforcement ---------------------------------------------------------

    async def require_feature(self, organization_id: uuid.UUID, feature: str) -> None:
        limits = await self.limits_for(organization_id)
        if feature not in limits.features:
            plan = await self.resolve_plan(organization_id)
            required = next(
                (
                    candidate.value
                    for candidate in (SubscriptionPlan.PRO, SubscriptionPlan.ENTERPRISE)
                    if feature in PLAN_LIMITS[candidate].features
                ),
                SubscriptionPlan.ENTERPRISE.value,
            )
            raise EntitlementError(
                f"The {feature.replace('_', ' ')} feature is not included in "
                f"the {plan.value} plan.",
                feature=feature,
                required_plan=required,
            )

    async def require_within_limit(
        self, organization_id: uuid.UUID, limit_name: str, current: int, *, adding: int = 1
    ) -> None:
        limits = await self.limits_for(organization_id)
        ceiling = getattr(limits, limit_name, None)
        if ceiling is None:
            return
        if current + adding > ceiling:
            plan = await self.resolve_plan(organization_id)
            raise EntitlementError(
                f"Your {plan.value} plan allows {ceiling} "
                f"{limit_name.removeprefix('max_').replace('_', ' ')}. "
                "Upgrade to add more.",
                feature=limit_name,
                limit=ceiling,
                current=current,
                required_plan=(
                    SubscriptionPlan.PRO.value
                    if plan is SubscriptionPlan.FREE
                    else SubscriptionPlan.ENTERPRISE.value
                ),
            )


__all__ = ["PLAN_LIMITS", "EntitlementService", "PlanLimits"]
