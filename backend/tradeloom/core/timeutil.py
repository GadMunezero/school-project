"""Timezone handling.

Everything is stored in UTC. Timezone names only matter at two boundaries:

* **Import** — broker CSVs carry local timestamps; we convert to UTC on the way in.
* **Analysis** — "performance by hour" and session buckets are meaningless in UTC for a trader in
  Chicago, so breakdowns are computed in the *account's* timezone.

Naive datetimes are always treated as being in the supplied timezone, never in the server's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from tradeloom.core.enums import AssetType, Timeframe, TradingSession

UTC = UTC

#: Session windows expressed in the *account* timezone, as [start, end) local times.
SESSION_WINDOWS: dict[TradingSession, tuple[time, time]] = {
    TradingSession.ASIA: (time(0, 0), time(7, 0)),
    TradingSession.LONDON: (time(7, 0), time(12, 0)),
    TradingSession.NEW_YORK_AM: (time(12, 0), time(16, 0)),
    TradingSession.NEW_YORK_PM: (time(16, 0), time(21, 0)),
    TradingSession.OVERNIGHT: (time(21, 0), time(23, 59, 59, 999999)),
}


@dataclass(frozen=True, slots=True)
class SessionBoundary:
    """When a market's trading day starts, in the market's own timezone.

    Most markets open and close inside one calendar day, so their trading day *is* the local
    date. Futures and FX do not: a CME session opens at 18:00 New York the evening *before* the
    day it is named after, and runs through the next afternoon. Grouping those bars by local date
    splits one session in two — an evening fragment labelled with the wrong weekday, and a
    truncated remainder whose "open" is midnight rather than the real open.

    The timezone is part of the boundary rather than the caller's choice. A CME contract rolls at
    18:00 New York whether it is being viewed from Chicago or Tokyo, so the viewer's timezone must
    not move it.
    """

    opens_at: time
    timezone: str

    def session_date(self, value: datetime) -> date:
        """The trading day a timestamp belongs to.

        A bar at or after the open belongs to the *next* calendar day's session, which is the
        convention the exchanges use: the Monday session begins Sunday evening.
        """
        local = to_zone(value, self.timezone)
        if local.time() >= self.opens_at:
            return local.date() + timedelta(days=1)
        return local.date()


#: Trading-day boundaries by asset type, for the asset types that have one.
#:
#: Futures and FX roll in the New York evening. Everything else — equities, ETFs, indices,
#: options, CFDs — trades inside a single calendar day, and crypto has no close at all, so for
#: those the local date is the trading day and no entry belongs here.
MARKET_BOUNDARIES: dict[AssetType, SessionBoundary] = {
    # CME and ICE: the trading day opens 18:00 ET and runs to 17:00 ET the next afternoon.
    AssetType.FUTURES: SessionBoundary(time(18, 0), "America/New_York"),
    # Interbank FX rolls at 17:00 ET, which is also where the daily close is struck.
    AssetType.FOREX: SessionBoundary(time(17, 0), "America/New_York"),
    # CFDs track their underlying's hours; the common ones are FX and index futures.
    AssetType.CFD: SessionBoundary(time(17, 0), "America/New_York"),
}


def boundary_for(asset_type: AssetType | str | None) -> SessionBoundary | None:
    """The trading-day boundary for an asset type, or ``None`` when the local date is right."""
    if asset_type is None:
        return None
    try:
        resolved = AssetType(asset_type)
    except ValueError:
        return None
    return MARKET_BOUNDARIES.get(resolved)


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def get_zone(name: str | None) -> ZoneInfo:
    if not name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return ZoneInfo("UTC")


def is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False
    return True


def ensure_aware(value: datetime, *, assume: str | ZoneInfo = UTC) -> datetime:
    """Attach a timezone to a naive datetime, then normalise to UTC."""
    zone = assume if isinstance(assume, (ZoneInfo, timezone)) else get_zone(str(assume))
    if value.tzinfo is None:
        value = value.replace(tzinfo=zone)
    return value.astimezone(UTC)


def to_zone(value: datetime, zone_name: str | None) -> datetime:
    return ensure_aware(value).astimezone(get_zone(zone_name))


def start_of_day(value: datetime, zone_name: str | None = None) -> datetime:
    local = to_zone(value, zone_name)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)


def local_date(value: datetime, zone_name: str | None = None) -> date:
    return to_zone(value, zone_name).date()


def trading_day(
    value: datetime, asset_type: AssetType | str | None, zone_name: str | None = None
) -> date:
    """Which trading day a moment belongs to for a given market.

    The market's own boundary wins where it has one, because that is what the day *is*: a futures
    trade closed at 19:00 New York on Monday belongs to Tuesday's session, and filing it under
    Monday would put it in a day that had already settled. Everything else falls back to the local
    date in ``zone_name`` — the account's timezone for a trade, the market's for generated data.
    """
    boundary = boundary_for(asset_type)
    if boundary is not None:
        return boundary.session_date(value)
    return local_date(value, zone_name)


def session_for(value: datetime, zone_name: str | None = None) -> TradingSession:
    """Bucket a timestamp into a trading session using the account's local clock."""
    local_time = to_zone(value, zone_name).time()
    for session, (start, end) in SESSION_WINDOWS.items():
        if start <= local_time < end:
            return session
    return TradingSession.OVERNIGHT


def floor_to_timeframe(value: datetime, timeframe: Timeframe) -> datetime:
    """Align a timestamp to the opening boundary of its bar.

    Weekly bars anchor to Monday 00:00 UTC; every other timeframe divides the day evenly, so
    epoch-modulo alignment is exact.
    """
    aware = ensure_aware(value)
    if timeframe is Timeframe.W1:
        monday = aware - timedelta(days=aware.weekday())
        return monday.replace(hour=0, minute=0, second=0, microsecond=0)
    seconds = timeframe.seconds
    epoch_seconds = int(aware.timestamp())
    return datetime.fromtimestamp(epoch_seconds - (epoch_seconds % seconds), tz=UTC)


def next_bar_open(value: datetime, timeframe: Timeframe) -> datetime:
    return floor_to_timeframe(value, timeframe) + timedelta(seconds=timeframe.seconds)


def iso(value: datetime | None) -> str | None:
    return None if value is None else ensure_aware(value).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> datetime:
    text = value.strip().replace("Z", "+00:00")
    return ensure_aware(datetime.fromisoformat(text))


def holding_seconds(entry: datetime, exit_: datetime | None) -> int | None:
    if exit_ is None:
        return None
    return int((ensure_aware(exit_) - ensure_aware(entry)).total_seconds())


__all__ = [
    "MARKET_BOUNDARIES",
    "SESSION_WINDOWS",
    "UTC",
    "SessionBoundary",
    "boundary_for",
    "ensure_aware",
    "floor_to_timeframe",
    "get_zone",
    "holding_seconds",
    "is_valid_timezone",
    "iso",
    "local_date",
    "next_bar_open",
    "parse_iso",
    "session_for",
    "start_of_day",
    "to_zone",
    "trading_day",
    "utcnow",
]
