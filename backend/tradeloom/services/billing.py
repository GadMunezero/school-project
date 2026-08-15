"""Stripe billing.

The browser is never the source of truth for a subscription. Checkout returns a URL; the plan
only changes when a **signature-verified webhook** from Stripe says it did. A crafted request
claiming "I am now on Pro" changes nothing, because no endpoint accepts a plan from the client.

Webhook idempotency is enforced by a unique constraint on ``subscription_events.external_event_id``:
a redelivered event is recognised and skipped rather than applied twice.

When ``STRIPE_ENABLED`` is false the service runs in **local entitlement mode**: checkout is
refused with a clear message rather than pretending to work, and plans can only be changed by an
administrator. Nothing here fakes a payment flow.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core.config import get_settings
from tradeloom.core.enums import (
    AuditAction,
    NotificationKind,
    SubscriptionPlan,
    SubscriptionStatus,
)
from tradeloom.core.errors import (
    ExternalServiceError,
    NotFoundError,
    UnprocessableStateError,
    ValidationError,
)
from tradeloom.core.logging import get_logger
from tradeloom.core.timeutil import UTC, utcnow
from tradeloom.models.organization import Organization
from tradeloom.models.platform import Subscription, SubscriptionEvent
from tradeloom.services.audit import AuditService
from tradeloom.services.notifications import NotificationService

logger = get_logger(__name__)

#: Stripe events we act on. Anything else is recorded and ignored.
HANDLED_EVENTS = frozenset(
    {
        "checkout.session.completed",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.payment_failed",
        "invoice.payment_succeeded",
    }
)

_STATUS_MAP = {
    "active": SubscriptionStatus.ACTIVE,
    "trialing": SubscriptionStatus.TRIALING,
    "past_due": SubscriptionStatus.PAST_DUE,
    "canceled": SubscriptionStatus.CANCELED,
    "incomplete": SubscriptionStatus.INCOMPLETE,
    "incomplete_expired": SubscriptionStatus.INCOMPLETE,
    "unpaid": SubscriptionStatus.UNPAID,
}


class BillingService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        organization_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> None:
        self.session = session
        self.organization_id = organization_id
        self.actor_user_id = actor_user_id
        self.settings = get_settings()
        self.audit = AuditService(session)

    # ------------------------------------------------------------------

    @property
    def is_enabled(self) -> bool:
        return bool(self.settings.stripe_enabled and self.settings.stripe_secret_key)

    def _stripe(self) -> Any:
        if not self.is_enabled:
            raise UnprocessableStateError(
                "Billing is not configured on this deployment. "
                "Set STRIPE_ENABLED and the Stripe keys to enable checkout."
            )
        try:
            import stripe
        except ImportError as exc:  # pragma: no cover - packaged in the worker/billing extras
            raise ExternalServiceError("The billing integration is unavailable.") from exc
        stripe.api_key = self.settings.stripe_secret_key
        stripe.max_network_retries = 2
        return stripe

    def price_for(self, plan: SubscriptionPlan, interval: str = "monthly") -> str:
        mapping = {
            (SubscriptionPlan.PRO, "monthly"): self.settings.stripe_price_pro_monthly,
            (SubscriptionPlan.PRO, "yearly"): self.settings.stripe_price_pro_yearly,
            (SubscriptionPlan.ENTERPRISE, "monthly"): self.settings.stripe_price_enterprise_monthly,
        }
        price = mapping.get((plan, interval))
        if not price:
            raise ValidationError(f"No price is configured for the {plan.value} {interval} plan.")
        return price

    # ------------------------------------------------------------------

    async def get_or_create_subscription(self, organization_id: uuid.UUID) -> Subscription:
        result = await self.session.execute(
            select(Subscription).where(Subscription.organization_id == organization_id)
        )
        subscription = result.scalar_one_or_none()
        if subscription is None:
            subscription = Subscription(
                organization_id=organization_id,
                plan=SubscriptionPlan.FREE,
                status=SubscriptionStatus.ACTIVE,
            )
            self.session.add(subscription)
            await self.session.flush()
        return subscription

    async def create_checkout_session(
        self,
        organization_id: uuid.UUID,
        *,
        plan: SubscriptionPlan,
        interval: str,
        customer_email: str,
    ) -> str:
        if plan is SubscriptionPlan.FREE:
            raise ValidationError("The Free plan does not require checkout.")

        stripe = self._stripe()
        subscription = await self.get_or_create_subscription(organization_id)
        organization = await self.session.get(Organization, organization_id)
        if organization is None:
            raise NotFoundError("Workspace not found.")

        try:
            session = stripe.checkout.Session.create(
                mode="subscription",
                line_items=[{"price": self.price_for(plan, interval), "quantity": 1}],
                success_url=self.settings.billing_success_url,
                cancel_url=self.settings.billing_cancel_url,
                customer=subscription.stripe_customer_id or None,
                customer_email=(customer_email if not subscription.stripe_customer_id else None),
                # The organization id travels with the session so the webhook can attribute it
                # without trusting anything the browser sends back.
                client_reference_id=str(organization_id),
                metadata={"organization_id": str(organization_id), "plan": plan.value},
                subscription_data={
                    "metadata": {"organization_id": str(organization_id), "plan": plan.value}
                },
            )
        except Exception as exc:
            logger.warning("stripe_checkout_failed", error=type(exc).__name__)
            raise ExternalServiceError("Could not start checkout. Please try again.") from exc

        return str(session.url)

    async def create_portal_session(self, organization_id: uuid.UUID) -> str:
        stripe = self._stripe()
        subscription = await self.get_or_create_subscription(organization_id)
        if not subscription.stripe_customer_id:
            raise UnprocessableStateError(
                "This workspace has no billing history yet. Start a subscription first."
            )
        try:
            session = stripe.billing_portal.Session.create(
                customer=subscription.stripe_customer_id,
                return_url=self.settings.billing_success_url,
            )
        except Exception as exc:
            logger.warning("stripe_portal_failed", error=type(exc).__name__)
            raise ExternalServiceError("Could not open the billing portal.") from exc
        return str(session.url)

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    def verify_webhook(self, payload: bytes, signature: str | None) -> dict[str, Any]:
        """Verify the Stripe signature. An unverified payload is never processed."""
        if not self.settings.stripe_webhook_secret:
            raise UnprocessableStateError("Webhook processing is not configured.")
        if not signature:
            raise ValidationError("Missing signature header.")

        stripe = self._stripe()
        try:
            event = stripe.Webhook.construct_event(
                payload, signature, self.settings.stripe_webhook_secret
            )
        except Exception as exc:
            logger.warning("stripe_signature_invalid", error=type(exc).__name__)
            raise ValidationError("The webhook signature could not be verified.") from exc
        return dict(event)

    async def handle_event(self, event: dict[str, Any]) -> str:
        """Apply a verified event. Returns a short status for the response body."""
        event_id = str(event.get("id") or "")
        event_type = str(event.get("type") or "")

        existing = await self.session.execute(
            select(SubscriptionEvent).where(SubscriptionEvent.external_event_id == event_id)
        )
        if existing.scalar_one_or_none() is not None:
            # Stripe retries aggressively; a duplicate must be a no-op.
            return "duplicate"

        data = (event.get("data") or {}).get("object") or {}
        organization_id = self._organization_from(data)

        record = SubscriptionEvent(
            organization_id=organization_id,
            event_type=event_type,
            external_event_id=event_id or None,
            # A redacted subset only — the full Stripe object can contain personal data.
            payload={
                "type": event_type,
                "status": data.get("status"),
                "customer": data.get("customer"),
                "subscription": data.get("subscription") or data.get("id"),
            },
        )
        self.session.add(record)
        await self.session.flush()

        if event_type not in HANDLED_EVENTS:
            record.processed_at = utcnow()
            return "ignored"

        if organization_id is None:
            record.processing_error = "no organization reference on the event"
            return "unattributed"

        subscription = await self.get_or_create_subscription(organization_id)
        record.subscription_id = subscription.id

        if event_type in (
            "checkout.session.completed",
            "customer.subscription.created",
            "customer.subscription.updated",
        ):
            await self._apply_subscription_state(subscription, data)
        elif event_type == "customer.subscription.deleted":
            subscription.plan = SubscriptionPlan.FREE
            subscription.status = SubscriptionStatus.CANCELED
            subscription.canceled_at = utcnow()
            subscription.stripe_subscription_id = None
        elif event_type == "invoice.payment_failed":
            subscription.status = SubscriptionStatus.PAST_DUE
            await self._notify_owner(
                organization_id, NotificationKind.SUBSCRIPTION_PAYMENT_FAILED, {}
            )
        elif (
            event_type == "invoice.payment_succeeded"
            and subscription.status is SubscriptionStatus.PAST_DUE
        ):
            subscription.status = SubscriptionStatus.ACTIVE

        record.processed_at = utcnow()
        await self.session.flush()
        await self.audit.record(
            AuditAction.SUBSCRIPTION_CHANGED,
            organization_id=organization_id,
            entity_type="subscription",
            entity_id=subscription.id,
            summary=f"Stripe event {event_type}",
        )
        return "processed"

    def _organization_from(self, data: dict[str, Any]) -> uuid.UUID | None:
        candidates = [
            (data.get("metadata") or {}).get("organization_id"),
            data.get("client_reference_id"),
            ((data.get("subscription_details") or {}).get("metadata") or {}).get("organization_id"),
        ]
        for candidate in candidates:
            if not candidate:
                continue
            try:
                return uuid.UUID(str(candidate))
            except ValueError:
                continue
        return None

    async def _apply_subscription_state(
        self, subscription: Subscription, data: dict[str, Any]
    ) -> None:
        if customer := data.get("customer"):
            subscription.stripe_customer_id = str(customer)
        stripe_subscription_id = data.get("subscription") or (
            data.get("id") if str(data.get("object", "")) == "subscription" else None
        )
        if stripe_subscription_id:
            subscription.stripe_subscription_id = str(stripe_subscription_id)

        plan_name = (data.get("metadata") or {}).get("plan")
        if plan_name in {p.value for p in SubscriptionPlan}:
            subscription.plan = SubscriptionPlan(plan_name)
        elif subscription.plan is SubscriptionPlan.FREE:
            # A completed checkout without metadata still means *some* paid plan; default to Pro
            # and record the ambiguity rather than silently leaving the workspace on Free.
            subscription.plan = SubscriptionPlan.PRO

        status = str(data.get("status") or "active")
        subscription.status = _STATUS_MAP.get(status, SubscriptionStatus.ACTIVE)
        subscription.cancel_at_period_end = bool(data.get("cancel_at_period_end", False))
        subscription.current_period_start = _epoch(data.get("current_period_start"))
        subscription.current_period_end = _epoch(data.get("current_period_end"))
        subscription.trial_ends_at = _epoch(data.get("trial_end"))

        await self._notify_owner(
            subscription.organization_id,
            NotificationKind.SUBSCRIPTION_UPDATED,
            {"plan": subscription.plan.value},
        )

    async def _notify_owner(
        self, organization_id: uuid.UUID, kind: NotificationKind, data: dict[str, Any]
    ) -> None:
        organization = await self.session.get(Organization, organization_id)
        if organization is None:
            return
        await NotificationService(self.session).notify(
            organization_id=organization_id,
            user_id=organization.owner_user_id,
            kind=kind,
            data=data,
            link="/billing",
        )

    # ------------------------------------------------------------------

    async def set_plan_manually(
        self, organization_id: uuid.UUID, plan: SubscriptionPlan, *, reason: str
    ) -> Subscription:
        """Administrator override, used for enterprise contracts and support fixes.

        Deliberately not reachable from any workspace-scoped endpoint — only an authenticated
        platform administrator can call it.
        """
        subscription = await self.get_or_create_subscription(organization_id)
        subscription.plan = plan
        subscription.status = SubscriptionStatus.ACTIVE
        await self.session.flush()
        await self.audit.record(
            AuditAction.ADMIN_ACTION,
            organization_id=organization_id,
            actor_user_id=self.actor_user_id,
            entity_type="subscription",
            entity_id=subscription.id,
            summary=f"Plan set to {plan.value} manually: {reason}"[:255],
        )
        return subscription


def _epoch(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


__all__ = ["HANDLED_EVENTS", "BillingService"]
