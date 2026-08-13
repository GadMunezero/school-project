"""Domain enumerations.

These are the single source of truth for every closed vocabulary in the product. The TypeScript
mirror in `frontend/src/lib/contracts.ts` is generated from this module by
`scripts/gen_contracts.py`; run it after changing anything here.
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    """Platform-level role. Distinct from per-organization membership roles."""

    USER = "user"
    SUPPORT = "support"
    ADMIN = "admin"


class UserStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class MemberRole(StrEnum):
    """Role inside an organization. Ordered from least to most privileged."""

    VIEWER = "viewer"
    MEMBER = "member"
    MANAGER = "manager"
    OWNER = "owner"

    @property
    def rank(self) -> int:
        return _MEMBER_ROLE_RANK[self]

    def at_least(self, other: MemberRole) -> bool:
        return self.rank >= other.rank


_MEMBER_ROLE_RANK: dict[MemberRole, int] = {
    MemberRole.VIEWER: 0,
    MemberRole.MEMBER: 1,
    MemberRole.MANAGER: 2,
    MemberRole.OWNER: 3,
}


class MemberStatus(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    REMOVED = "removed"


class AccountType(StrEnum):
    LIVE = "live"
    PAPER = "paper"
    DEMO = "demo"
    PROP_EVALUATION = "prop_evaluation"
    PROP_FUNDED = "prop_funded"
    BACKTEST = "backtest"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    CLOSED = "closed"


class AssetType(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    FUTURES = "futures"
    OPTION = "option"
    FOREX = "forex"
    CRYPTO = "crypto"
    CFD = "cfd"
    INDEX = "index"


class Direction(StrEnum):
    LONG = "long"
    SHORT = "short"

    @property
    def sign(self) -> int:
        return 1 if self is Direction.LONG else -1

    @property
    def opposite(self) -> Direction:
        return Direction.SHORT if self is Direction.LONG else Direction.LONG


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"

    @property
    def sign(self) -> int:
        return 1 if self is OrderSide.BUY else -1

    @property
    def opposite(self) -> OrderSide:
        return OrderSide.SELL if self is OrderSide.BUY else OrderSide.BUY


class OrderType(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TimeInForce(StrEnum):
    GTC = "gtc"
    DAY = "day"
    IOC = "ioc"
    FOK = "fok"


class OrderStatus(StrEnum):
    PENDING = "pending"
    WORKING = "working"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class TradeStatus(StrEnum):
    OPEN = "open"
    PARTIALLY_CLOSED = "partially_closed"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class TradeSource(StrEnum):
    MANUAL = "manual"
    IMPORT = "import"
    BROKER_SYNC = "broker_sync"
    BACKTEST = "backtest"
    REPLAY = "replay"


class PositionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class TradingSession(StrEnum):
    """Session buckets are derived from the account timezone, not the server timezone."""

    ASIA = "asia"
    LONDON = "london"
    NEW_YORK_AM = "new_york_am"
    NEW_YORK_PM = "new_york_pm"
    OVERNIGHT = "overnight"


class Timeframe(StrEnum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"

    @property
    def seconds(self) -> int:
        return _TIMEFRAME_SECONDS[self]


_TIMEFRAME_SECONDS: dict[Timeframe, int] = {
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.M30: 1_800,
    Timeframe.H1: 3_600,
    Timeframe.H4: 14_400,
    Timeframe.D1: 86_400,
    Timeframe.W1: 604_800,
}


class CommissionModelType(StrEnum):
    NONE = "none"
    PER_SHARE = "per_share"
    PER_CONTRACT = "per_contract"
    PER_TRADE = "per_trade"
    PERCENT_OF_NOTIONAL = "percent_of_notional"
    TIERED_PERCENT = "tiered_percent"


class SlippageModelType(StrEnum):
    NONE = "none"
    FIXED_TICKS = "fixed_ticks"
    PERCENT_OF_PRICE = "percent_of_price"
    SPREAD_FRACTION = "spread_fraction"


class ExecutionModelType(StrEnum):
    """When an order created from a bar-N signal is allowed to fill."""

    NEXT_BAR_OPEN = "next_bar_open"
    CURRENT_BAR_CLOSE = "current_bar_close"


class PositionSizingType(StrEnum):
    FIXED_QUANTITY = "fixed_quantity"
    FIXED_NOTIONAL = "fixed_notional"
    FIXED_RISK_AMOUNT = "fixed_risk_amount"
    PERCENT_OF_EQUITY = "percent_of_equity"
    PERCENT_RISK = "percent_risk"


class IntrabarPriority(StrEnum):
    """Which level is assumed to be hit first when a single bar spans both."""

    STOP_FIRST = "stop_first"
    TARGET_FIRST = "target_first"
    WORST_CASE = "worst_case"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}


class ImportStatus(StrEnum):
    UPLOADED = "uploaded"
    MAPPING = "mapping"
    VALIDATING = "validating"
    PREVIEW = "preview"
    COMMITTING = "committing"
    COMPLETED = "completed"
    FAILED = "failed"
    REVERTED = "reverted"


class ImportRowStatus(StrEnum):
    PENDING = "pending"
    VALID = "valid"
    INVALID = "invalid"
    DUPLICATE = "duplicate"
    IMPORTED = "imported"
    SKIPPED = "skipped"


class BacktestRunMode(StrEnum):
    BACKTEST = "backtest"
    REPLAY = "replay"


class StrategyStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class StrategyKind(StrEnum):
    """Where the strategy's logic comes from."""

    BUILTIN = "builtin"
    JOURNAL_ONLY = "journal_only"


