"""Billing endpoints.

The plan is never accepted from the client. Checkout returns a Stripe URL; the subscription only
changes when a signature-verified webhook says so. The webhook route is deliberately outside the
session-cookie flow — it authenticates with Stripe's signature, not with a user session.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Request, status
from pydantic import Field

from tradeloom.api.deps import DbSession, Entitlements, ManagerTenant, Tenant
from tradeloom.core.enums import SubscriptionPlan
from tradeloom.core.logging import get_logger
from tradeloom.schemas.common import DataResponse, MessageResponse, TradeloomModel
from tradeloom.services.billing import BillingService
from tradeloom.services.entitlements import PLAN_LIMITS

logger = get_logger(__name__)
router = APIRouter(prefix="/billing", tags=["billing"])


class CheckoutRequest(TradeloomModel):
    plan: SubscriptionPlan
    interval: str = Field(default="monthly", pattern="^(monthly|yearly)$")


@router.get("/plans", response_model=DataResponse[list[dict]], summary="Available plans")
async def plans(tenant: Tenant) -> DataResponse[list[dict]]:
    service = BillingService(tenant.session, organization_id=tenant.organization_id)
    return DataResponse(
        data=[
            {
                "plan": plan.value,
                "limits": limits.to_dict(),
                # False when Stripe is not configured, so the UI can disable the button rather
                # than opening a checkout that would fail.
                "purchasable": plan is not SubscriptionPlan.FREE and service.is_enabled,
            }
            for plan, limits in PLAN_LIMITS.items()
        ]
    )


@router.get("/subscription", response_model=DataResponse[dict], summary="Current entitlements")
async def subscription(tenant: Tenant, entitlements: Entitlements) -> DataResponse[dict]:
    snapshot = await entitlements.snapshot(tenant.organization_id)
    service = BillingService(tenant.session, organization_id=tenant.organization_id)
    return DataResponse(data={**snapshot, "billing_enabled": service.is_enabled})


@router.post(
    "/checkout",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[dict],
    summary="Start a Stripe checkout session",
)
async def checkout(payload: CheckoutRequest, tenant: ManagerTenant) -> DataResponse[dict]:
    service = BillingService(
        tenant.session, organization_id=tenant.organization_id, actor_user_id=tenant.user_id
    )
    url = await service.create_checkout_session(
        tenant.organization_id,
        plan=payload.plan,
        interval=payload.interval,
        customer_email=tenant.user.email,
    )
    await tenant.session.commit()
    return DataResponse(data={"checkout_url": url})


@router.post("/portal", response_model=DataResponse[dict], summary="Open the billing portal")
async def portal(tenant: ManagerTenant) -> DataResponse[dict]:
    service = BillingService(
        tenant.session, organization_id=tenant.organization_id, actor_user_id=tenant.user_id
    )
    url = await service.create_portal_session(tenant.organization_id)
    await tenant.session.commit()
    return DataResponse(data={"portal_url": url})


@router.post("/webhook", response_model=MessageResponse, summary="Stripe webhook receiver")
async def webhook(
    request: Request,
    session: DbSession,
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
) -> MessageResponse:
    """Signature-verified webhook receiver.

    No session cookie is involved, and no CSRF token: the request is authenticated by Stripe's
    HMAC signature over the raw body. The raw bytes are used exactly as received, because any
    re-serialisation would invalidate the signature.
    """
    payload = await request.body()
    service = BillingService(session)
    event = service.verify_webhook(payload, stripe_signature)
    outcome = await service.handle_event(event)
    await session.commit()
    logger.info("stripe_webhook_processed", event_type=event.get("type"), outcome=outcome)
    return MessageResponse(message=outcome)


__all__ = ["router"]
