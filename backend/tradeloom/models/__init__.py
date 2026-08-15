"""SQLAlchemy models.

Importing this package registers every table on ``Base.metadata`` — Alembic's ``env.py`` and the
test harness both rely on that, so new model modules must be added here.
"""

from tradeloom.db.base import Base
from tradeloom.models.account import Account, AccountSnapshot, CashTransaction
from tradeloom.models.backtest import (
    Backtest,
    BacktestOrder,
    BacktestRun,
    BacktestTrade,
    DrawdownPoint,
    EquityPoint,
    ReplaySession,
)
from tradeloom.models.file import FileObject
from tradeloom.models.identity import EmailToken, LoginAttempt, OAuthAccount, User, UserSession
from tradeloom.models.imports import Import, ImportRow, ImportTemplate
from tradeloom.models.instrument import Instrument, InstrumentAlias
from tradeloom.models.journal import JournalEntry, Screenshot
from tradeloom.models.market_data import MarketData, MarketDataCoverage, MarketDataSource
from tradeloom.models.organization import (
    Organization,
    OrganizationMember,
    Permission,
    Role,
    RolePermission,
)
from tradeloom.models.platform import (
    AnalyticsSnapshot,
    AuditLog,
    InviteCode,
    InviteRedemption,
    JobRecord,
    Notification,
    Subscription,
    SubscriptionEvent,
)
from tradeloom.models.strategy import Setup, Strategy, StrategyParameter, StrategyVersion, Tag
from tradeloom.models.trading import Order, Position, Trade, TradeTag

__all__ = [
    "Account",
    "AccountSnapshot",
    "AnalyticsSnapshot",
    "AuditLog",
    "Backtest",
    "BacktestOrder",
    "BacktestRun",
    "BacktestTrade",
    "Base",
    "CashTransaction",
    "DrawdownPoint",
    "EmailToken",
    "EquityPoint",
    "FileObject",
    "Import",
    "ImportRow",
    "ImportTemplate",
    "Instrument",
    "InstrumentAlias",
    "InviteCode",
    "InviteRedemption",
    "JobRecord",
    "JournalEntry",
    "LoginAttempt",
    "MarketData",
    "MarketDataCoverage",
    "MarketDataSource",
    "Notification",
    "OAuthAccount",
    "Order",
    "Organization",
    "OrganizationMember",
    "Permission",
    "Position",
    "ReplaySession",
    "Role",
    "RolePermission",
    "Screenshot",
    "Setup",
    "Strategy",
    "StrategyParameter",
    "StrategyVersion",
    "Subscription",
    "SubscriptionEvent",
    "Tag",
    "Trade",
    "TradeTag",
    "User",
    "UserSession",
]
