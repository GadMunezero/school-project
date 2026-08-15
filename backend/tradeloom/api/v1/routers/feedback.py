"""In-app feedback.

Anyone signed in can file a report; only platform staff can read them. That asymmetry is the
whole design: a report carries the page a user was on and whatever their browser volunteered, and
one workspace has no business reading another's complaints.

The client-supplied ``context`` is stored verbatim and never interpreted. It is displayed to staff
as data, which is the only safe thing to do with a blob a browser wrote.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from pydantic import Field

from tradeloom.api.deps import Tenant
from tradeloom.core.logging import get_logger
from tradeloom.models.platform import FeedbackReport
from tradeloom.schemas.common import DataResponse, TradeloomModel

router = APIRouter(prefix="/feedback", tags=["feedback"])
logger = get_logger(__name__)

KINDS = ("bug", "idea", "question", "other")

#: Enough for a considered report, short enough that the field cannot be used as free storage.
MAX_MESSAGE = 4000
#: Context is diagnostic, not a payload. Anything larger is a client bug.
MAX_CONTEXT_KEYS = 20
MAX_CONTEXT_VALUE = 300


class FeedbackCreate(TradeloomModel):
    kind: str = Field(default="other", max_length=16)
    message: str = Field(min_length=3, max_length=MAX_MESSAGE)
    page: str | None = Field(default=None, max_length=255)
    context: dict[str, Any] = Field(default_factory=dict)


def _trim_context(context: dict[str, Any]) -> dict[str, str]:
    """Keep the diagnostic value, drop the rest.

    Coerced to strings and capped so a browser cannot post arbitrary structures into a column that
    an administrator will later read.
    """
    trimmed: dict[str, str] = {}
    for key, value in list(context.items())[:MAX_CONTEXT_KEYS]:
        trimmed[str(key)[:60]] = str(value)[:MAX_CONTEXT_VALUE]
    return trimmed


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=DataResponse[dict],
    summary="Send feedback from inside the app",
)
async def submit(payload: FeedbackCreate, tenant: Tenant) -> DataResponse[dict]:
    kind = payload.kind if payload.kind in KINDS else "other"

    report = FeedbackReport(
        organization_id=tenant.organization_id,
        user_id=tenant.user.id,
        reporter_email=tenant.user.email,
        kind=kind,
        message=payload.message.strip(),
        page=payload.page,
        context=_trim_context(payload.context),
        status="new",
    )
    tenant.session.add(report)
    await tenant.session.commit()

    # Logged without the message: a report can contain anything the user chose to type, including
    # things they would not expect to find in a log aggregator.
    logger.info("feedback_received", kind=kind, page=payload.page)

    return DataResponse(
        data={
            "id": str(report.id),
            "kind": report.kind,
            "message": "Thanks — this went straight to the people building Tradeloom.",
        }
    )


__all__ = ["MAX_MESSAGE", "router"]
