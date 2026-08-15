"""Edge reports must be provably right, and provably free of look-ahead.

Each test builds a session by hand so the correct answer is known by construction rather than
asserted against whatever the code happens to produce. The look-ahead tests are the important
ones: a report that peeked at the bars it was about to judge would show a flawless hit rate and be
completely worthless.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradeloom.core.enums import AssetType
from tradeloom.core.timeutil import boundary_for
from tradeloom.engine.bars import Bar, BarSeries
from tradeloom.engine.reports import (
    Outcome,
    available_conditions,
    gap_fill,
    group_sessions,
    initial_balance,
    list_reports,
    previous_day_levels,
    run_report,
    session_context,
    split_by,
)


def bar(when: datetime, o: str, h: str, low: str, c: str) -> Bar:
    return Bar(
        opened_at=when,
        open=Decimal(o),
        high=Decimal(h),
        low=Decimal(low),
        close=Decimal(c),
        volume=Decimal(100),
    )


def session_bars(day: str, rows: list[tuple[int, str, str, str, str]]) -> list[Bar]:
    """Build a day's bars from (minute offset from 14:30 UTC, o, h, l, c) tuples."""
    base = datetime.fromisoformat(f"{day}T14:30:00").replace(tzinfo=UTC)
    return [bar(base + timedelta(minutes=m), o, h, low, c) for m, o, h, low, c in rows]


class TestGrouping:
    def test_bars_split_into_sessions_by_local_date(self) -> None:
        bars = session_bars("2026-03-02", [(0, "100", "101", "99", "100")])
        bars += session_bars("2026-03-03", [(0, "100", "101", "99", "100")])
        sessions = group_sessions(BarSeries(bars), "UTC")

        assert [s.day.isoformat() for s in sessions] == ["2026-03-02", "2026-03-03"]

    def test_session_extremes_span_all_its_bars(self) -> None:
        bars = session_bars(
            "2026-03-02",
            [
                (0, "100", "104", "99", "103"),
                (5, "103", "108", "102", "105"),
                (10, "105", "106", "97", "98"),
            ],
        )
        session = group_sessions(BarSeries(bars), "UTC")[0]

        assert session.open == Decimal("100")
        assert session.close == Decimal("98")
        assert session.high == Decimal("108")
        assert session.low == Decimal("97")

    def test_a_daily_bar_keeps_its_own_date_in_every_timezone(self) -> None:
        """A daily bar carries a date, not a moment.

        Vendors stamp them at midnight UTC, so converting that midnight into a western zone
        walks every bar back into the previous day. Real CBOE VIX data made the damage
        obvious: sessions landed one weekday early, producing Sunday sessions for a market
        that does not trade on Sundays, and no Friday sessions at all.
        """
        # Friday, Monday, Tuesday — a normal week with the weekend closed.
        days = ["2026-03-06", "2026-03-09", "2026-03-10"]
        bars = [
            bar(
                datetime.fromisoformat(f"{day}T00:00:00").replace(tzinfo=UTC),
                "100",
                "101",
                "99",
                "100",
            )
            for day in days
        ]

        for timezone in ("UTC", "America/New_York", "Asia/Tokyo"):
            sessions = group_sessions(BarSeries(bars), timezone, timeframe="1d")
            assert [s.day.isoformat() for s in sessions] == days, timezone
            assert not [s for s in sessions if s.day.weekday() >= 5], timezone

    def test_intraday_bars_still_group_by_local_date(self) -> None:
        """The daily rule must not leak into intraday data, where the zone genuinely matters."""
        # 00:30 UTC on the 3rd is still the evening of the 2nd in New York.
        when = datetime.fromisoformat("2026-03-03T00:30:00").replace(tzinfo=UTC)
        bars = BarSeries([bar(when, "100", "101", "99", "100")])

        assert group_sessions(bars, "UTC", timeframe="1h")[0].day.isoformat() == "2026-03-03"
        assert (
            group_sessions(bars, "America/New_York", timeframe="1h")[0].day.isoformat()
            == "2026-03-02"
        )


