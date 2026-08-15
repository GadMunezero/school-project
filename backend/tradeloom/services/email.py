"""Transactional email.

The sender is an interface with two implementations: SMTP, and a logging sender used in
development and tests. Emails are rendered from small inline templates — the product sends five
kinds of message, which does not justify a template engine dependency.

Tokens appear in outbound links but are never written to the log: :class:`LoggingEmailSender`
records the recipient, subject and the link *path* with the token masked.
"""

from __future__ import annotations

import re
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from tradeloom.core.config import Settings, get_settings
from tradeloom.core.logging import get_logger

logger = get_logger(__name__)

_TOKEN_IN_URL = re.compile(r"(token=)[^&\s]+")


def _mask_tokens(text: str) -> str:
    return _TOKEN_IN_URL.sub(r"\1[redacted]", text)


@dataclass(slots=True)
class OutboundEmail:
    to: str
    subject: str
    text_body: str
    html_body: str | None = None


class EmailSender(Protocol):
    def send(self, message: OutboundEmail) -> None: ...


class LoggingEmailSender:
    """Development/test sender. Records that an email would have been sent, with tokens masked."""

    def __init__(self) -> None:
        self.sent: list[OutboundEmail] = []

    def send(self, message: OutboundEmail) -> None:
        self.sent.append(message)
        logger.info(
            "email_not_sent_logging_mode",
            to=message.to,
            subject=message.subject,
            preview=_mask_tokens(message.text_body[:200]),
        )


class SmtpEmailSender:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(self, message: OutboundEmail) -> None:
        email = EmailMessage()
        email["From"] = f"{self.settings.smtp_from_name} <{self.settings.smtp_from_email}>"
        email["To"] = message.to
        email["Subject"] = message.subject
        email.set_content(message.text_body)
        if message.html_body:
            email.add_alternative(message.html_body, subtype="html")

        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=15) as server:
            if self.settings.smtp_tls:
                server.starttls()
            if self.settings.smtp_user and self.settings.smtp_password:
                server.login(self.settings.smtp_user, self.settings.smtp_password)
            server.send_message(email)
        logger.info("email_sent", to=message.to, subject=message.subject)


def build_sender(settings: Settings | None = None) -> EmailSender:
    resolved = settings or get_settings()
    if not resolved.email_enabled or resolved.is_test:
        return LoggingEmailSender()
    return SmtpEmailSender(resolved)


# --- message templates ------------------------------------------------------


def _shell(title: str, body: str, cta_label: str | None, cta_url: str | None) -> str:
    button = ""
    if cta_label and cta_url:
        button = (
            f'<p style="margin:28px 0"><a href="{cta_url}" '
            'style="background:#1f6f5c;color:#fff;padding:12px 22px;border-radius:8px;'
            'text-decoration:none;font-weight:600;display:inline-block">'
            f"{cta_label}</a></p>"
        )
    return (
        '<div style="font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif;'
        'max-width:520px;margin:0 auto;color:#1a1f1d;line-height:1.6">'
        f'<h1 style="font-size:20px;margin:0 0 12px">{title}</h1>'
        f"<p>{body}</p>{button}"
        '<p style="color:#6b7671;font-size:13px;margin-top:32px">'
        "If you did not expect this email you can safely ignore it.</p>"
        '<p style="color:#6b7671;font-size:12px">— Tradeloom</p></div>'
    )


class EmailService:
    def __init__(self, sender: EmailSender | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.sender = sender or build_sender(self.settings)

    def _frontend(self, path: str) -> str:
        return f"{self.settings.frontend_url.rstrip('/')}{path}"

    def send_verification(self, to: str, name: str | None, token: str) -> None:
        url = self._frontend(f"/verify-email?token={token}")
        self.sender.send(
            OutboundEmail(
                to=to,
                subject="Confirm your Tradeloom email address",
                text_body=(
                    f"Hi {name or 'there'},\n\n"
                    "Confirm your email address to finish setting up Tradeloom:\n"
                    f"{url}\n\n"
                    f"This link expires in {self.settings.email_verify_ttl_seconds // 3600} hours."
                ),
                html_body=_shell(
                    "Confirm your email",
                    "Confirm your email address to finish setting up your Tradeloom workspace.",
                    "Confirm email",
                    url,
                ),
            )
        )

    def send_password_reset(self, to: str, name: str | None, token: str) -> None:
        url = self._frontend(f"/reset-password?token={token}")
        minutes = self.settings.password_reset_ttl_seconds // 60
        self.sender.send(
            OutboundEmail(
                to=to,
                subject="Reset your Tradeloom password",
                text_body=(
                    f"Hi {name or 'there'},\n\n"
                    f"Use this link to choose a new password:\n{url}\n\n"
                    f"The link expires in {minutes} minutes and can only be used once."
                ),
                html_body=_shell(
                    "Reset your password",
                    f"Choose a new password. This link expires in {minutes} minutes "
                    "and can only be used once.",
                    "Reset password",
                    url,
                ),
            )
        )

    def send_password_changed(self, to: str, name: str | None) -> None:
        self.sender.send(
            OutboundEmail(
                to=to,
                subject="Your Tradeloom password was changed",
                text_body=(
                    f"Hi {name or 'there'},\n\n"
                    "Your password was just changed and all other sessions were signed out. "
                    "If this wasn't you, reset your password immediately."
                ),
                html_body=_shell(
                    "Password changed",
                    "Your password was changed and all other sessions were signed out. "
                    "If this wasn't you, reset your password immediately.",
                    "Go to Tradeloom",
                    self._frontend("/login"),
                ),
            )
        )

    def send_job_notification(self, to: str, title: str, body: str, link_path: str | None) -> None:
        url = self._frontend(link_path) if link_path else None
        self.sender.send(
            OutboundEmail(
                to=to,
                subject=title,
                text_body=f"{body}\n\n{url or ''}".strip(),
                html_body=_shell(title, body, "Open in Tradeloom" if url else None, url),
            )
        )


__all__ = [
    "EmailSender",
    "EmailService",
    "LoggingEmailSender",
    "OutboundEmail",
    "SmtpEmailSender",
    "build_sender",
]
