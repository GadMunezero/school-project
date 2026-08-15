"""Scheduled maintenance.

Retention work is deliberately conservative: it removes only what a documented policy says to
remove, and it logs counts so an unexpected purge is visible immediately.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from celery import shared_task
from sqlalchemy import delete, select, update

from tradeloom.core.logging import get_logger
from tradeloom.core.timeutil import utcnow
from tradeloom.db.session import session_scope
from tradeloom.models.file import FileObject
from tradeloom.models.identity import EmailToken, LoginAttempt, User, UserSession
from tradeloom.services.storage import get_storage
from worker.runtime import run_async

logger = get_logger(__name__)

#: A deletion request has this long to be cancelled before it is executed.
DELETION_GRACE_DAYS = 7
#: Login attempt rows older than this are purged; the audit log keeps the security-relevant trail.
LOGIN_ATTEMPT_RETENTION_DAYS = 90


@shared_task(name="tradeloom.maintenance.cleanup_expired_files", acks_late=True)
def cleanup_expired_files_task() -> dict[str, Any]:
    return run_async(_cleanup_files())


async def _cleanup_files() -> dict[str, Any]:
    async with session_scope() as session:
        result = await session.execute(
            select(FileObject).where(
                FileObject.expires_at.isnot(None),
                FileObject.expires_at <= utcnow(),
                FileObject.deleted_at.is_(None),
            )
        )
        storage = get_storage()
        removed = 0
        for record in result.scalars().all():
            try:
                storage.delete(record.object_key)
            except Exception:
                logger.warning("cleanup_storage_delete_failed", file_id=str(record.id))
            record.deleted_at = utcnow()
            record.is_available = False
            removed += 1
        logger.info("expired_files_cleaned", removed=removed)
        return {"removed": removed}


@shared_task(name="tradeloom.maintenance.purge_expired_sessions", acks_late=True)
def purge_expired_sessions_task() -> dict[str, Any]:
    return run_async(_purge_sessions())


async def _purge_sessions() -> dict[str, Any]:
    async with session_scope() as session:
        now = utcnow()
        sessions = await session.execute(
            delete(UserSession).where(UserSession.expires_at <= now - timedelta(days=30))
        )
        tokens = await session.execute(
            delete(EmailToken).where(EmailToken.expires_at <= now - timedelta(days=30))
        )
        attempts = await session.execute(
            delete(LoginAttempt).where(
                LoginAttempt.created_at <= now - timedelta(days=LOGIN_ATTEMPT_RETENTION_DAYS)
            )
        )
        counts = {
            "sessions": int(sessions.rowcount or 0),
            "email_tokens": int(tokens.rowcount or 0),
            "login_attempts": int(attempts.rowcount or 0),
        }
        logger.info("expired_records_purged", **counts)
        return counts


@shared_task(name="tradeloom.maintenance.process_deletion_requests", acks_late=True)
def process_deletion_requests_task() -> dict[str, Any]:
    return run_async(_process_deletions())


async def _process_deletions() -> dict[str, Any]:
    """Execute account deletions past their grace period.

    The user row is anonymised rather than dropped, because audit entries reference it and an
    append-only security log must not develop holes. Every piece of personal data is cleared and
    the workspaces the user solely owns cascade-delete with their trading data.
    """
    async with session_scope() as session:
        cutoff = utcnow() - timedelta(days=DELETION_GRACE_DAYS)
        result = await session.execute(
            select(User).where(
                User.deletion_requested_at.isnot(None),
                User.deletion_requested_at <= cutoff,
                User.deleted_at.is_(None),
            )
        )
        users = list(result.scalars().all())

        from tradeloom.core.enums import UserStatus
        from tradeloom.models.organization import Organization

        for user in users:
            owned = await session.execute(
                select(Organization).where(
                    Organization.owner_user_id == user.id, Organization.deleted_at.is_(None)
                )
            )
            for organization in owned.scalars().all():
                await session.execute(
                    delete(Organization).where(Organization.id == organization.id)
                )

            await session.execute(
                update(User)
                .where(User.id == user.id)
                .values(
                    email=f"deleted-{user.id}@removed.invalid",
                    password_hash=None,
                    full_name=None,
                    display_name=None,
                    preferences={},
                    status=UserStatus.DELETED,
                    deleted_at=utcnow(),
                    deletion_requested_at=None,
                )
            )

        logger.info("account_deletions_processed", count=len(users))
        return {"deleted": len(users)}


__all__ = [
    "cleanup_expired_files_task",
    "process_deletion_requests_task",
    "purge_expired_sessions_task",
]