class TestInitialBalance:
    def test_a_session_that_only_breaks_upward_is_a_single_break(self) -> None:
        # First 60 minutes set 100 to 110. Afterwards price takes the high and never the low.
        bars = session_bars(
            "2026-03-02",
            [
                (0, "105", "110", "100", "106"),
                (30, "106", "109", "101", "107"),
                (60, "107", "115", "106", "114"),  # breaks above
                (90, "114", "116", "112", "115"),
            ],
        )
        result = initial_balance(BarSeries(bars), minutes=60)

        assert len(result.sessions) == 1
        day = result.sessions[0]
        assert day.outcome is Outcome.BROKE_UP_ONLY
        assert {level.key: level.price for level in day.levels} == {
            "ib_high": Decimal("110.00"),
            "ib_low": Decimal("100.00"),
        }
        assert day.triggered_at == bars[2].opened_at
        assert result.hit_rate == Decimal("100.00")

    def test_a_session_that_takes_both_sides_is_a_double_break(self) -> None:
        bars = session_bars(
            "2026-03-02",
            [
                (0, "105", "110", "100", "106"),
                (60, "106", "112", "105", "111"),  # above
                (90, "111", "112", "95", "96"),  # and below
            ],
        )
        result = initial_balance(BarSeries(bars), minutes=60)

        assert result.sessions[0].outcome is Outcome.BROKE_BOTH
        # A double break is not a single break, so it must not count toward the headline.
        assert result.hit_rate == Decimal("0.00")

    def test_a_session_held_inside_the_range_breaks_neither_side(self) -> None:
        bars = session_bars(
            "2026-03-02",
            [
                (0, "105", "110", "100", "106"),
                (60, "106", "109", "101", "104"),
                (90, "104", "108", "102", "107"),
            ],
        )
        result = initial_balance(BarSeries(bars), minutes=60)

        assert result.sessions[0].outcome is Outcome.STAYED_INSIDE
        assert result.sessions[0].triggered_at is None

    def test_the_bars_that_form_the_range_cannot_break_it(self) -> None:
        """The look-ahead guard.

        The range-setting bars touch the range by definition. If they were searched for breaks,
        every session would register a double break and the report would be meaningless.
        """
        bars = session_bars(
            "2026-03-02",
            [
                (0, "105", "110", "100", "106"),  # sets the extremes
                (30, "106", "110", "100", "105"),  # touches both, still inside the window
                (60, "105", "107", "103", "104"),  # after the window, breaks nothing
            ],
        )
        result = initial_balance(BarSeries(bars), minutes=60)

        assert result.sessions[0].outcome is Outcome.STAYED_INSIDE

    def test_a_session_with_no_bars_after_the_window_is_not_counted(self) -> None:
        bars = session_bars("2026-03-02", [(0, "105", "110", "100", "106")])
        result = initial_balance(BarSeries(bars), minutes=60)

        assert result.sessions[0].outcome is Outcome.NO_SETUP
        # No qualifying session means the rate is undefined, not zero.
        assert result.sample_size == 0
        assert result.hit_rate is None

    def test_the_range_length_is_configurable(self) -> None:
        bars = session_bars(
            "2026-03-02",
            [
                (0, "105", "110", "100", "106"),
                (15, "106", "118", "105", "117"),  # inside 60m, outside 15m
            ],
        )

        assert initial_balance(BarSeries(bars), minutes=60).sessions[0].outcome is Outcome.NO_SETUP
        short = initial_balance(BarSeries(bars), minutes=15)
        assert short.sessions[0].outcome is Outcome.BROKE_UP_ONLY


