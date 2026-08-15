"""Policy documents, and the record that someone agreed to one.

The point of these is that consent is a thing that happened, not a flag someone set. A checkbox
that defaults to ticked and is never written down is worse than no checkbox: it looks like a
record and is not one.
"""

from __future__ import annotations

import pytest

from tradeloom.core import legal
from tradeloom.core.config import reset_settings_cache

pytestmark = pytest.mark.anyio

GOOD_PASSWORD = "CorrectHorse!7392"


class TestDocuments:
    def test_the_shipped_documents_declare_themselves_unwritten(self) -> None:
        """Nobody should write terms of service for you, so the repository does not pretend to."""
        legal.reset_cache()

        assert set(legal.unwritten()) == {legal.TERMS, legal.PRIVACY}
        assert legal.load(legal.TERMS).is_placeholder is True

    def test_a_missing_file_counts_as_unwritten_rather_than_crashing(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(legal, "CONTENT_ROOT", tmp_path)
        legal.reset_cache()

        document = legal.load(legal.TERMS)

        assert document.is_placeholder is True
        assert document.title == "Terms of Service"
        legal.reset_cache()

    def test_removing_the_marker_is_what_publishes_a_document(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:  # type: ignore[no-untyped-def]
        (tmp_path / "terms.md").write_text("# Terms\n\nReal terms, written by a real lawyer.")
        (tmp_path / "privacy.md").write_text("# Privacy\n\nA real policy.")
        monkeypatch.setattr(legal, "CONTENT_ROOT", tmp_path)
        legal.reset_cache()

        assert legal.unwritten() == []
        assert legal.load(legal.TERMS).is_placeholder is False
        legal.reset_cache()


class TestProductionGuard:
    def test_production_refuses_to_boot_on_placeholder_policies(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Recording that users accepted repository boilerplate is worse than having no terms."""
        legal.reset_cache()
        for key, value in {
            "TRADELOOM_ENV": "production",
            "SECRET_KEY": "a" * 64,
            "COOKIE_SECURE": "true",
            "DATABASE_URL": "postgresql+asyncpg://u:p@db:5432/tradeloom",
            "S3_ACCESS_KEY_ID": "key",
            "S3_SECRET_ACCESS_KEY": "secret",
        }.items():
            monkeypatch.setenv(key, value)
        reset_settings_cache()

        from tradeloom.core.config import get_settings

        problems = get_settings().validate_for_production()

        assert any("placeholder" in problem for problem in problems), problems

    def test_written_policies_clear_the_check(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:  # type: ignore[no-untyped-def]
        (tmp_path / "terms.md").write_text("# Terms\n\nReal.")
        (tmp_path / "privacy.md").write_text("# Privacy\n\nReal.")
        monkeypatch.setattr(legal, "CONTENT_ROOT", tmp_path)
        legal.reset_cache()

        for key, value in {
            "TRADELOOM_ENV": "production",
            "SECRET_KEY": "a" * 64,
            "COOKIE_SECURE": "true",
            "DATABASE_URL": "postgresql+asyncpg://u:p@db:5432/tradeloom",
            "S3_ACCESS_KEY_ID": "key",
            "S3_SECRET_ACCESS_KEY": "secret",
        }.items():
            monkeypatch.setenv(key, value)
        reset_settings_cache()

        from tradeloom.core.config import get_settings

        assert get_settings().validate_for_production() == []
        legal.reset_cache()


class TestEndpoints:
    async def test_documents_are_readable_without_an_account(self, client) -> None:  # type: ignore[no-untyped-def]
        """Nobody can agree to something they cannot read."""
        listed = await client.get("/api/v1/legal")
        assert listed.status_code == 200, listed.text
        assert {row["slug"] for row in listed.json()["data"]} == {"terms", "privacy"}

        document = await client.get("/api/v1/legal/terms")
        assert document.status_code == 200, document.text
        body = document.json()["data"]
        assert body["title"] == "Terms of Service"
        assert body["version"]
        # The page needs to know, so it can say so rather than present boilerplate as an agreement.
        assert body["is_placeholder"] is True

    async def test_an_unknown_document_is_a_404(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/api/v1/legal/cookies")
        assert response.status_code == 404, response.text


class TestConsent:
    async def test_omitting_consent_is_refused(self, client) -> None:  # type: ignore[no-untyped-def]
        """The hole this closes: the field used to default to true and the client hardcoded it."""
        response = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "silent@example.com",
                "password": GOOD_PASSWORD,
                "full_name": "Silent Consent",
            },
        )
        assert response.status_code == 422, response.text

    async def test_what_was_accepted_is_recorded_with_its_version(self, client, db) -> None:  # type: ignore[no-untyped-def]
        from sqlalchemy import select

        from tradeloom.models.identity import User
        from tradeloom.models.platform import PolicyAcceptance

        response = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "consenting@example.com",
                "password": GOOD_PASSWORD,
                "full_name": "Consenting Adult",
                "accepted_terms": True,
            },
        )
        assert response.status_code == 201, response.text

        user = (
            await db.execute(select(User).where(User.email == "consenting@example.com"))
        ).scalar_one()
        rows = (
            (await db.execute(select(PolicyAcceptance).where(PolicyAcceptance.user_id == user.id)))
            .scalars()
            .all()
        )

        recorded = {row.document: row.version for row in rows}
        assert recorded == legal.VERSIONS
        # The circumstances are the point of keeping the record at all.
        assert all(row.accepted_at is not None for row in rows)
