"""Audit logging.

Writes are append-only and best-effort in the sense that a logging failure must never break the
operation being audited — but the failure itself is logged loudly.

Field-level diffs are redacted here, at the point of capture, so a sensitive value cannot reach
the audit table by being passed through a generic `changes` dict.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core.enums import AuditAction
from tradeloom.core.logging import REDACTED_KEYS, get_logger, request_id_ctx
from tradeloom.core.timeutil import utcnow
from tradeloom.models.platform import AuditLog

logger = get_logger(__name__)

_MAX_VALUE_LENGTH = 500


def _sanitise(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        if isinstance(value, str) and len(value) > _MAX_VALUE_LENGTH:
            return value[:_MAX_VALUE_LENGTH] + "…"
        return value
    return str(value)[:_MAX_VALUE_LENGTH]


def diff_changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Field-level diff with sensitive keys replaced by a marker."""
    changes: dict[str, Any] = {}
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        if key.lower() in REDACTED_KEYS:
            changes[key] = {"changed": True}
            continue
        changes[key] = {"from": _sanitise(old), "to": _sanitise(new)}
    return changes


class AuditService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self,
        action: AuditAction,
        *,
        organization_id: uuid.UUID | None = None,
        actor_user_id: uuid.UUID | None = None,
        actor_email: str | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        summary: str | None = None,
        changes: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditLog | None:
        entry = AuditLog(
            created_at=utcnow(),
            organization_id=organization_id,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            summary=(summary or "")[:255] or None,
            changes=changes or {},
            ip_address=ip_address,
            user_agent=(user_agent or "")[:320] or None,
            request_id=request_id_ctx.get(),
        )
        try:
            self.session.add(entry)
            await self.session.flush()
        except Exception:  # pragma: no cover - the audit trail must not break the operation
            logger.exception("audit_write_failed", action=action.value, entity_type=entity_type)
            return None
        return entry


__all__ = ["AuditService", "diff_changes"]