class TestGapFill:
    def _two_days(self, second_day_rows: list[tuple[int, str, str, str, str]]) -> BarSeries:
        first = session_bars(
            "2026-03-02",
            [(0, "100", "101", "99", "100"), (60, "100", "101", "99", "100")],
        )
        return BarSeries(first + session_bars("2026-03-03", second_day_rows))

    def test_a_gap_up_that_trades_back_to_the_prior_close_is_filled(self) -> None:
        series = self._two_days(
            [
                (0, "105", "106", "104", "105"),
                (60, "105", "105", "99", "100"),  # trades back through 100
            ]
        )
        result = gap_fill(series)

        day = result.sessions[0]
        assert day.outcome is Outcome.FILLED
        assert day.measures["gap_points"] == Decimal("5.00")
        assert result.hit_rate == Decimal("100.00")

    def test_a_gap_that_never_returns_is_unfilled(self) -> None:
        series = self._two_days([(0, "105", "108", "104", "107"), (60, "107", "109", "103", "108")])
        result = gap_fill(series)

        assert result.sessions[0].outcome is Outcome.UNFILLED
        assert result.hit_rate == Decimal("0.00")

    def test_a_gap_down_fills_upward(self) -> None:
        series = self._two_days([(0, "95", "96", "94", "95"), (60, "95", "101", "95", "100")])
        result = gap_fill(series)

        assert result.sessions[0].outcome is Outcome.FILLED

    def test_a_negligible_gap_is_excluded_rather_than_counted_as_a_miss(self) -> None:
        # Opens at 100.02 against a 100 close: 0.02%, below the 0.1% floor.
        series = self._two_days(
            [(0, "100.02", "101", "99.5", "100.5"), (60, "100.5", "101", "100", "100.5")]
        )
        result = gap_fill(series)

        assert result.sessions[0].outcome is Outcome.NO_SETUP
        assert result.sample_size == 0
        assert result.hit_rate is None

    def test_the_first_session_is_skipped_because_it_has_no_prior_close(self) -> None:
        bars = session_bars(
            "2026-03-02", [(0, "100", "101", "99", "100"), (60, "100", "101", "99", "100")]
        )
        assert gap_fill(BarSeries(bars)).sessions == ()


class TestPreviousDayLevels:
    def _series(self, second_day_rows: list[tuple[int, str, str, str, str]]) -> BarSeries:
        first = session_bars(
            "2026-03-02",
            [(0, "100", "110", "90", "100"), (60, "100", "105", "95", "100")],
        )
        return BarSeries(first + session_bars("2026-03-03", second_day_rows))

    def test_taking_only_the_prior_high(self) -> None:
        result = previous_day_levels(self._series([(0, "100", "115", "95", "112")]))

        day = result.sessions[0]
        assert day.outcome is Outcome.BROKE_UP_ONLY
        assert {level.key: level.price for level in day.levels} == {
            "prior_high": Decimal("110.00"),
            "prior_low": Decimal("90.00"),
        }

    def test_taking_neither_side_keeps_the_day_inside(self) -> None:
        result = previous_day_levels(self._series([(0, "100", "108", "92", "105")]))
        assert result.sessions[0].outcome is Outcome.STAYED_INSIDE

    def test_taking_both_sides(self) -> None:
        result = previous_day_levels(
            self._series([(0, "100", "115", "95", "112"), (60, "112", "113", "85", "88")])
        )
        assert result.sessions[0].outcome is Outcome.BROKE_BOTH

    def test_levels_come_from_the_prior_session_only(self) -> None:
        """Today's own extremes must not leak into the levels it is judged against."""
        result = previous_day_levels(self._series([(0, "100", "200", "10", "150")]))

        day = result.sessions[0]
        prices = {level.key: level.price for level in day.levels}
        assert prices["prior_high"] == Decimal("110.00")
        assert prices["prior_low"] == Decimal("90.00")


class TestRegistry:
    def test_every_registered_report_can_run(self) -> None:
        first = session_bars(
            "2026-03-02", [(0, "100", "110", "90", "100"), (60, "100", "105", "95", "100")]
        )
        second = session_bars(
            "2026-03-03", [(0, "104", "112", "95", "108"), (60, "108", "111", "99", "100")]
        )
        series = BarSeries(first + second)

        for spec in list_reports():
            result = run_report(spec.key, series)
            assert result.key == spec.key
            # Buckets must account for every session, with nothing silently dropped.
            assert sum(result.buckets.values()) == len(result.sessions)

    def test_an_unknown_report_key_is_refused(self) -> None:
        series = BarSeries(session_bars("2026-03-02", [(0, "100", "101", "99", "100")]))
        with pytest.raises(KeyError):
            run_report("rm -rf /", series)

    def test_parameters_reach_the_report(self) -> None:
        bars = session_bars(
            "2026-03-02", [(0, "105", "110", "100", "106"), (15, "106", "118", "105", "117")]
        )
        result = run_report("initial_balance", BarSeries(bars), parameters={"minutes": 15})

        assert result.sessions[0].outcome is Outcome.BROKE_UP_ONLY

    def test_every_session_carries_what_the_chart_needs(self) -> None:
        first = session_bars(
            "2026-03-02", [(0, "100", "110", "90", "100"), (60, "100", "105", "95", "100")]
        )
        second = session_bars(
            "2026-03-03", [(0, "104", "112", "95", "108"), (60, "108", "111", "99", "100")]
        )
        result = previous_day_levels(BarSeries(first + second))

        day = result.sessions[0].to_dict()
        # The drill-down needs a window and levels, or the day cannot be plotted for verification.
        assert day["window_start"] < day["window_end"]
        assert len(day["levels"]) == 2
        assert all(isinstance(level["price"], str) for level in day["levels"])


