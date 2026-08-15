"""What may and may not leave the process in an error report.

An error reporter is a pipe to a third party. These tests are about what goes down it: never a
password, never a session token, never a signed URL, and never a user's email address — none of
which help diagnose anything, and all of which are somebody else's data.
"""

from __future__ import annotations

import pytest

from tradeloom.core.config import reset_settings_cache
from tradeloom.core.logging import REDACTION_PLACEHOLDER
from tradeloom.core.observability import _before_send, _scrub, init_sentry


class TestScrubbing:
    def test_secrets_are_replaced_wherever_they_sit(self) -> None:
        event = {
            "request": {
                "data": {
                    "email": "trader@example.com",
                    "password": "hunter2-the-real-one",
                    "csrf_token": "abc123",
                }
            },
            "extra": {"stripe_secret_key": "sk_live_dangerous"},
        }

        scrubbed = _scrub(event)

        assert scrubbed["request"]["data"]["password"] == REDACTION_PLACEHOLDER
        assert scrubbed["request"]["data"]["csrf_token"] == REDACTION_PLACEHOLDER
        assert scrubbed["extra"]["stripe_secret_key"] == REDACTION_PLACEHOLDER
        # Not every field is a secret; scrubbing everything would make reports useless.
        assert scrubbed["request"]["data"]["email"] == "trader@example.com"

    def test_secrets_nested_in_lists_are_found(self) -> None:
        event = {"breadcrumbs": [{"data": {"api_key": "live-key"}}, {"data": {"page": "/journal"}}]}

        scrubbed = _scrub(event)

        assert scrubbed["breadcrumbs"][0]["data"]["api_key"] == REDACTION_PLACEHOLDER
        assert scrubbed["breadcrumbs"][1]["data"]["page"] == "/journal"

    def test_a_signed_url_keeps_its_path_and_loses_its_credential(self) -> None:
        """The query string of a presigned URL *is* the credential."""
        url = "https://s3.example.com/uploads/statement.csv?X-Amz-Signature=deadbeef&expires=99"

        scrubbed = _scrub({"extra": {"location": url}})["extra"]["location"]

        assert scrubbed.startswith("https://s3.example.com/uploads/statement.csv?")
        assert "deadbeef" not in scrubbed

    def test_a_verification_link_loses_its_token(self) -> None:
        link = "https://app.example.com/verify-email?token=secret-value-here"

        scrubbed = _scrub({"extra": {"link": link}})["extra"]["link"]

        assert "secret-value-here" not in scrubbed

    def test_scrubbing_leaves_ordinary_values_alone(self) -> None:
        event = {"tags": {"component": "api"}, "level": "error", "count": 3}

        assert _scrub(event) == event


class TestBeforeSend:
    def test_a_user_is_reduced_to_an_id(self) -> None:
        """An email in a third party's error tool is personal data for no diagnostic gain."""
        event = {"user": {"id": "abc", "email": "trader@example.com", "ip_address": "1.2.3.4"}}

        sent = _before_send(event, {})

        assert sent is not None
        assert sent["user"] == {"id": "abc"}

    def test_an_anonymous_event_carries_no_user_at_all(self) -> None:
        sent = _before_send({"user": {"email": "trader@example.com"}}, {})

        assert sent is not None
        assert sent["user"] is None

    def test_the_gate_scrubs_as_well_as_trims(self) -> None:
        event = {"user": {"id": "x"}, "extra": {"session_token": "tok"}}

        sent = _before_send(event, {})

        assert sent is not None
        assert sent["extra"]["session_token"] == REDACTION_PLACEHOLDER


class TestInitialisation:
    def test_without_a_dsn_nothing_is_switched_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The default. A deployment that wants no third party involved sets nothing."""
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        reset_settings_cache()

        assert init_sentry("test") is False

    def test_with_a_dsn_it_starts_with_pii_switched_off(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The options are the whole point, so they are asserted rather than the fact it ran.

        `sentry_sdk.init` is captured rather than executed: a real client opens a transport and
        starts posting envelopes, and a test suite has no business sending anything anywhere.
        """
        sentry_sdk = pytest.importorskip("sentry_sdk")

        captured: dict[str, object] = {}
        monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: captured.update(kwargs))
        monkeypatch.setattr(sentry_sdk, "set_tag", lambda *_: None)
        monkeypatch.setattr("tradeloom.core.observability._initialised", False)
        monkeypatch.setenv("SENTRY_DSN", "https://public@example.ingest.sentry.io/1")
        monkeypatch.setenv("TRADELOOM_ENV", "test")
        reset_settings_cache()

        assert init_sentry("api") is True

        assert captured["dsn"] == "https://public@example.ingest.sentry.io/1"
        assert captured["environment"] == "test"
        # Never on: request bodies carry passwords and trade data.
        assert captured["send_default_pii"] is False
        assert captured["max_request_body_size"] == "never"
        # And the scrubbing gate is actually attached, not merely defined.
        assert captured["before_send"] is _before_send

        monkeypatch.setattr("tradeloom.core.observability._initialised", False)
