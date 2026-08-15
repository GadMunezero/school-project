"""Test harness.

Runs against SQLite so `pytest` needs no external services. The portable column types in
``tradeloom.db.types`` mean the semantics under test (UUID identity, aware datetimes, exact
Decimals) are identical to production PostgreSQL.

Each test gets a fresh database file. That is slower than a shared connection with rollbacks, but
it removes a whole class of order-dependent flakiness, and the suite is fast enough that the
trade is worth making.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

# Settings are read at import time by several modules, so the environment must be set first.
os.environ.setdefault("TRADELOOM_ENV", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-used-in-production-0123456789abcdef")
os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
os.environ.setdefault("EMAIL_ENABLED", "false")
os.environ.setdefault("STRIPE_ENABLED", "false")
os.environ.setdefault("COOKIE_SECURE", "false")
# Argon2 at production cost would dominate the suite's runtime; the algorithm is unchanged.
os.environ.setdefault("ARGON2_TIME_COST", "1")
os.environ.setdefault("ARGON2_MEMORY_COST_KIB", "8192")
os.environ.setdefault("ARGON2_PARALLELISM", "1")

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tradeloom.core.config import get_settings, reset_settings_cache
from tradeloom.core.security import reset_hasher_cache
from tradeloom.db.base import Base
from tradeloom.db.session import set_session_factory
from tradeloom.models import *  # noqa: F403  (registers every table)
from tradeloom.services.storage import InMemoryStorage, set_storage

pytest_plugins = ["anyio"]


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_caches() -> AsyncIterator[None]:
    reset_settings_cache()
    reset_hasher_cache()
    set_storage(InMemoryStorage())
    yield
    set_storage(None)


@pytest.fixture
def invite_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the test against a closed signup.

    Set through the environment and the settings cache cleared, so the application reads it the
    same way a deployment would rather than through a patched attribute.
    """
    monkeypatch.setenv("SIGNUP_MODE", "invite")
    reset_settings_cache()


@pytest.fixture
async def engine(tmp_path: Path):  # type: ignore[no-untyped-def]
    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    os.environ["DATABASE_URL"] = url
    reset_settings_cache()
    assert get_settings().database_url == url

    engine = create_async_engine(url, future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def session_factory(engine):  # type: ignore[no-untyped-def]
    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    set_session_factory(factory)
    try:
        yield factory
    finally:
        set_session_factory(None)


@pytest.fixture
async def db(session_factory):  # type: ignore[no-untyped-def]
    """A session for direct service-level tests."""
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
async def app(session_factory):  # type: ignore[no-untyped-def]
    from tradeloom.main import create_app

    return create_app()


@pytest.fixture
async def client(app) -> AsyncIterator[httpx.AsyncClient]:  # type: ignore[no-untyped-def]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", follow_redirects=True
    ) as client:
        yield client


class ApiUser:
    """A signed-in API client that carries its session cookie and CSRF token."""

    def __init__(self, client: httpx.AsyncClient, email: str, password: str) -> None:
        self.client = client
        self.email = email
        self.password = password
        self.csrf_token: str = ""
        self.organization_id: str = ""
        self.user_id: str = ""
        self._cookies: dict[str, str] = {}

    def _headers(self, method: str) -> dict[str, str]:
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            return {"X-CSRF-Token": self.csrf_token}
        return {}

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:  # type: ignore[no-untyped-def]
        headers = {**self._headers(method), **kwargs.pop("headers", {})}
        response = await self.client.request(
            method, url, headers=headers, cookies=self._cookies, **kwargs
        )
        for name, value in response.cookies.items():
            self._cookies[name] = value
        return response

    async def get(self, url: str, **kwargs):  # type: ignore[no-untyped-def]
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs):  # type: ignore[no-untyped-def]
        return await self.request("POST", url, **kwargs)

    async def patch(self, url: str, **kwargs):  # type: ignore[no-untyped-def]
        return await self.request("PATCH", url, **kwargs)

    async def delete(self, url: str, **kwargs):  # type: ignore[no-untyped-def]
        return await self.request("DELETE", url, **kwargs)

    def _absorb(self, response: httpx.Response) -> None:
        payload = response.json()["data"]
        self.csrf_token = payload["csrf_token"]
        self.user_id = payload["user"]["id"]
        if payload.get("active_organization"):
            self.organization_id = payload["active_organization"]["id"]
        for name, value in response.cookies.items():
            self._cookies[name] = value


