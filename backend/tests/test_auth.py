"""Authentication, session and CSRF tests."""

from __future__ import annotations

import pytest

from tests.conftest import signup

pytestmark = pytest.mark.anyio

GOOD_PASSWORD = "CorrectHorse!7392"


class TestSignup:
    async def test_signup_creates_a_personal_workspace(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "new@example.com",
                "password": GOOD_PASSWORD,
                "full_name": "New Trader",
                "accepted_terms": True,
            },
        )
        assert response.status_code == 201
        data = response.json()["data"]
        assert data["active_organization"]["is_personal"] is True
        assert data["active_organization"]["plan"] == "free"
        assert data["csrf_token"]
        # The session cookie is HttpOnly; the CSRF cookie deliberately is not.
        cookies = response.headers.get_list("set-cookie")
        session_cookie = next(c for c in cookies if c.startswith("tl_session="))
        csrf_cookie = next(c for c in cookies if c.startswith("tl_csrf="))
        assert "HttpOnly" in session_cookie
        assert "HttpOnly" not in csrf_cookie
        assert "SameSite=lax" in session_cookie.lower().replace("samesite=lax", "SameSite=lax")

    @pytest.mark.parametrize(
        "password",
        ["short1!A", "alllowercaseletters", "PASSWORD12345678", "aaaaaaaaaaaaaaaa1A!"],
    )
    async def test_weak_passwords_are_rejected(self, client, password: str) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "weak@example.com",
                "password": password,
                "full_name": "Weak",
                "accepted_terms": True,
            },
        )
        assert response.status_code == 422

    async def test_duplicate_email_is_rejected(self, client) -> None:  # type: ignore[no-untyped-def]
        await signup(client, "dup@example.com")
        response = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "dup@example.com",
                "password": GOOD_PASSWORD,
                "full_name": "Dup",
                "accepted_terms": True,
            },
        )
        assert response.status_code == 409

    async def test_terms_must_be_accepted(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/api/v1/auth/signup",
            json={
                "email": "terms@example.com",
                "password": GOOD_PASSWORD,
                "full_name": "Terms",
                "accepted_terms": False,
            },
        )
        assert response.status_code == 422


class TestLogin:
    async def test_wrong_password_is_indistinguishable_from_unknown_account(self, client) -> None:  # type: ignore[no-untyped-def]
        await signup(client, "known@example.com")

        wrong = await client.post(
            "/api/v1/auth/login",
            json={"email": "known@example.com", "password": "TotallyWrong!99xz"},
        )
        unknown = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "TotallyWrong!99xz"},
        )
        assert wrong.status_code == unknown.status_code == 401
        # Identical code and message: no account enumeration.
        assert wrong.json()["error"] == {
            **unknown.json()["error"],
            "request_id": wrong.json()["error"]["request_id"],
        }

    async def test_login_issues_a_new_session_id(self, client) -> None:  # type: ignore[no-untyped-def]
        user = await signup(client, "rotate@example.com")
        first = user._cookies["tl_session"]

        response = await client.post(
            "/api/v1/auth/login",
            json={"email": "rotate@example.com", "password": user.password},
        )
        assert response.status_code == 200
        # A fresh session token on every sign-in defeats fixation.
        assert response.cookies["tl_session"] != first

    async def test_repeated_failures_lock_the_account(self, client) -> None:  # type: ignore[no-untyped-def]
        await signup(client, "lock@example.com")
        statuses = []
        for _ in range(10):
            response = await client.post(
                "/api/v1/auth/login",
                json={"email": "lock@example.com", "password": "WrongPassword!123"},
            )
            statuses.append(response.status_code)
        # Eventually throttled rather than allowing unlimited guesses.
        assert 429 in statuses


