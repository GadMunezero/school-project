"""Feedback goes one way: anyone signed in can file it, only staff can read it."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio


class TestSubmitting:
    async def test_a_signed_in_user_can_send_a_report(self, alice) -> None:  # type: ignore[no-untyped-def]
        response = await alice.post(
            "/api/v1/feedback",
            json={
                "kind": "bug",
                "message": "The equity curve stops a day short on the analytics page.",
                "page": "/analytics",
                "context": {"viewport": "1440x900", "browser": "Chromium 131"},
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["data"]["kind"] == "bug"

    async def test_an_anonymous_visitor_cannot(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post("/api/v1/feedback", json={"message": "hello there"})
        assert response.status_code in (401, 403), response.text

    async def test_an_unknown_kind_falls_back_rather_than_failing(self, alice) -> None:  # type: ignore[no-untyped-def]
        """A future client sending a new kind should still get its report filed."""
        response = await alice.post(
            "/api/v1/feedback", json={"kind": "wishlist", "message": "Add a dark mode toggle."}
        )
        assert response.status_code == 201, response.text
        assert response.json()["data"]["kind"] == "other"

    async def test_an_empty_report_is_refused(self, alice) -> None:  # type: ignore[no-untyped-def]
        response = await alice.post("/api/v1/feedback", json={"message": "  "})
        assert response.status_code == 422, response.text

    async def test_an_enormous_message_is_refused(self, alice) -> None:  # type: ignore[no-untyped-def]
        response = await alice.post("/api/v1/feedback", json={"message": "x" * 5000})
        assert response.status_code == 422, response.text

    async def test_context_is_capped_rather_than_stored_wholesale(self, alice, db) -> None:  # type: ignore[no-untyped-def]
        """The field is diagnostic, not free storage for whatever a browser feels like posting."""
        from sqlalchemy import select

        from tradeloom.models.platform import FeedbackReport

        response = await alice.post(
            "/api/v1/feedback",
            json={
                "message": "Something went wrong.",
                "context": {f"key{i}": "v" * 1000 for i in range(60)},
            },
        )
        assert response.status_code == 201, response.text

        report = (
            (await db.execute(select(FeedbackReport).order_by(FeedbackReport.created_at.desc())))
            .scalars()
            .first()
        )
        assert report is not None
        assert len(report.context) <= 20
        assert all(len(value) <= 300 for value in report.context.values())


class TestReading:
    async def test_a_workspace_owner_cannot_read_the_queue(self, alice) -> None:  # type: ignore[no-untyped-def]
        """One workspace has no business reading another's complaints."""
        await alice.post("/api/v1/feedback", json={"message": "A report of my own."})

        response = await alice.get("/api/v1/admin/feedback")
        assert response.status_code == 403, response.text

    async def test_staff_read_reports_and_can_triage_them(self, staff) -> None:  # type: ignore[no-untyped-def]
        submitted = await staff.post(
            "/api/v1/feedback",
            json={"kind": "idea", "message": "Let me tag a backtest.", "page": "/backtester"},
        )
        assert submitted.status_code == 201, submitted.text
        report_id = submitted.json()["data"]["id"]

        listed = await staff.get("/api/v1/admin/feedback")
        assert listed.status_code == 200, listed.text
        row = next(r for r in listed.json()["data"] if r["id"] == report_id)
        assert row["status"] == "new"
        assert row["page"] == "/backtester"
        # Staff need to know who to reply to.
        assert row["reporter_email"] == staff.email

        triaged = await staff.post(
            f"/api/v1/admin/feedback/{report_id}/status", json={"status": "reviewed"}
        )
        assert triaged.status_code == 200, triaged.text
        assert triaged.json()["data"]["status"] == "reviewed"

    async def test_the_queue_can_be_filtered_to_what_is_untriaged(self, staff) -> None:  # type: ignore[no-untyped-def]
        first = await staff.post("/api/v1/feedback", json={"message": "Report one."})
        await staff.post("/api/v1/feedback", json={"message": "Report two."})
        await staff.post(
            f"/api/v1/admin/feedback/{first.json()['data']['id']}/status",
            json={"status": "closed"},
        )

        new_only = await staff.get("/api/v1/admin/feedback", params={"status": "new"})
        assert new_only.status_code == 200, new_only.text
        ids = {row["id"] for row in new_only.json()["data"]}
        assert first.json()["data"]["id"] not in ids

    async def test_an_invalid_status_is_refused(self, staff) -> None:  # type: ignore[no-untyped-def]
        submitted = await staff.post("/api/v1/feedback", json={"message": "Report."})
        response = await staff.post(
            f"/api/v1/admin/feedback/{submitted.json()['data']['id']}/status",
            json={"status": "wontfix"},
        )
        assert response.status_code == 422, response.text