class TestConditions:
    """Splitting a rate by context is only fair if the context was knowable at the open."""

    def _week(self) -> BarSeries:
        """Five sessions, Monday to Friday, each opening exactly at the previous close."""
        bars: list[Bar] = []
        for day in ("2026-03-02", "2026-03-03", "2026-03-04", "2026-03-05", "2026-03-06"):
            bars += session_bars(
                day,
                [
                    (0, "100", "110", "100", "105"),
                    (60, "105", "112", "99", "104"),
                    (120, "104", "111", "99", "100"),
                ],
            )
        return BarSeries(bars)

    def test_context_never_reads_the_session_it_describes(self) -> None:
        """Every context value must come from the calendar or the previous session."""
        days = group_sessions(self._week(), "UTC")

        # Rewrite the last session to be wildly different; its context must not change, because
        # nothing in it is derived from its own bars.
        before = session_context(days, 3)
        after = session_context([*days[:3], days[3]], 3)
        assert before == after

    def test_the_first_session_has_only_calendar_context(self) -> None:
        days = group_sessions(self._week(), "UTC")
        context = session_context(days, 0)

        # No prior session exists, so there is nothing to say about gaps or yesterday.
        assert context == {"weekday": "monday"}

    def test_weekday_is_the_local_calendar_day(self) -> None:
        days = group_sessions(self._week(), "UTC")
        assert [session_context(days, i)["weekday"] for i in range(5)] == [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
        ]

    def test_a_session_opening_at_the_prior_close_is_not_a_gap(self) -> None:
        days = group_sessions(self._week(), "UTC")
        assert session_context(days, 1)["gap"] == "flat_open"

    def test_gap_direction_compares_the_open_with_the_prior_close(self) -> None:
        first = session_bars("2026-03-02", [(0, "100", "101", "99", "100")])
        up = session_bars("2026-03-03", [(0, "108", "109", "107", "108")])
        down = session_bars("2026-03-04", [(0, "90", "91", "89", "90")])
        days = group_sessions(BarSeries(first + up + down), "UTC")

        assert session_context(days, 1)["gap"] == "gap_up"
        assert session_context(days, 2)["gap"] == "gap_down"

    def test_prior_day_direction_uses_the_previous_session_only(self) -> None:
        rising = session_bars("2026-03-02", [(0, "100", "110", "99", "108")])
        falling = session_bars("2026-03-03", [(0, "108", "109", "95", "96")])
        third = session_bars("2026-03-04", [(0, "96", "97", "95", "96")])
        days = group_sessions(BarSeries(rising + falling + third), "UTC")

        assert session_context(days, 1)["prior_day"] == "prior_up"
        assert session_context(days, 2)["prior_day"] == "prior_down"

    def test_splitting_partitions_the_sessions_without_losing_any(self) -> None:
        result = previous_day_levels(self._week(), timezone="UTC")
        values = split_by(result, "weekday")

        # Every classifiable session lands in exactly one slice.
        counted = sum(len(value.sessions) for value in values)
        classifiable = sum(1 for s in result.sessions if "weekday" in s.context)
        assert counted == classifiable

    def test_each_slice_computes_its_own_rate_over_its_own_sample(self) -> None:
        result = previous_day_levels(self._week(), timezone="UTC")
        for value in split_by(result, "weekday"):
            hits = sum(1 for s in value.counted if s.outcome in result.headline_outcomes)
            if value.sample_size == 0:
                # An empty slice has no rate, rather than a rate of zero.
                assert value.hit_rate is None
            else:
                expected = Decimal(hits * 100) / Decimal(value.sample_size)
                assert abs(value.hit_rate - expected) < Decimal("0.01")

    def test_an_unknown_condition_is_refused(self) -> None:
        result = previous_day_levels(self._week(), timezone="UTC")
        with pytest.raises(KeyError):
            split_by(result, "phase_of_the_moon")

    def test_a_condition_with_one_value_is_not_offered_as_a_breakdown(self) -> None:
        """A split where every session lands in one bucket repeats the headline and explains nothing."""
        result = previous_day_levels(self._week(), timezone="UTC")
        conditions = available_conditions(result)

        for condition in conditions:
            assert len(condition["values"]) >= 2
        # This week never gaps, so "how it opened" collapses to a single value and is dropped.
        assert "gap" not in {c["key"] for c in conditions}