class TestSessionAndCsrf:
    async def test_unauthenticated_requests_are_rejected(self, client) -> None:  # type: ignore[no-untyped-def]
        for path in ("/api/v1/trades", "/api/v1/accounts", "/api/v1/auth/session"):
            assert (await client.get(path)).status_code == 401, path

    async def test_unsafe_methods_require_the_csrf_header(self, client, alice) -> None:  # type: ignore[no-untyped-def]
        # Same cookies, no X-CSRF-Token header.
        response = await client.post(
            "/api/v1/accounts",
            json={"name": "No CSRF", "currency": "USD", "initial_balance": "1000"},
            cookies=alice._cookies,
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "csrf_failed"

    async def test_a_wrong_csrf_token_is_rejected(self, client, alice) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/api/v1/accounts",
            json={"name": "Bad CSRF", "currency": "USD", "initial_balance": "1000"},
            cookies=alice._cookies,
            headers={"X-CSRF-Token": "not-the-right-token"},
        )
        assert response.status_code == 403

    async def test_safe_methods_do_not_need_csrf(self, client, alice) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/api/v1/accounts", cookies=alice._cookies)
        assert response.status_code == 200

    async def test_logout_revokes_the_session(self, alice) -> None:  # type: ignore[no-untyped-def]
        assert (await alice.get("/api/v1/auth/session")).status_code == 200
        assert (await alice.post("/api/v1/auth/logout")).status_code == 200
        assert (await alice.get("/api/v1/auth/session")).status_code == 401

    async def test_a_forged_session_cookie_is_rejected(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/api/v1/auth/session", cookies={"tl_session": "a" * 43})
        assert response.status_code == 401

    async def test_listing_and_revoking_sessions(self, client, alice) -> None:  # type: ignore[no-untyped-def]
        # Sign in a second time to create another session.
        await client.post(
            "/api/v1/auth/login",
            json={"email": alice.email, "password": alice.password},
        )
        sessions = (await alice.get("/api/v1/auth/sessions")).json()["data"]
        assert len(sessions) >= 2
        assert sum(1 for s in sessions if s["is_current"]) == 1

        response = await alice.post("/api/v1/auth/sessions/revoke-all")
        assert response.status_code == 200
        assert response.json()["data"]["revoked"] >= 1
        # The current session still works.
        assert (await alice.get("/api/v1/auth/session")).status_code == 200


class TestPasswordManagement:
    async def test_password_reset_never_reveals_whether_an_account_exists(self, client) -> None:  # type: ignore[no-untyped-def]
        await signup(client, "reset@example.com")
        known = await client.post(
            "/api/v1/auth/password-reset", json={"email": "reset@example.com"}
        )
        unknown = await client.post(
            "/api/v1/auth/password-reset", json={"email": "ghost@example.com"}
        )
        assert known.status_code == unknown.status_code == 200
        assert known.json()["message"] == unknown.json()["message"]

    async def test_changing_the_password_requires_the_current_one(self, alice) -> None:  # type: ignore[no-untyped-def]
        response = await alice.post(
            "/api/v1/auth/password",
            json={"current_password": "wrong-password", "new_password": "BrandNew!Pass99x"},
        )
        assert response.status_code == 401

    async def test_changing_the_password_signs_out_other_sessions(self, client, alice) -> None:  # type: ignore[no-untyped-def]
        other = await client.post(
            "/api/v1/auth/login",
            json={"email": alice.email, "password": alice.password},
        )
        other_cookies = dict(other.cookies.items())

        response = await alice.post(
            "/api/v1/auth/password",
            json={"current_password": alice.password, "new_password": "BrandNew!Pass99x"},
        )
        assert response.status_code == 200

        # The other session is gone; this one survives.
        assert (await client.get("/api/v1/auth/session", cookies=other_cookies)).status_code == 401
        assert (await alice.get("/api/v1/auth/session")).status_code == 200

    async def test_an_invalid_reset_token_is_refused(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.post(
            "/api/v1/auth/password-reset/confirm",
            json={"token": "x" * 32, "new_password": "BrandNew!Pass99x"},
        )
        assert response.status_code == 422


class TestSecurityHeaders:
    async def test_responses_carry_hardening_headers(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/health/live")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "Content-Security-Policy" in response.headers
        assert response.headers["X-Request-ID"]

    async def test_hsts_is_absent_over_plain_http(self, client) -> None:  # type: ignore[no-untyped-def]
        # Sending HSTS from a non-HTTPS deployment would poison the developer's browser.
        response = await client.get("/health/live")
        assert "Strict-Transport-Security" not in response.headers


class TestHealth:
    async def test_liveness_does_not_touch_dependencies(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/health/live")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["checks"] == {}

    async def test_readiness_reports_dependency_state(self, client) -> None:  # type: ignore[no-untyped-def]
        response = await client.get("/health/ready")
        assert response.status_code == 200
        checks = response.json()["checks"]
        assert checks["database"]["ok"] is True
