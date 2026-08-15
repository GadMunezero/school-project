"""Password hashing, opaque token generation, and constant-time comparison.

Design notes:

* Passwords use Argon2id with parameters from settings (defaults follow the OWASP 2024 cheat
  sheet: 64 MiB, t=3, p=2). ``needs_rehash`` lets us transparently upgrade cost parameters on the
  next successful sign-in.
* Session and reset tokens are 256-bit random values. Only their SHA-256 digest is stored, so a
  database dump cannot be replayed as a live session.
* :func:`verify_password` is deliberately slow even for unknown accounts — :func:`dummy_verify`
  burns an equivalent hash so signup/login timing does not reveal whether an email exists.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type

from tradeloom.core.config import get_settings

TOKEN_BYTES = 32
#: A precomputed hash of a value nobody can supply, used to equalise login timing.
_DUMMY_PASSWORD = "tradeloom-timing-equaliser"

_hasher: PasswordHasher | None = None
_dummy_hash: str | None = None


def _get_hasher() -> PasswordHasher:
    global _hasher
    if _hasher is None:
        settings = get_settings()
        _hasher = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_cost_kib,
            parallelism=settings.argon2_parallelism,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
    return _hasher


def reset_hasher_cache() -> None:
    """Tests lower the Argon2 cost; call this after changing settings."""
    global _hasher, _dummy_hash
    _hasher = None
    _dummy_hash = None


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("password must not be empty")
    return _get_hasher().hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _get_hasher().verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def dummy_verify() -> None:
    """Spend the same CPU as a real verification for accounts that do not exist."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = _get_hasher().hash(_DUMMY_PASSWORD)
    try:
        _get_hasher().verify(_dummy_hash, "definitely-not-the-password")
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return


def needs_rehash(password_hash: str) -> bool:
    try:
        return _get_hasher().check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def generate_token() -> str:
    """A URL-safe opaque token. Shown to the caller once; only its digest is persisted."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """SHA-256 is correct here: the input already has 256 bits of entropy, so stretching adds
    nothing while making session lookups needlessly expensive."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))


def sign_value(value: str) -> str:
    """HMAC a value with the application secret (used for CSRF double-submit tokens)."""
    settings = get_settings()
    digest = hmac.new(settings.secret_key.encode("utf-8"), value.encode("utf-8"), hashlib.sha256)
    return digest.hexdigest()


def checksum_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "checksum_bytes",
    "constant_time_equals",
    "dummy_verify",
    "generate_token",
    "hash_password",
    "hash_token",
    "needs_rehash",
    "reset_hasher_cache",
    "sign_value",
    "verify_password",
]
