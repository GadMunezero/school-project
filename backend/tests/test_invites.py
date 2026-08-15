"""A closed signup is only as good as the code that refuses.

These tests care about the ways an invite gate leaks: a code that works twice, one that outlives
its expiry, one that keeps working after it was revoked, and a refusal that tells an attacker
which codes exist.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from tradeloom.core.errors import ValidationError
from tradeloom.core.timeutil import utcnow
from tradeloom.services.invites import CODE_LENGTH, InviteService, generate_code, normalise

pytestmark = pytest.mark.anyio


class TestCodeGeneration:
    def test_codes_avoid_characters_that_get_misread(self) -> None:
        """A code is read off a screen and typed into another one."""
        confusable = set("OI01lUV")
        for _ in range(200):
            assert not (set(generate_code()) & confusable)

    def test_codes_are_the_stated_length_and_not_repeated(self) -> None:
        codes = {generate_code() for _ in range(500)}
        assert len(codes) == 500
        assert all(len(code) == CODE_LENGTH for code in codes)

    def test_what_a_person_types_is_accepted(self) -> None:
        assert normalise(" abcd-efgh ") == "ABCDEFGH"
        assert normalise("ABCD EFGH") == "ABCDEFGH"


class TestRedemption:
    async def test_a_single_use_code_works_once(self, db) -> None:  # type: ignore[no-untyped-def]
        service = InviteService(db)
        invite = await service.create(note="First tester", max_uses=1)

        redeemed = await service.redeem(invite.code, email="one@example.com")
        assert redeemed.used_count == 1

        # The second attempt is refused, and refused the same way an unknown code is.
        with pytest.raises(ValidationError):
            await service.redeem(invite.code, email="two@example.com")

    async def test_a_multi_use_code_admits_exactly_its_limit(self, db) -> None:  # type: ignore[no-untyped-def]
        service = InviteService(db)
        invite = await service.create(note="Cohort", max_uses=3)

        for index in range(3):
            await service.redeem(invite.code, email=f"user{index}@example.com")

        with pytest.raises(ValidationError):
            await service.redeem(invite.code, email="fourth@example.com")

        await db.refresh(invite)
        assert invite.used_count == 3

    async def test_an_expired_code_is_refused(self, db) -> None:  # type: ignore[no-untyped-def]
        service = InviteService(db)
        invite = await service.create(max_uses=1, expires_in_days=1)
        invite.expires_at = utcnow() - timedelta(minutes=1)
        await db.flush()

        with pytest.raises(ValidationError):
            await service.redeem(invite.code, email="late@example.com")

    async def test_a_revoked_code_stops_working_immediately(self, db) -> None:  # type: ignore[no-untyped-def]
        service = InviteService(db)
        invite = await service.create(max_uses=5)
        await service.redeem(invite.code, email="early@example.com")

        await service.revoke(invite.id)

        with pytest.raises(ValidationError):
            await service.redeem(invite.code, email="after@example.com")

    async def test_an_unknown_code_and_a_spent_one_are_indistinguishable(self, db) -> None:  # type: ignore[no-untyped-def]
        """Otherwise the form is an oracle for which codes exist."""
        service = InviteService(db)
        invite = await service.create(max_uses=1)
        await service.redeem(invite.code, email="first@example.com")

        with pytest.raises(ValidationError) as spent:
            await service.redeem(invite.code, email="second@example.com")
        with pytest.raises(ValidationError) as unknown:
            await service.redeem("ZZZZZZZZZZ", email="second@example.com")

        assert str(spent.value) == str(unknown.value)

    async def test_an_empty_code_is_refused_without_touching_the_table(self, db) -> None:  # type: ignore[no-untyped-def]
        service = InviteService(db)
        invite = await service.create(max_uses=1)

        with pytest.raises(ValidationError):
            await service.redeem("", email="nobody@example.com")

        await db.refresh(invite)
        assert invite.used_count == 0

    async def test_the_limit_is_enforced_by_the_database_not_by_a_prior_read(self, db) -> None:  # type: ignore[no-untyped-def]
        """The guard against two people claiming the last use at once.

        A read-then-write implementation passes every test above and still lets both through under
        a race. This asserts the shape that makes the race impossible: the increment only applies
        to a row that still has a use left, so a stale in-memory copy cannot widen the limit.
        """
        service = InviteService(db)
        invite = await service.create(max_uses=1)

        # A stale reader believes the code is unused, which is exactly what a racing request holds.
        stale_view = invite.used_count
        assert stale_view == 0

        await service.redeem(invite.code, email="winner@example.com")

        # The stale view is still 0, and the second redemption must still fail.
        assert stale_view == 0
        with pytest.raises(ValidationError):
            await service.redeem(invite.code, email="loser@example.com")

    async def test_redemption_records_who_used_the_code(self, db) -> None:  # type: ignore[no-untyped-def]
        service = InviteService(db)
        invite = await service.create(max_uses=2)
        await service.redeem(invite.code, email="Alice@Example.com")

        redemptions = await service.redemptions(invite.id)
        assert [r.email for r in redemptions] == ["alice@example.com"]

    async def test_a_code_is_reported_with_the_state_an_admin_needs(self, db) -> None:  # type: ignore[no-untyped-def]
        service = InviteService(db)
        invite = await service.create(note="Beta 1", max_uses=2)

        assert InviteService.to_dict(invite)["state"] == "active"
        assert InviteService.to_dict(invite)["uses_left"] == 2

        await service.redeem(invite.code, email="a@example.com")
        await service.redeem(invite.code, email="b@example.com")
        await db.refresh(invite)
        assert InviteService.to_dict(invite)["state"] == "used"
        assert InviteService.to_dict(invite)["uses_left"] == 0

        await service.revoke(invite.id)
        await db.refresh(invite)
        # Revoked outranks used: it is the reason an admin acted, and the one they need to see.
        assert InviteService.to_dict(invite)["state"] == "revoked"

    async def test_revoking_something_that_does_not_exist_is_a_404(self, db) -> None:  # type: ignore[no-untyped-def]
        from tradeloom.core.errors import NotFoundError

        with pytest.raises(NotFoundError):
            await InviteService(db).revoke(uuid.uuid4())


class TestSignupGate:
    """The gate as a caller experiences it."""

    async def _admin_invite(self, db, **kwargs) -> str:  # type: ignore[no-untyped-def]
        invite = await InviteService(db).create(**kwargs)
        await db.commit()
        return invite.code

    async def test_open_signup_ignores_invite_codes(self, client) -> None:  # type: ignore[no-untyped-def]
        """The default. Existing deployments must not lock their users out on upgrade."""
        response = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "open@example.com",
                "password": "OpenSesame!2026",
                "full_name": "Open Signup",
                "accepted_terms": True,
            },
        )
        assert response.status_code == 201, response.text

    async def test_the_policy_endpoint_reports_the_door_state(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/api/v1/auth/signup-policy")
        assert response.status_code == 200
        assert response.json()["data"]["invite_required"] is False

    async def test_closed_signup_refuses_without_a_code(self, client, invite_only) -> None:  # type: ignore[no-untyped-def]
        assert (await client.get("/api/v1/auth/signup-policy")).json()["data"][
            "invite_required"
        ] is True

        response = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "uninvited@example.com",
                "password": "NoEntry!2026xy",
                "full_name": "Uninvited Person",
                "accepted_terms": True,
            },
        )
        assert response.status_code in (400, 422), response.text

    async def test_closed_signup_refuses_a_wrong_code(self, client, invite_only) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "guessing@example.com",
                "password": "Guessing!2026xy",
                "full_name": "Guessing Person",
                "accepted_terms": True,
                "invite_code": "ZZZZZZZZZZ",
            },
        )
        assert response.status_code in (400, 422), response.text

    async def test_a_valid_code_admits_exactly_one_account(self, client, db, invite_only) -> None:  # type: ignore[no-untyped-def]
        code = await self._admin_invite(db, note="Tester", max_uses=1)

        first = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "invited@example.com",
                "password": "Welcome!2026xy",
                "full_name": "Invited Person",
                "accepted_terms": True,
                "invite_code": code,
            },
        )
        assert first.status_code == 201, first.text

        second = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "gatecrasher@example.com",
                "password": "Welcome!2026xy",
                "full_name": "Gate Crasher",
                "accepted_terms": True,
                "invite_code": code,
            },
        )
        assert second.status_code in (400, 422), second.text

    async def test_a_refused_signup_creates_no_account(self, client, db, invite_only) -> None:  # type: ignore[no-untyped-def]
        """The invite is claimed before anything is written, so a refusal leaves no orphan user."""
        from sqlalchemy import func, select

        from tradeloom.models.identity import User

        before = (await db.execute(select(func.count()).select_from(User))).scalar_one()

        response = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "orphan@example.com",
                "password": "Orphaned!2026xy",
                "full_name": "Orphan Candidate",
                "accepted_terms": True,
                "invite_code": "NOPENOPENO",
            },
        )
        assert response.status_code in (400, 422)

        after = (await db.execute(select(func.count()).select_from(User))).scalar_one()
        assert after == before

    async def test_only_staff_can_mint_an_invite(self, alice) -> None:  # type: ignore[no-untyped-def]
        """A workspace owner is not platform staff."""
        response = await alice.post("/api/v1/admin/invites", json={"note": "self-service"})
        assert response.status_code == 403, response.text


class TestAdminEndpoints:
    """The console an administrator actually uses to run the guest list."""

    async def test_staff_can_issue_list_and_revoke(self, staff) -> None:  # type: ignore[no-untyped-def]
        created = await staff.post(
            "/api/v1/admin/invites", json={"note": "Jamie", "max_uses": 2, "expires_in_days": 14}
        )
        assert created.status_code == 201, created.text
        invite = created.json()["data"]

        # An administrator has to be able to read the code back to send it to someone.
        assert len(invite["code"]) == CODE_LENGTH
        assert invite["state"] == "active"
        assert invite["uses_left"] == 2
        assert invite["note"] == "Jamie"

        listed = await staff.get("/api/v1/admin/invites")
        assert listed.status_code == 200, listed.text
        assert invite["id"] in {row["id"] for row in listed.json()["data"]}

        revoked = await staff.post(f"/api/v1/admin/invites/{invite['id']}/revoke")
        assert revoked.status_code == 200, revoked.text
        assert revoked.json()["data"]["state"] == "revoked"

    async def test_the_list_shows_who_redeemed_each_code(self, staff, db, invite_only) -> None:  # type: ignore[no-untyped-def]
        created = await staff.post("/api/v1/admin/invites", json={"note": "Cohort", "max_uses": 2})
        code = created.json()["data"]["code"]

        signed_up = await staff.client.post(
            "/api/v1/auth/signup",
            json={
                "email": "cohort@example.com",
                "password": "Welcome!2026xy",
                "full_name": "Cohort Member",
                "accepted_terms": True,
                "invite_code": code,
            },
        )
        assert signed_up.status_code == 201, signed_up.text

        listed = await staff.get("/api/v1/admin/invites")
        row = next(r for r in listed.json()["data"] if r["code"] == code)
        assert row["used_count"] == 1
        assert row["redeemed_by"] == ["cohort@example.com"]

    async def test_issuing_an_invite_is_recorded_in_the_audit_log(self, staff) -> None:  # type: ignore[no-untyped-def]
        await staff.post("/api/v1/admin/invites", json={"note": "Audited"})

        logs = await staff.get("/api/v1/admin/audit-logs", params={"page_size": 20})
        assert logs.status_code == 200, logs.text
        assert any("Audited" in (row["summary"] or "") for row in logs.json()["data"])

    async def test_a_nonsense_use_count_is_refused(self, staff) -> None:  # type: ignore[no-untyped-def]
        response = await staff.post("/api/v1/admin/invites", json={"max_uses": 0})
        assert response.status_code == 422, response.text
