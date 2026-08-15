"""Error reporting.

Optional by construction: with no ``SENTRY_DSN`` set, :func:`init_sentry` does nothing and the
application behaves exactly as it did before. A deployment that does not want a third party
holding its stack traces simply leaves it unset.

What leaves the process is deliberately narrow. ``send_default_pii`` stays off, so Sentry does not
attach request bodies, cookies or headers of its own accord, and :func:`_scrub` walks whatever is
left against the same :data:`~tradeloom.core.logging.REDACTED_KEYS` the log pipeline uses — one
list, so a secret that never reaches the logs never reaches an error report either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tradeloom.core.config import get_settings
from tradeloom.core.logging import REDACTED_KEYS, REDACTION_PLACEHOLDER, get_logger

if TYPE_CHECKING:  # pragma: no cover - the SDK is an optional extra
    from sentry_sdk.types import Event
else:  # The hook is typed against the SDK's own Event when it is installed, and a plain dict
    # otherwise, so this module still type-checks without the extra present.
    Event = dict

logger = get_logger(__name__)

#: Query-string and body keys are scrubbed by name; these URL fragments are scrubbed wholesale
#: because the value *is* the secret.
_SENSITIVE_PATH_HINTS = ("token=", "signature=", "sig=", "key=")

_initialised = False


def _scrub_value(key: str, value: Any) -> Any:
    if key.lower() in REDACTED_KEYS:
        return REDACTION_PLACEHOLDER
    return _scrub(value)


def _scrub(value: Any) -> Any:
    """Recursively replace anything that looks like a secret.

    Depth is not bounded because Sentry events are already size-capped before they reach here, and
    a partial scrub would be worse than a slow one.
    """
    if isinstance(value, dict):
        return {key: _scrub_value(str(key), item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        scrubbed = [_scrub(item) for item in value]
        return type(value)(scrubbed) if isinstance(value, tuple) else scrubbed
    if isinstance(value, str) and any(hint in value.lower() for hint in _SENSITIVE_PATH_HINTS):
        # A presigned URL or a verification link: the query string is the credential.
        head, _, _ = value.partition("?")
        return f"{head}?{REDACTION_PLACEHOLDER}"
    return value


def _before_send(event: Event, _hint: dict[str, Any]) -> Event | None:
    """Last gate before an event leaves the process."""
    scrubbed: Any = _scrub(dict(event))
    # The user is identified by id only. An email in an error report is personal data sitting in a
    # third party's system for no diagnostic gain.
    user = scrubbed.get("user")
    if isinstance(user, dict):
        scrubbed["user"] = {"id": user.get("id")} if user.get("id") else None
    return scrubbed  # type: ignore[no-any-return]


def init_sentry(component: str) -> bool:
    """Start error reporting for this process. Returns whether it was switched on.

    ``component`` distinguishes the API from the worker in the Sentry UI; they fail in different
    ways and are usually being read by someone looking at one or the other.
    """
    global _initialised
    if _initialised:
        return True

    settings = get_settings()
    if not settings.sentry_dsn:
        return False

    try:
        import sentry_sdk
    except ImportError:  # pragma: no cover - the extra is not installed
        logger.warning("sentry_sdk_missing", detail="SENTRY_DSN is set but sentry-sdk is absent")
        return False

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        release=settings.sentry_release or None,
        # Sampled, because a beta does not need every trace and an unsampled tracer is a
        # meaningful share of request latency.
        traces_sample_rate=settings.sentry_traces_sample_rate,
        # Never on. Request bodies carry passwords and trade data.
        send_default_pii=False,
        max_request_body_size="never",
        before_send=_before_send,
        attach_stacktrace=True,
    )
    sentry_sdk.set_tag("component", component)

    _initialised = True
    logger.info("sentry_enabled", component=component, environment=settings.environment)
    return True


__all__ = ["init_sentry"]