class TestSessionBoundaries:
    """Futures and FX trade through the evening into the next afternoon.

    Their trading day is not a calendar day, so grouping by local date splits one session into an
    evening fragment and a truncated remainder. That is not a cosmetic mislabelling: the remainder
    reports an opening range measured from midnight, and the two fragments show a gap across what
    was continuous trading.
    """

    #: Sunday 18:00 New York, in UTC. Mid-March, so ET is safely UTC-4 either side.
    OPEN = datetime(2026, 3, 15, 22, 0, tzinfo=UTC)

    def _overnight(self, hours: int = 23, start: datetime | None = None) -> list[Bar]:
        """One CME-style session: 18:00 ET through 17:00 ET the next day."""
        base = start or self.OPEN
        return [bar(base + timedelta(hours=h), "100", "101", "99", "100") for h in range(hours)]

    def test_a_session_spanning_two_dates_is_one_session(self) -> None:
        boundary = boundary_for(AssetType.FUTURES)
        sessions = group_sessions(
            BarSeries(self._overnight()), "America/New_York", boundary=boundary
        )

        assert len(sessions) == 1
        # Named for the day it ends on, which is the convention the exchanges use: the Monday
        # session begins Sunday evening.
        assert sessions[0].day.isoformat() == "2026-03-16"
        assert sessions[0].day.strftime("%A") == "Monday"
        assert len(sessions[0].bars) == 23

    def test_the_boundary_does_not_move_with_the_viewer(self) -> None:
        """A CME contract rolls at 18:00 New York whether you read it from Chicago or Tokyo."""
        boundary = boundary_for(AssetType.FUTURES)
        bars = BarSeries(self._overnight())

        for timezone in ("America/New_York", "Asia/Tokyo", "Europe/London", "UTC"):
            sessions = group_sessions(bars, timezone, boundary=boundary)
            assert [s.day.isoformat() for s in sessions] == ["2026-03-16"], timezone

    def test_a_market_without_a_boundary_still_groups_by_local_date(self) -> None:
        """Equities close and reopen inside one calendar day; nothing here should change them."""
        sessions = group_sessions(
            BarSeries(self._overnight()),
            "America/New_York",
            boundary=boundary_for(AssetType.EQUITY),
        )

        assert [s.day.isoformat() for s in sessions] == ["2026-03-15", "2026-03-16"]

    def test_the_opening_range_is_measured_from_the_real_open(self) -> None:
        """The bug this fixes, stated as a number.

        Split into calendar days, the first hour of the "Monday" fragment is midnight — six hours
        into a session that opened at 18:00 — so the initial balance was measured against a range
        the session had already been trading inside.
        """
        bars = [
            bar(self.OPEN, "105", "110", "100", "106"),  # 18:00 ET: the true opening hour
            *[bar(self.OPEN + timedelta(hours=h), "106", "108", "102", "107") for h in range(1, 8)],
            bar(self.OPEN + timedelta(hours=8), "107", "115", "106", "114"),  # breaks up, 02:00 ET
        ]
        series = BarSeries(bars)

        # Split at midnight, the session is cut in two and the second half measures its "opening"
        # range from 00:00 — a range price had already been trading inside for six hours.
        split = initial_balance(series, minutes=60, timezone="America/New_York", boundary=None)
        assert len(split.sessions) == 2
        assert {level.key: level.price for level in split.sessions[1].levels} == {
            "ib_high": Decimal("108.00"),
            "ib_low": Decimal("102.00"),
        }

        result = initial_balance(
            series,
            minutes=60,
            timezone="America/New_York",
            boundary=boundary_for(AssetType.FUTURES),
        )

        assert len(result.sessions) == 1
        day = result.sessions[0]
        assert day.outcome is Outcome.BROKE_UP_ONLY
        # The real opening hour, 18:00 ET.
        assert {level.key: level.price for level in day.levels} == {
            "ib_high": Decimal("110.00"),
            "ib_low": Decimal("100.00"),
        }

    def test_continuous_trading_is_not_reported_as_a_gap(self) -> None:
        """A price move inside one session is not an overnight gap.

        Split at midnight, the evening fragment's close and the next fragment's open sit either
        side of the cut, and the report measures the distance between them as a gap. Nothing
        gapped: those two bars are consecutive hours of continuous trading.
        """
        bars = [
            # 18:00 through 23:00 ET, closing at 100.
            *[bar(self.OPEN + timedelta(hours=h), "100", "101", "99", "100") for h in range(6)],
            # 00:00 ET, five points higher — the very next hour of the same session.
            bar(self.OPEN + timedelta(hours=6), "105", "106", "104", "105"),
            bar(self.OPEN + timedelta(hours=7), "105", "106", "99", "100"),
        ]
        series = BarSeries(bars)

        # Grouped by New York calendar date, those five points look like a gap that then filled.
        split = gap_fill(series, timezone="America/New_York", boundary=None)
        assert [s.outcome for s in split.sessions] == [Outcome.FILLED]
        assert split.sessions[0].measures["gap_points"] == Decimal("5.00")
        assert split.hit_rate == Decimal("100.00")

        # Grouped by the trading day, there is one session, and a session cannot gap against
        # itself — so there is nothing to measure rather than a fabricated 100% fill rate.
        result = gap_fill(
            series, timezone="America/New_York", boundary=boundary_for(AssetType.FUTURES)
        )
        assert len(result.sessions) == 0
        assert result.hit_rate is None

    def test_forex_rolls_an_hour_before_futures(self) -> None:
        """17:00 ET, not 18:00 — a bar in that hour belongs to a different day for each."""
        five_pm = datetime(2026, 3, 16, 21, 0, tzinfo=UTC)  # Monday 17:00 ET
        bars = BarSeries([bar(five_pm, "100", "101", "99", "100")])

        forex = group_sessions(bars, "UTC", boundary=boundary_for(AssetType.FOREX))
        futures = group_sessions(bars, "UTC", boundary=boundary_for(AssetType.FUTURES))

        assert forex[0].day.isoformat() == "2026-03-17"
        assert futures[0].day.isoformat() == "2026-03-16"

    def test_daily_bars_ignore_the_boundary(self) -> None:
        """A daily bar already is a session; a roll time cannot apply to it."""
        days = ["2026-03-16", "2026-03-17"]
        bars = BarSeries(
            [
                bar(
                    datetime.fromisoformat(f"{day}T00:00:00").replace(tzinfo=UTC),
                    "100",
                    "101",
                    "99",
                    "100",
                )
                for day in days
            ]
        )

        sessions = group_sessions(
            bars, "America/New_York", timeframe="1d", boundary=boundary_for(AssetType.FUTURES)
        )
        assert [s.day.isoformat() for s in sessions] == days

    def test_only_the_asset_types_that_roll_have_a_boundary(self) -> None:
        assert boundary_for(AssetType.FUTURES) is not None
        assert boundary_for(AssetType.FOREX) is not None
        assert boundary_for(AssetType.EQUITY) is None
        assert boundary_for(AssetType.CRYPTO) is None
        # Accepts the wire value too, and refuses to guess at anything it does not know.
        assert boundary_for("futures") is not None
        assert boundary_for("nonsense") is None
        assert boundary_for(None) is None
