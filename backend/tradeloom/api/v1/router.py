"""Aggregate router for ``/api/v1``."""

from __future__ import annotations

from fastapi import APIRouter

from tradeloom.api.v1.routers import (
    accounts,
    admin,
    analytics,
    auth,
    backtests,
    billing,
    files,
    imports,
    instruments,
    journal,
    market_data,
    notifications,
    orders,
    organizations,
    positions,
    replay,
    reports,
    search,
    setups,
    strategies,
    tags,
    trades,
    users,
)

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(organizations.router)
api_router.include_router(accounts.router)
api_router.include_router(instruments.router)
api_router.include_router(market_data.router)
api_router.include_router(trades.router)
api_router.include_router(orders.router)
api_router.include_router(positions.router)
api_router.include_router(strategies.router)
api_router.include_router(setups.router)
api_router.include_router(tags.router)
api_router.include_router(journal.router)
api_router.include_router(imports.router)
api_router.include_router(backtests.router)
api_router.include_router(replay.router)
api_router.include_router(analytics.router)
api_router.include_router(reports.router)
api_router.include_router(files.router)
api_router.include_router(search.router)
api_router.include_router(notifications.router)
api_router.include_router(billing.router)
api_router.include_router(admin.router)

__all__ = ["api_router"]
