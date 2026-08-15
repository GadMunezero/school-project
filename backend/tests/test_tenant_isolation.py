"""Tenant isolation and IDOR tests.

These are the tests that matter most. Each one takes a real resource id belonging to Alice and
tries to reach it as Bob through the public API.

The expected result is **404, not 403**. A 403 would confirm the resource exists, which is itself
a leak — an attacker enumerating ids could map another tenant's data without ever reading it.
"""

from __future__ import annotations

import uuid

import pytest

from tests.conftest import create_account, create_trade

pytestmark = pytest.mark.anyio


class TestCrossTenantReads:
    async def test_bob_cannot_read_alices_account(self, alice, bob) -> None:  # type: ignore[no-untyped-def]
        account = await create_account(alice, "Alice primary")
        response = await bob.get(f"/api/v1/accounts/{account['id']}")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    async def test_bob_cannot_read_alices_trade(self, alice, bob) -> None:  # type: ignore[no-untyped-def]
        account = await create_account(alice)
        trade = await create_trade(alice, account["id"])
        response = await bob.get(f"/api/v1/trades/{trade['id']}")
        assert response.status_code == 404

    async def test_listing_never_returns_another_tenants_rows(self, alice, bob) -> None:  # type: ignore[no-untyped-def]
        account = await create_account(alice)
        await create_trade(alice, account["id"], symbol="SECRET")

        await create_account(bob, "Bob primary")
        trades = (await bob.get("/api/v1/trades")).json()["data"]
        assert trades == []

        accounts = (await bob.get("/api/v1/accounts")).json()["data"]
        assert [a["name"] for a in accounts] == ["Bob primary"]

    async def test_filtering_by_another_tenants_id_returns_nothing(self, alice, bob) -> None:  # type: ignore[no-untyped-def]
        alice_account = await create_account(alice)
        await create_trade(alice, alice_account["id"])
        await create_account(bob, "Bob primary")

        # An attacker-supplied filter value must not widen the tenant scope.
        response = await bob.get(f"/api/v1/trades?account_id={alice_account['id']}")
        assert response.status_code == 200
        assert response.json()["data"] == []

    async def test_analytics_is_scoped_to_the_caller(self, alice, bob) -> None:  # type: ignore[no-untyped-def]
        account = await create_account(alice)
        await create_trade(alice, account["id"], entry_price="100", exit_price="150")

        alice_metrics = (await alice.get("/api/v1/analytics/overview")).json()["data"]["metrics"]
        bob_metrics = (await bob.get("/api/v1/analytics/overview")).json()["data"]["metrics"]

        assert alice_metrics["total_trades"] == 1
        assert bob_metrics["total_trades"] == 0
        assert bob_metrics["net_profit"] == "0"

    async def test_search_does_not_cross_tenants(self, alice, bob) -> None:  # type: ignore[no-untyped-def]
        account = await create_account(alice)
        await create_trade(alice, account["id"], symbol="ZZTOP")
        results = (await bob.get("/api/v1/search?q=ZZTOP")).json()["data"]
        assert results == []


class TestCrossTenantWrites:
    async def test_bob_cannot_update_alices_trade(self, alice, bob) -> None:  # type: ignore[no-untyped-def]
        account = await create_account(alice)
        trade = await create_trade(alice, account["id"])

        response = await bob.patch(f"/api/v1/trades/{trade['id']}", json={"notes": "compromised"})
        assert response.status_code == 404

        # And Alice's data is untouched.
        after = (await alice.get(f"/api/v1/trades/{trade['id']}")).json()["data"]["trade"]
        assert after["notes"] != "compromised"

    async def test_bob_cannot_delete_alices_trade(self, alice, bob) -> None:  # type: ignore[no-untyped-def]
        account = await create_account(alice)
        trade = await create_trade(alice, account["id"])

        assert (await bob.delete(f"/api/v1/trades/{trade['id']}")).status_code == 404
        assert (await alice.get(f"/api/v1/trades/{trade['id']}")).status_code == 200

    async def test_bob_cannot_create_a_trade_on_alices_account(self, alice, bob) -> None:  # type: ignore[no-untyped-def]
        alice_account = await create_account(alice)
        response = await bob.post(
            "/api/v1/trades",
            json={
                "account_id": alice_account["id"],
                "symbol": "NVLX",
                "asset_type": "equity",
                "direction": "long",
                "entry_timestamp": "2024-05-06T14:30:00Z",
                "entry_price": "100",
                "quantity": "10",
            },
        )
        assert response.status_code == 404

    async def test_bulk_operations_silently_skip_foreign_ids(self, alice, bob) -> None:  # type: ignore[no-untyped-def]
        alice_account = await create_account(alice)
        alice_trade = await create_trade(alice, alice_account["id"])

        bob_account = await create_account(bob, "Bob primary")
        bob_trade = await create_trade(bob, bob_account["id"])

        response = await bob.post(
            "/api/v1/trades/bulk/delete",
            json={"trade_ids": [bob_trade["id"], alice_trade["id"]]},
        )
        assert response.status_code == 200
        # Bob asked for two, but only his own was eligible.
        assert response.json()["succeeded"] == 1
        assert (await alice.get(f"/api/v1/trades/{alice_trade['id']}")).status_code == 200

    async def test_bob_cannot_attach_alices_tag_to_his_trade(self, alice, bob) -> None:  # type: ignore[no-untyped-def]
        alice_tag = (await alice.post("/api/v1/tags", json={"name": "Alice only"})).json()["data"]

        bob_account = await create_account(bob, "Bob primary")
        bob_trade = await create_trade(bob, bob_account["id"])

        response = await bob.patch(
            f"/api/v1/trades/{bob_trade['id']}", json={"tag_ids": [alice_tag["id"]]}
        )
        assert response.status_code == 404