class ParameterType(StrEnum):
    INTEGER = "integer"
    DECIMAL = "decimal"
    BOOLEAN = "boolean"
    STRING = "string"
    CHOICE = "choice"


class SubscriptionPlan(StrEnum):
    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    TRIALING = "trialing"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    INCOMPLETE = "incomplete"
    UNPAID = "unpaid"


class NotificationKind(StrEnum):
    IMPORT_COMPLETED = "import_completed"
    IMPORT_FAILED = "import_failed"
    BACKTEST_COMPLETED = "backtest_completed"
    BACKTEST_FAILED = "backtest_failed"
    SUBSCRIPTION_UPDATED = "subscription_updated"
    SUBSCRIPTION_PAYMENT_FAILED = "subscription_payment_failed"
    EXPORT_READY = "export_ready"
    ACCOUNT_SECURITY = "account_security"


class NotificationSeverity(StrEnum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class AuditAction(StrEnum):
    LOGIN_SUCCEEDED = "login_succeeded"
    LOGIN_FAILED = "login_failed"
    LOGOUT = "logout"
    SIGNUP = "signup"
    PASSWORD_CHANGED = "password_changed"
    PASSWORD_RESET_REQUESTED = "password_reset_requested"
    PASSWORD_RESET_COMPLETED = "password_reset_completed"
    EMAIL_VERIFIED = "email_verified"
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    IMPORT_COMMITTED = "import_committed"
    IMPORT_REVERTED = "import_reverted"
    BACKTEST_SUBMITTED = "backtest_submitted"
    EXPORT_REQUESTED = "export_requested"
    SUBSCRIPTION_CHANGED = "subscription_changed"
    ADMIN_ACTION = "admin_action"
    ACCOUNT_DELETION_REQUESTED = "account_deletion_requested"


class CandleQualityIssue(StrEnum):
    DUPLICATE_TIMESTAMP = "duplicate_timestamp"
    OUT_OF_ORDER = "out_of_order"
    MISSING_BAR = "missing_bar"
    INVALID_OHLC = "invalid_ohlc"
    NON_POSITIVE_PRICE = "non_positive_price"
    NEGATIVE_VOLUME = "negative_volume"


__all__ = [
    "AccountStatus",
    "AccountType",
    "AssetType",
    "AuditAction",
    "BacktestRunMode",
    "CandleQualityIssue",
    "CommissionModelType",
    "Direction",
    "ExecutionModelType",
    "ImportRowStatus",
    "ImportStatus",
    "IntrabarPriority",
    "JobStatus",
    "MemberRole",
    "MemberStatus",
    "NotificationKind",
    "NotificationSeverity",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "ParameterType",
    "PositionSizingType",
    "PositionStatus",
    "SlippageModelType",
    "StrategyKind",
    "StrategyStatus",
    "SubscriptionPlan",
    "SubscriptionStatus",
    "TimeInForce",
    "Timeframe",
    "TradeSource",
    "TradeStatus",
    "TradingSession",
    "UserRole",
    "UserStatus",
]
