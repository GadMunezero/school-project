"""Invite codes for a closed signup.

A beta is a guest list. When ``SIGNUP_MODE=invite`` the only way to create an account is with a
code an administrator issued, and this module is the whole of that gate.

Two decisions worth stating:

* **Redemption is a conditional UPDATE, not read-then-write.** Two people redeeming the last use
  of the same code at the same instant would both pass a "is there a use left?" check and both get
  in. Incrementing under ``WHERE used_count < max_uses`` makes the database decide, and the loser
  sees the same refusal as anyone arriving too late.
* **Codes are stored as issued.** An invite is a short-lived admission ticket, not a credential:
  an administrator has to read it back to send it to someone. It grants nothing beyond the right
  to register, and it stops working the moment it is spent, expired or revoked.
"""

from __future__ import annotations

import builtins
import secrets
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core.errors import NotFoundError, ValidationError
from tradeloom.core.timeutil import utcnow
from tradeloom.models.platform import InviteCode, InviteRedemption

#: Unambiguous in a hurried retype: no O/0, I/1/l, U/V.
_ALPHABET = "ABCDEFGHJKMNPQRSTWXYZ23456789"
CODE_LENGTH = 10


def generate_code() -> str:
    """A random, human-transcribable code."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))


def normalise(code: str) -> str:
    """Accept what a person actually types: spaces, dashes and lower case."""
    return code.strip().upper().replace(" ", "").replace("-", "")


class InviteService:
    """Issue, list, revoke and redeem invite codes. Platform-wide, never tenant-scoped."""

    def __init__(self, session: AsyncSession, *, actor_user_id: uuid.UUID | None = None) -> None:
        self.session = session
        self.actor_user_id = actor_user_id

    # -- administration -------------------------------------------------

    async def create(
        self,
        *,
        note: str | None = None,
        max_uses: int = 1,
        expires_in_days: int | None = 30,
    ) -> InviteCode:
        if max_uses < 1:
            raise ValidationError("An invite has to be good for at least one signup.")

        expires_at = utcnow() + timedelta(days=expires_in_days) if expires_in_days else None
        invite = InviteCode(
            code=generate_code(),
            note=(note or "").strip() or None,
            created_by_user_id=self.actor_user_id,
            max_uses=max_uses,
            used_count=0,
            expires_at=expires_at,
        )
        self.session.add(invite)
        await self.session.flush()
        return invite

    async def list(self, *, limit: int = 100) -> builtins.list[InviteCode]:
        result = await self.session.execute(
            select(InviteCode).order_by(InviteCode.created_at.desc()).limit(limit)
        )
        return builtins.list(result.scalars().all())

    async def revoke(self, invite_id: uuid.UUID) -> InviteCode:
        invite = await self.session.get(InviteCode, invite_id)
        if invite is None:
            raise NotFoundError("Invite not found.")
        if invite.revoked_at is None:
            invite.revoked_at = utcnow()
            await self.session.flush()
        return invite

    async def redemptions(self, invite_id: uuid.UUID) -> builtins.list[InviteRedemption]:
        result = await self.session.execute(
            select(InviteRedemption)
            .where(InviteRedemption.invite_code_id == invite_id)
            .order_by(InviteRedemption.created_at.asc())
        )
        return builtins.list(result.scalars().all())

    # -- redemption -----------------------------------------------------

    async def redeem(self, code: str, *, email: str) -> InviteCode:
        """Claim one use of a code, or raise.

        Every refusal says the same thing. An invite gate that distinguished "no such code" from
        "already used" would let someone map which codes exist, and there is nothing a would-be
        signer-up can do differently with the distinction anyway.
        """
        refusal = ValidationError("That invite code is not valid. Check it, or ask for a new one.")

        cleaned = normalise(code or "")
        if not cleaned:
            raise refusal

        now = utcnow()
        # One statement decides it: the row must still exist, be unrevoked, be unexpired, and have
        # a use left. Anything else matches nothing and updates nothing.
        result = await self.session.execute(
            update(InviteCode)
            .where(
                InviteCode.code == cleaned,
                InviteCode.revoked_at.is_(None),
                InviteCode.used_count < InviteCode.max_uses,
                (InviteCode.expires_at.is_(None)) | (InviteCode.expires_at > now),
            )
            .values(used_count=InviteCode.used_count + 1)
            .returning(InviteCode.id)
        )
        invite_id = result.scalar_one_or_none()
        if invite_id is None:
            raise refusal

        self.session.add(
            InviteRedemption(invite_code_id=invite_id, user_id=None, email=email.lower())
        )
        await self.session.flush()

        invite = await self.session.get(InviteCode, invite_id)
        assert invite is not None
        return invite

    async def attach_user(self, invite: InviteCode, user_id: uuid.UUID, email: str) -> None:
        """Record which account a redemption produced, once the user row exists."""
        result = await self.session.execute(
            select(InviteRedemption)
            .where(
                InviteRedemption.invite_code_id == invite.id,
                InviteRedemption.email == email.lower(),
                InviteRedemption.user_id.is_(None),
            )
            .order_by(InviteRedemption.created_at.desc())
            .limit(1)
        )
        redemption = result.scalar_one_or_none()
        if redemption is not None:
            redemption.user_id = user_id
            await self.session.flush()

    # -- presentation ---------------------------------------------------

    @staticmethod
    def to_dict(
        invite: InviteCode, *, redeemed_by: builtins.list[str] | None = None
    ) -> dict[str, Any]:
        now = utcnow()
        expired = invite.expires_at is not None and invite.expires_at <= now
        spent = invite.used_count >= invite.max_uses
        if invite.revoked_at is not None:
            state = "revoked"
        elif expired:
            state = "expired"
        elif spent:
            state = "used"
        else:
            state = "active"

        return {
            "id": str(invite.id),
            "code": invite.code,
            "note": invite.note,
            "max_uses": invite.max_uses,
            "used_count": invite.used_count,
            "uses_left": max(0, invite.max_uses - invite.used_count),
            "state": state,
            "expires_at": invite.expires_at.isoformat() if invite.expires_at else None,
            "revoked_at": invite.revoked_at.isoformat() if invite.revoked_at else None,
            "created_at": invite.created_at.isoformat(),
            "redeemed_by": redeemed_by or [],
        }

    async def count_active(self) -> int:
        now = utcnow()
        result = await self.session.execute(
            select(func.count())
            .select_from(InviteCode)
            .where(
                InviteCode.revoked_at.is_(None),
                InviteCode.used_count < InviteCode.max_uses,
                (InviteCode.expires_at.is_(None)) | (InviteCode.expires_at > now),
            )
        )
        return int(result.scalar_one() or 0)


__all__ = ["CODE_LENGTH", "InviteService", "generate_code", "normalise"]