class TestCrossTenantFiles:
    async def test_bob_cannot_mint_a_signed_url_for_alices_file(self, alice, bob) -> None:  # type: ignore[no-untyped-def]
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 128
        upload = await alice.post(
            "/api/v1/files",
            files={"file": ("chart.png", png, "image/png")},
            data={"purpose": "screenshot"},
        )
        assert upload.status_code == 201, upload.text
        file_id = upload.json()["data"]["id"]

        # Alice can.
        assert (await alice.get(f"/api/v1/files/{file_id}/url")).status_code == 200
        # Bob cannot, and is not told the file exists.
        response = await bob.get(f"/api/v1/files/{file_id}/url")
        assert response.status_code == 404

    async def test_bob_cannot_delete_alices_file(self, alice, bob) -> None:  # type: ignore[no-untyped-def]
        png = b"\x89PNG\r\n\x1a\n" + b"0" * 128
        file_id = (
            await alice.post("/api/v1/files", files={"file": ("c.png", png, "image/png")})
        ).json()["data"]["id"]

        assert (await bob.delete(f"/api/v1/files/{file_id}")).status_code == 404
        assert (await alice.get(f"/api/v1/files/{file_id}/url")).status_code == 200


class TestCrossTenantStrategiesAndImports:
    async def test_bob_cannot_read_alices_strategy(self, alice, bob) -> None:  # type: ignore[no-untyped-def]
        strategy = (
            await alice.post(
                "/api/v1/strategies",
                json={"name": "Alice edge", "kind": "journal_only"},
            )
        ).json()["data"]
        assert (await bob.get(f"/api/v1/strategies/{strategy['id']}")).status_code == 404

    async def test_bob_cannot_read_alices_import(self, alice, bob) -> None:  # type: ignore[no-untyped-def]
        account = await create_account(alice)
        csv = "Time,Symbol,Side,Quantity,Price\n2024-05-06 14:30:00,NVLX,Buy,10,100\n"
        record = (
            await alice.post(
                "/api/v1/imports",
                files={"file": ("f.csv", csv.encode(), "text/csv")},
                data={"account_id": account["id"]},
            )
        ).json()["data"]
        assert (await bob.get(f"/api/v1/imports/{record['id']}")).status_code == 404

    async def test_bob_cannot_commit_alices_import(self, alice, bob) -> None:  # type: ignore[no-untyped-def]
        account = await create_account(alice)
        csv = "Time,Symbol,Side,Quantity,Price\n2024-05-06 14:30:00,NVLX,Buy,10,100\n"
        record = (
            await alice.post(
                "/api/v1/imports",
                files={"file": ("f.csv", csv.encode(), "text/csv")},
                data={"account_id": account["id"]},
            )
        ).json()["data"]
        assert (await bob.post(f"/api/v1/imports/{record['id']}/commit")).status_code == 404


class TestWorkspaceSwitching:
    async def test_bob_cannot_switch_into_alices_workspace(self, alice, bob) -> None:  # type: ignore[no-untyped-def]
        response = await bob.post(
            "/api/v1/auth/switch-organization",
            json={"organization_id": alice.organization_id},
        )
        # 404 rather than 403 — Bob learns nothing about whether the workspace exists.
        assert response.status_code == 404

    async def test_switching_to_a_random_uuid_is_refused(self, alice) -> None:  # type: ignore[no-untyped-def]
        response = await alice.post(
            "/api/v1/auth/switch-organization",
            json={"organization_id": str(uuid.uuid4())},
        )
        assert response.status_code == 404

    async def test_session_reports_only_your_own_workspaces(self, alice, bob) -> None:  # type: ignore[no-untyped-def]
        session = (await bob.get("/api/v1/auth/session")).json()["data"]
        ids = {org["id"] for org in session["organizations"]}
        assert alice.organization_id not in ids


class TestAdminAuthorization:
    async def test_ordinary_user_cannot_reach_admin_routes(self, alice) -> None:  # type: ignore[no-untyped-def]
        for path in (
            "/api/v1/admin/overview",
            "/api/v1/admin/users",
            "/api/v1/admin/organizations",
            "/api/v1/admin/jobs",
            "/api/v1/admin/audit-logs",
        ):
            response = await alice.get(path)
            assert response.status_code == 403, path
            assert response.json()["error"]["code"] == "forbidden"

    async def test_admin_routes_require_authentication(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/api/v1/admin/overview")
        assert response.status_code == 401

    async def test_a_user_cannot_promote_themselves(self, alice) -> None:  # type: ignore[no-untyped-def]
        # There is no endpoint that accepts a role from the client; the profile update ignores it.
        response = await alice.patch("/api/v1/users/me", json={"role": "admin"})
        assert response.status_code == 422  # `extra="forbid"` rejects the unknown field

        profile = (await alice.get("/api/v1/users/me")).json()["data"]
        assert profile["role"] == "user"
