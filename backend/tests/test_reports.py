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

from tradeloom.engine.bars import Bar, BarSeries
from tradeloom.engine.reports import (
    Outcome,
    gap_fill,
    group_sessions,
    initial_balance,
    list_reports,
    previous_day_levels,
    run_report,
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
