"""Authentication and user-profile contracts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pydantic import EmailStr, Field, field_validator, model_validator

from tradeloom.core.enums import MemberRole, SubscriptionPlan, UserRole, UserStatus
from tradeloom.core.timeutil import is_valid_timezone
from tradeloom.schemas.common import TradeloomModel

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128

#: Rejected outright regardless of length or character classes.
_COMMON_PASSWORDS = frozenset(
    {
        "password123456",
        "qwertyuiop1234",
        "administrator1",
        "letmeinplease1",
        "tradingpasswor",
    }
)


def validate_password_strength(password: str) -> str:
    """Length-first policy with a small character-class floor.

    Long passphrases are the goal, so the length minimum does the heavy lifting; the class checks
    only stop trivially weak choices like a single repeated word.
    """
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ValueError(f"Password must be at most {PASSWORD_MAX_LENGTH} characters")
    if password.lower()[:14] in _COMMON_PASSWORDS:
        raise ValueError("This password is too common. Choose something less predictable.")
    classes = sum(
        bool(pattern.search(password))
        for pattern in (
            re.compile(r"[a-z]"),
            re.compile(r"[A-Z]"),
            re.compile(r"\d"),
            re.compile(r"[^\w\s]"),
        )
    )
    if classes < 3:
        raise ValueError(
            "Password must combine at least three of: lowercase, uppercase, digits, symbols"
        )
    if len(set(password)) < 6:
        raise ValueError("Password must use at least six distinct characters")
    return password


class SignupRequest(TradeloomModel):
    email: EmailStr
    password: str
    full_name: str = Field(min_length=1, max_length=160)
    #: Optional workspace name; defaults to "<first name>'s workspace".
    organization_name: str | None = Field(default=None, max_length=120)
    timezone: str = "UTC"
    accepted_terms: bool = True
    #: Required only when the deployment runs a closed signup (``SIGNUP_MODE=invite``).
    invite_code: str | None = Field(default=None, max_length=40)

    @field_validator("password")
    @classmethod
    def _password(cls, value: str) -> str:
        return validate_password_strength(value)

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str) -> str:
        if not is_valid_timezone(value):
            raise ValueError("Unknown timezone")
        return value

    @model_validator(mode="after")
    def _terms(self) -> SignupRequest:
        if not self.accepted_terms:
            raise ValueError("You must accept the terms to create an account")
        return self


class LoginRequest(TradeloomModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=PASSWORD_MAX_LENGTH)
    remember_me: bool = True


class PasswordResetRequest(TradeloomModel):
    email: EmailStr


class PasswordResetConfirm(TradeloomModel):
    token: str = Field(min_length=16, max_length=256)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _password(cls, value: str) -> str:
        return validate_password_strength(value)


class PasswordChangeRequest(TradeloomModel):
    current_password: str = Field(min_length=1)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _password(cls, value: str) -> str:
        return validate_password_strength(value)

    @model_validator(mode="after")
    def _different(self) -> PasswordChangeRequest:
        if self.current_password == self.new_password:
            raise ValueError("New password must differ from the current one")
        return self


class EmailVerificationRequest(TradeloomModel):
    token: str = Field(min_length=16, max_length=256)


class OrganizationSummary(TradeloomModel):
    id: Any
    name: str
    slug: str
    role: MemberRole
    is_personal: bool
    base_currency: str
    timezone: str
    plan: SubscriptionPlan = SubscriptionPlan.FREE


class UserProfile(TradeloomModel):
    id: Any
    email: str
    full_name: str | None
    display_name: str | None
    role: UserRole
    status: UserStatus
    email_verified: bool
    timezone: str
    locale: str
    theme: str
    avatar_url: str | None = None
    preferences: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    last_login_at: datetime | None = None


class SessionInfo(TradeloomModel):
    """What the client needs to render the shell. Everything here is server-derived."""

    user: UserProfile
    active_organization: OrganizationSummary | None
    organizations: list[OrganizationSummary] = Field(default_factory=list)
    #: Resolved server-side from the subscription; the client only renders it.
    entitlements: dict[str, Any] = Field(default_factory=dict)
    csrf_token: str
    expires_at: datetime


class UpdateProfileRequest(TradeloomModel):
    full_name: str | None = Field(default=None, max_length=160)
    display_name: str | None = Field(default=None, max_length=80)
    timezone: str | None = None
    locale: str | None = Field(default=None, max_length=16)
    theme: str | None = Field(default=None, pattern="^(light|dark|system)$")
    preferences: dict[str, Any] | None = None

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str | None) -> str | None:
        if value is not None and not is_valid_timezone(value):
            raise ValueError("Unknown timezone")
        return value


class SwitchOrganizationRequest(TradeloomModel):
    organization_id: Any


class ActiveSessionInfo(TradeloomModel):
    id: Any
    ip_address: str | None
    user_agent: str | None
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    is_current: bool


class AccountDeletionRequest(TradeloomModel):
    """Deleting an account is irreversible, so it requires the password and a typed confirmation."""

    password: str
    confirmation: str

    @field_validator("confirmation")
    @classmethod
    def _confirm(cls, value: str) -> str:
        if value.strip().upper() != "DELETE MY ACCOUNT":
            raise ValueError('Type "DELETE MY ACCOUNT" to confirm')
        return value


__all__ = [
    "PASSWORD_MAX_LENGTH",
    "PASSWORD_MIN_LENGTH",
    "AccountDeletionRequest",
    "ActiveSessionInfo",
    "EmailVerificationRequest",
    "LoginRequest",
    "OrganizationSummary",
    "PasswordChangeRequest",
    "PasswordResetConfirm",
    "PasswordResetRequest",
    "SessionInfo",
    "SignupRequest",
    "SwitchOrganizationRequest",
    "UpdateProfileRequest",
    "UserProfile",
    "validate_password_strength",
]
