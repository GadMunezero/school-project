"""Session cookie handling.

Two cookies, deliberately different:

* the **session cookie** is ``HttpOnly`` — JavaScript must never be able to read the credential;
* the **CSRF cookie** is readable by JavaScript, because the double-submit pattern requires the
  client to copy its value into the ``X-CSRF-Token`` header. Possession of the header proves the
  request came from our own origin's script rather than a cross-site form post.

``SameSite=Lax`` blocks the cross-site POST case outright; the CSRF token covers the remainder
(subdomain takeover, older browsers, and any future switch to ``SameSite=None``).
"""

from __future__ import annotations

from fastapi import Response

from tradeloom.core.config import Settings
from tradeloom.services.auth import IssuedSession


def set_session_cookies(response: Response, issued: IssuedSession, settings: Settings) -> None:
    max_age = settings.session_ttl_seconds
    domain = settings.cookie_domain or None

    response.set_cookie(
        key=settings.session_cookie_name,
        value=issued.token,
        max_age=max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
        domain=domain,
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=issued.csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
        domain=domain,
    )


def clear_session_cookies(response: Response, settings: Settings) -> None:
    domain = settings.cookie_domain or None
    for name in (settings.session_cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(key=name, path="/", domain=domain)


__all__ = ["clear_session_cookies", "set_session_cookies"]
