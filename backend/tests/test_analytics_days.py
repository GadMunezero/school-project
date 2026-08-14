"""Which day a trade counts toward.

A futures trade closed at 19:00 New York belongs to the *next* session — the day it was filed
under has already settled. Booking it by the calendar moves P&L into a day that was over, which
shows up as an equity curve that disagrees with the calendar heatmap and, for anyone trading a
funded account with a daily loss limit, as a number that is simply not the one their firm sees.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from tradeloom.core.enums import AssetType, Direction, TradeStatus
from tradeloom.core.timeutil import trading_day
from tradeloom.models.trading import Trade
from tradeloom.services.analytics import AnalyticsService

ZONE = "America/New_York"


def trade(
    *,
    asset_type: AssetType,
    exit_at: datetime,
    net_pnl: str = "100",
    symbol: str = "TEST",
) -> Trade:
    """An in-memory closed trade. Never added to a session — these tests touch no database."""
    return Trade(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        symbol=symbol,
        asset_type=asset_type,
        direction=Direction.LONG,
        status=TradeStatus.CLOSED,
        entry_timestamp=exit_at,
        exit_timestamp=exit_at,
        net_pnl=Decimal(net_pnl),
    )


class TestTradingDay:
    #: Monday 19:00 New York — after the futures open, before midnight.
    MONDAY_EVENING = datetime(2026, 3, 16, 23, 0, tzinfo=UTC)

    def test_a_futures_trade_after_the_open_counts_toward_the_next_session(self) -> None:
        assert trading_day(self.MONDAY_EVENING, AssetType.FUTURES, ZONE).isoformat() == "2026-03-17"

    def test_the_same_moment_is_still_monday_for_an_equity(self) -> None:
        """Equities have no evening roll; 19:00 is after-hours on the same day."""
        assert trading_day(self.MONDAY_EVENING, AssetType.EQUITY, ZONE).isoformat() == "2026-03-16"

    def test_forex_rolls_an_hour_earlier_than_futures(self) -> None:
        five_thirty = datetime(2026, 3, 16, 21, 30, tzinfo=UTC)  # Monday 17:30 New York
        assert trading_day(five_thirty, AssetType.FOREX, ZONE).isoformat() == "2026-03-17"
        assert trading_day(five_thirty, AssetType.FUTURES, ZONE).isoformat() == "2026-03-16"

    def test_a_market_without_a_roll_uses_the_account_timezone(self) -> None:
        """The account's clock still decides the day where the market does not."""
        just_after_midnight_utc = datetime(2026, 3, 17, 2, 0, tzinfo=UTC)  # 22:00 Monday in NY

        assert trading_day(just_after_midnight_utc, AssetType.EQUITY, ZONE).isoformat() == (
            "2026-03-16"
        )
        assert trading_day(just_after_midnight_utc, AssetType.EQUITY, "UTC").isoformat() == (
            "2026-03-17"
        )

    def test_crypto_never_rolls_because_it_never_closes(self) -> None:
        assert trading_day(self.MONDAY_EVENING, AssetType.CRYPTO, ZONE).isoformat() == "2026-03-16"


class TestCalendarBreakdown:
    """The dashboard heatmap buckets by the same rule."""

    def _service(self) -> AnalyticsService:
        return AnalyticsService(session=None, organization_id=uuid.uuid4())  # type: ignore[arg-type]

    def test_an_evening_futures_trade_lands_on_the_next_day(self) -> None:
        rows = self._service()._calendar(
            [
                trade(
                    asset_type=AssetType.FUTURES,
                    exit_at=datetime(2026, 3, 16, 23, 0, tzinfo=UTC),  # Mon 19:00 NY
                    net_pnl="250",
                )
            ],
            ZONE,
        )

        assert [row["date"] for row in rows] == ["2026-03-17"]
        assert rows[0]["net_pnl"] == "250"

    def test_two_markets_at_one_moment_land_on_their_own_days(self) -> None:
        """Not a bug: at 19:00 New York the equity day is over and the futures day has begun."""
        moment = datetime(2026, 3, 16, 23, 0, tzinfo=UTC)
        rows = self._service()._calendar(
            [
                trade(asset_type=AssetType.EQUITY, exit_at=moment, net_pnl="100", symbol="NVLX"),
                trade(asset_type=AssetType.FUTURES, exit_at=moment, net_pnl="400", symbol="MQ1"),
            ],
            ZONE,
        )

        assert {row["date"]: row["net_pnl"] for row in rows} == {
            "2026-03-16": "100",
            "2026-03-17": "400",
        }

    def test_trades_in_one_session_across_midnight_share_a_day(self) -> None:
        """The point of the whole exercise: one session is one bucket."""
        rows = self._service()._calendar(
            [
                # Monday 19:00 NY and Tuesday 10:00 NY are the same futures session.
                trade(
                    asset_type=AssetType.FUTURES,
                    exit_at=datetime(2026, 3, 16, 23, 0, tzinfo=UTC),
                    net_pnl="300",
                ),
                trade(
                    asset_type=AssetType.FUTURES,
                    exit_at=datetime(2026, 3, 17, 14, 0, tzinfo=UTC),
                    net_pnl="-100",
                ),
            ],
            ZONE,
        )

        assert len(rows) == 1
        assert rows[0]["date"] == "2026-03-17"
        assert rows[0]["net_pnl"] == "200"
        assert rows[0]["trades"] == 2


class TestEquityCurveDays:
    def test_each_sample_carries_the_trading_day_of_its_trade(self) -> None:
        """Daily returns group on this, so it has to agree with the calendar."""
        service = AnalyticsService(session=None, organization_id=uuid.uuid4())  # type: ignore[arg-type]
        rows = [
            trade(
                asset_type=AssetType.FUTURES,
                exit_at=datetime(2026, 3, 16, 23, 0, tzinfo=UTC),
                net_pnl="300",
            )
        ]

        samples = service._build_equity_curve(rows, Decimal("100000"), ZONE)

        assert [s.trading_day.isoformat() for s in samples if s.trading_day] == [
            "2026-03-17",
            "2026-03-17",
        ]

    def test_an_equity_trade_keeps_the_calendar_day(self) -> None:
        service = AnalyticsService(session=None, organization_id=uuid.uuid4())  # type: ignore[arg-type]
        rows = [
            trade(
                asset_type=AssetType.EQUITY,
                exit_at=datetime(2026, 3, 16, 23, 0, tzinfo=UTC),
                net_pnl="300",
            )
        ]

        samples = service._build_equity_curve(rows, Decimal("100000"), ZONE)

        assert {s.trading_day.isoformat() for s in samples if s.trading_day} == {"2026-03-16"}