async def signup(
    client: httpx.AsyncClient, email: str, password: str = "CorrectHorse!7392"
) -> ApiUser:
    user = ApiUser(client, email, password)
    response = await client.post(
        "/api/v1/auth/signup",
        json={
            "email": email,
            "password": password,
            "full_name": "Test Trader",
            "timezone": "UTC",
            "accepted_terms": True,
        },
    )
    assert response.status_code == 201, response.text
    user._absorb(response)
    return user


@pytest.fixture
async def alice(client: httpx.AsyncClient) -> ApiUser:
    return await signup(client, "alice@example.com")


@pytest.fixture
async def bob(client: httpx.AsyncClient) -> ApiUser:
    return await signup(client, "bob@example.com")


async def create_account(user: ApiUser, name: str = "Main", **overrides) -> dict:  # type: ignore[no-untyped-def]
    payload = {
        "name": name,
        "broker": "Test Broker",
        "account_type": "live",
        "currency": "USD",
        "initial_balance": "100000",
        "leverage": "1",
        "timezone": "UTC",
        **overrides,
    }
    response = await user.post("/api/v1/accounts", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def create_trade(
    user: ApiUser,
    account_id: str,
    *,
    symbol: str = "NVLX",
    direction: str = "long",
    entry_price: str = "100",
    exit_price: str | None = "110",
    quantity: str = "100",
    **overrides,
) -> dict:  # type: ignore[no-untyped-def]
    payload: dict = {
        "account_id": account_id,
        "symbol": symbol,
        "asset_type": "equity",
        "direction": direction,
        "entry_timestamp": "2024-05-06T14:30:00Z",
        "entry_price": entry_price,
        "quantity": quantity,
        **overrides,
    }
    if exit_price is not None:
        payload.setdefault("exit_timestamp", "2024-05-06T16:00:00Z")
        payload["exit_price"] = exit_price

    response = await user.post("/api/v1/trades", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["data"][0]


async def upgrade_to_pro(db, organization_id: str) -> None:  # type: ignore[no-untyped-def]
    """Put a workspace on the Pro plan.

    Mirrors what an administrator override does. Tests must not be able to grant themselves
    entitlements through the tenant API — that they cannot is exactly what the billing tests
    assert — so this writes the subscription row directly.
    """
    import uuid as uuid_module

    from sqlalchemy import select

    from tradeloom.core.enums import SubscriptionPlan, SubscriptionStatus
    from tradeloom.models.platform import Subscription

    result = await db.execute(
        select(Subscription).where(
            Subscription.organization_id == uuid_module.UUID(organization_id)
        )
    )
    subscription = result.scalar_one_or_none()
    if subscription is None:
        subscription = Subscription(organization_id=uuid_module.UUID(organization_id))
        db.add(subscription)
    subscription.plan = SubscriptionPlan.PRO
    subscription.status = SubscriptionStatus.ACTIVE
    await db.commit()


@pytest.fixture
async def pro_alice(alice, db):  # type: ignore[no-untyped-def]
    """Alice, on a plan that includes backtesting, replay and comparison."""
    await upgrade_to_pro(db, alice.organization_id)
    return alice


@pytest.fixture
async def pro_bob(bob, db):  # type: ignore[no-untyped-def]
    await upgrade_to_pro(db, bob.organization_id)
    return bob


__all__ = [
    "ApiUser",
    "create_account",
    "create_trade",
    "signup",
    "upgrade_to_pro",
]


async def promote_to_admin(db, email: str) -> None:  # type: ignore[no-untyped-def]
    """Give an account the platform staff role.

    Written directly, for the same reason `upgrade_to_pro` is: no tenant API grants this, and that
    it cannot is what the admin tests assert.
    """
    from sqlalchemy import select

    from tradeloom.core.enums import UserRole
    from tradeloom.models.identity import User

    result = await db.execute(select(User).where(User.email == email.lower()))
    user = result.scalar_one()
    user.role = UserRole.ADMIN
    await db.commit()


@pytest.fixture
async def staff(alice, db) -> ApiUser:  # type: ignore[no-untyped-def]
    """Alice, holding the platform administrator role."""
    await promote_to_admin(db, alice.email)
    return alice
