"""Statistical edge reports.

A report answers one recurring question about a market — "when the first hour's range breaks, does
it break one side or both?" — by walking every session in a date range and bucketing the outcome.
The headline is a percentage, but the percentage is not the product: **every session it counted is
returned with it**, carrying the exact levels involved and the window to plot. A trader can open
any single day and see for themselves whether the number is telling the truth.

That is the whole design constraint. A statistic you cannot audit is a statistic you have to take
on faith, and this module exists so you never have to.

Two rules hold everywhere in here:

* **No look-ahead.** A level is only ever computed from bars that closed before the bars tested
  against it. The initial balance is measured over its window and then breaks are searched for in
  *later* bars only; yesterday's high is taken from yesterday's bars alone. Feeding a report a
  session's own outcome would make every number meaningless.
* **No floats.** Prices and percentages stay :class:`~decimal.Decimal` end to end, and an
  undefined ratio is ``None`` rather than zero — "no sessions had a gap" is not "0% of gaps
  filled".

The module is pure: bars in, results out. It touches no database, no session and no settings,
which is what lets it be tested in milliseconds and reused by the API, the worker and any future
CLI without change.
"""

from __future__ import annotations

import builtins
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum

from tradeloom.core.money import quantize_percent, quantize_price, safe_div
from tradeloom.core.timeutil import local_date
from tradeloom.engine.bars import Bar, BarSeries

#: Guard against a caller asking for a report over a decade of one-minute bars.
MAX_SESSIONS = 2_000


class Outcome(StrEnum):
    """Every bucket any report can put a session into.

    One flat vocabulary rather than one enum per report: the UI renders buckets generically, and a
    new report that reuses ``BROKE_BOTH`` gets the existing colour and copy for free.
    """

    BROKE_UP_ONLY = "broke_up_only"
    BROKE_DOWN_ONLY = "broke_down_only"
    BROKE_BOTH = "broke_both"
    STAYED_INSIDE = "stayed_inside"
    FILLED = "filled"
    UNFILLED = "unfilled"
    NO_SETUP = "no_setup"


#: Buckets that mean "the condition this report is about did not arise today". They are reported
#: separately and excluded from the headline percentage, because a day with no gap says nothing
#: about how often gaps fill.
EXCLUDED_FROM_RATE = frozenset({Outcome.NO_SETUP})


@dataclass(frozen=True, slots=True)
class Level:
    """A horizontal price line to draw when a session is opened for verification."""

    key: str
    label: str
    price: Decimal

    def to_dict(self) -> dict[str, str]:
        return {"key": self.key, "label": self.label, "price": str(self.price)}


@dataclass(frozen=True, slots=True)
class SessionResult:
    """One session's contribution to a report, with everything needed to re-examine it."""

    session_date: date
    outcome: Outcome
    #: The lines that define the setup — the IB high, yesterday's low, the gap edges.
    levels: tuple[Level, ...]
    #: When the deciding event happened, if one did. Null when nothing triggered.
    triggered_at: datetime | None
    #: Bars to plot. Includes the prior session where the setup depends on it.
    window_start: datetime
    window_end: datetime
    #: Report-specific measurements, already quantised for display.
    measures: Mapping[str, Decimal | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "session_date": self.session_date.isoformat(),
            "outcome": self.outcome.value,
            "levels": [level.to_dict() for level in self.levels],
            "triggered_at": self.triggered_at.isoformat() if self.triggered_at else None,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "measures": {k: (str(v) if v is not None else None) for k, v in self.measures.items()},
        }


@dataclass(frozen=True, slots=True)
class ReportResult:
    """A report over a date range: the headline, the buckets, and every session behind them."""

    key: str
    name: str
    question: str
    #: Which bucket the headline percentage counts.
    headline_outcomes: tuple[Outcome, ...]
    sessions: tuple[SessionResult, ...]

    @property
    def counted(self) -> builtins.list[SessionResult]:
        """Sessions where the setup actually arose."""
        return [s for s in self.sessions if s.outcome not in EXCLUDED_FROM_RATE]

    @property
    def buckets(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for session in self.sessions:
            counts[session.outcome.value] = counts.get(session.outcome.value, 0) + 1
        return counts

    @property
    def sample_size(self) -> int:
        return len(self.counted)

    @property
    def hit_rate(self) -> Decimal | None:
        """Percentage of qualifying sessions in the headline bucket.

        ``None`` when nothing qualified — an undefined rate, not a zero one.
        """
        hits = sum(1 for s in self.counted if s.outcome in self.headline_outcomes)
        ratio = safe_div(hits * 100, len(self.counted))
        return quantize_percent(ratio) if ratio is not None else None

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "name": self.name,
            "question": self.question,
            "headline_outcomes": [o.value for o in self.headline_outcomes],
            "hit_rate": str(self.hit_rate) if self.hit_rate is not None else None,
            "sample_size": self.sample_size,
            "total_sessions": len(self.sessions),
            "buckets": self.buckets,
            "sessions": [s.to_dict() for s in self.sessions],
        }


# ---------------------------------------------------------------------------
# Session grouping
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Session:
    """One trading day's bars, in the instrument's own timezone."""

    day: date
    bars: tuple[Bar, ...]

    @property
    def opened_at(self) -> datetime:
        return self.bars[0].opened_at

    @property
    def closed_at(self) -> datetime:
        return self.bars[-1].opened_at

    @property
    def open(self) -> Decimal:
        return self.bars[0].open

    @property
    def close(self) -> Decimal:
        return self.bars[-1].close

    @property
    def high(self) -> Decimal:
        return max(bar.high for bar in self.bars)

    @property
    def low(self) -> Decimal:
        return min(bar.low for bar in self.bars)


def group_sessions(series: BarSeries, timezone: str = "UTC") -> builtins.list[Session]:
    """Split a bar series into trading days by local date.

    The timezone matters and is not cosmetic: a US futures session that opens at 18:00 New York
    belongs to the *next* calendar day locally, and grouping in UTC would cut it in half.
    """
    grouped: dict[date, builtins.list[Bar]] = {}
    for bar in series:
        grouped.setdefault(local_date(bar.opened_at, timezone), []).append(bar)

    # Sorted by day: BarSeries guarantees ascending bars, but a caller could hand us a series
    # stitched from two fetches, and a report that walked days out of order would compare the
    # wrong session against the wrong "previous" one.
    return [Session(day=day, bars=tuple(bars)) for day, bars in sorted(grouped.items()) if bars]


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


def initial_balance(
    series: BarSeries,
    *,
    timezone: str = "UTC",
    minutes: int = 60,
) -> ReportResult:
    """Does the opening range break one side, or both?

    The initial balance is the high and low of the first ``minutes`` of a session. Breaks are
    searched for only in bars that open *after* that window closes — the bars that formed the range
    obviously "touch" it, and counting them would report a 100% break rate.

    The distinction the report is built around is single break against double break: a session that
    only ever takes one side is a session where the first break tended to hold.
    """
    sessions: builtins.list[SessionResult] = []

    for session in _limited(group_sessions(series, timezone)):
        window_end = session.opened_at + timedelta(minutes=minutes)
        window = [bar for bar in session.bars if bar.opened_at < window_end]
        rest = [bar for bar in session.bars if bar.opened_at >= window_end]

        # A session needs both a formed range and time left to break it.
        if not window or not rest:
            sessions.append(
                SessionResult(
                    session_date=session.day,
                    outcome=Outcome.NO_SETUP,
                    levels=(),
                    triggered_at=None,
                    window_start=session.opened_at,
                    window_end=session.closed_at,
                    measures={"reason": None},
                )
            )
            continue

        ib_high = quantize_price(max(bar.high for bar in window))
        ib_low = quantize_price(min(bar.low for bar in window))

        broke_up_at = next((b.opened_at for b in rest if b.high > ib_high), None)
        broke_down_at = next((b.opened_at for b in rest if b.low < ib_low), None)

        if broke_up_at and broke_down_at:
            outcome = Outcome.BROKE_BOTH
            triggered = min(broke_up_at, broke_down_at)
        elif broke_up_at:
            outcome = Outcome.BROKE_UP_ONLY
            triggered = broke_up_at
        elif broke_down_at:
            outcome = Outcome.BROKE_DOWN_ONLY
            triggered = broke_down_at
        else:
            outcome = Outcome.STAYED_INSIDE
            triggered = None

        sessions.append(
            SessionResult(
                session_date=session.day,
                outcome=outcome,
                levels=(
                    Level("ib_high", f"{minutes}m high", ib_high),
                    Level("ib_low", f"{minutes}m low", ib_low),
                ),
                triggered_at=triggered,
                window_start=session.opened_at,
                window_end=session.closed_at,
                measures={
                    "ib_range": quantize_price(ib_high - ib_low),
                    "session_range": quantize_price(session.high - session.low),
                    "extension": _extension(session, ib_high, ib_low),
                },
            )
        )

    return ReportResult(
        key="initial_balance",
        name=f"Initial balance ({minutes}m)",
        question=(
            f"Once the first {minutes} minutes have set a range, "
            "does price break one side or both?"
        ),
        headline_outcomes=(Outcome.BROKE_UP_ONLY, Outcome.BROKE_DOWN_ONLY),
        sessions=tuple(sessions),
    )


def _extension(session: Session, ib_high: Decimal, ib_low: Decimal) -> Decimal | None:
    """How far beyond the range the session travelled, as a multiple of the range itself."""
    ib_range = ib_high - ib_low
    if ib_range <= 0:
        return None
    beyond = max(session.high - ib_high, ib_low - session.low, Decimal(0))
    ratio = safe_div(beyond, ib_range)
    return quantize_percent(ratio) if ratio is not None else None


def gap_fill(
    series: BarSeries,
    *,
    timezone: str = "UTC",
    minimum_percent: Decimal = Decimal("0.1"),
) -> ReportResult:
    """When a session opens away from the last close, does it trade back to it?

    Only gaps of at least ``minimum_percent`` count — every session opens a tick or two away from
    the previous close, and counting those would drown the real gaps in noise. Sessions without a
    qualifying gap are bucketed as ``no_setup`` and left out of the percentage entirely.
    """
    sessions: builtins.list[SessionResult] = []
    days = _limited(group_sessions(series, timezone))

    for index, session in enumerate(days):
        if index == 0:
            continue  # No prior close to gap from.

        previous = days[index - 1]
        prior_close = quantize_price(previous.close)
        open_price = quantize_price(session.open)

        gap = open_price - prior_close
        gap_percent = safe_div(abs(gap) * 100, prior_close)

        if gap_percent is None or gap_percent < minimum_percent or gap == 0:
            sessions.append(
                SessionResult(
                    session_date=session.day,
                    outcome=Outcome.NO_SETUP,
                    levels=(Level("prior_close", "Prior close", prior_close),),
                    triggered_at=None,
                    window_start=previous.opened_at,
                    window_end=session.closed_at,
                    measures={"gap_percent": quantize_percent(gap_percent or Decimal(0))},
                )
            )
            continue

        # Filled means price traded back through the previous close at some point in the session.
        filled_at = next(
            (
                bar.opened_at
                for bar in session.bars
                if (gap > 0 and bar.low <= prior_close) or (gap < 0 and bar.high >= prior_close)
            ),
            None,
        )

        sessions.append(
            SessionResult(
                session_date=session.day,
                outcome=Outcome.FILLED if filled_at else Outcome.UNFILLED,
                levels=(
                    Level("prior_close", "Prior close", prior_close),
                    Level("session_open", "Open", open_price),
                ),
                triggered_at=filled_at,
                window_start=previous.opened_at,
                window_end=session.closed_at,
                measures={
                    "gap_percent": quantize_percent(gap_percent),
                    "gap_points": quantize_price(gap),
                },
            )
        )

    return ReportResult(
        key="gap_fill",
        name="Gap fill",
        question=(
            "When a session opens away from the previous close, "
            "how often does it trade back to it?"
        ),
        headline_outcomes=(Outcome.FILLED,),
        sessions=tuple(sessions),
    )


def previous_day_levels(series: BarSeries, *, timezone: str = "UTC") -> ReportResult:
    """Does today take out yesterday's high, yesterday's low, both, or neither?

    Yesterday's extremes are fixed before today opens, which makes this the cleanest look-ahead
    test in the module: the levels come from one session and the outcome from the next.
    """
    sessions: builtins.list[SessionResult] = []
    days = _limited(group_sessions(series, timezone))

    for index, session in enumerate(days):
        if index == 0:
            continue

        previous = days[index - 1]
        prior_high = quantize_price(previous.high)
        prior_low = quantize_price(previous.low)

        took_high_at = next((b.opened_at for b in session.bars if b.high > prior_high), None)
        took_low_at = next((b.opened_at for b in session.bars if b.low < prior_low), None)

        if took_high_at and took_low_at:
            outcome = Outcome.BROKE_BOTH
            triggered = min(took_high_at, took_low_at)
        elif took_high_at:
            outcome = Outcome.BROKE_UP_ONLY
            triggered = took_high_at
        elif took_low_at:
            outcome = Outcome.BROKE_DOWN_ONLY
            triggered = took_low_at
        else:
            outcome = Outcome.STAYED_INSIDE
            triggered = None

        sessions.append(
            SessionResult(
                session_date=session.day,
                outcome=outcome,
                levels=(
                    Level("prior_high", "Prior day high", prior_high),
                    Level("prior_low", "Prior day low", prior_low),
                ),
                triggered_at=triggered,
                window_start=previous.opened_at,
                window_end=session.closed_at,
                measures={
                    "prior_range": quantize_price(prior_high - prior_low),
                    "session_range": quantize_price(session.high - session.low),
                },
            )
        )

    return ReportResult(
        key="previous_day_levels",
        name="Previous day levels",
        question="Does today take out yesterday's high, yesterday's low, both, or neither?",
        headline_outcomes=(Outcome.BROKE_UP_ONLY, Outcome.BROKE_DOWN_ONLY),
        sessions=tuple(sessions),
    )


def _limited(sessions: Sequence[Session]) -> Sequence[Session]:
    """Keep the most recent window when a caller asks for more history than is sensible."""
    return sessions[-MAX_SESSIONS:] if len(sessions) > MAX_SESSIONS else sessions


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReportSpec:
    """What a report is and what it can be tuned with, declared for the UI to render."""

    key: str
    name: str
    question: str
    description: str
    parameters: tuple[dict[str, object], ...] = ()


REPORTS: tuple[ReportSpec, ...] = (
    ReportSpec(
        key="initial_balance",
        name="Initial balance",
        question="Does the opening range break one side, or both?",
        description=(
            "Measures the high and low of the session's first minutes, then looks only at later "
            "bars to see which side gives way. A session that takes one side and never the other "
            "is one where the first break held."
        ),
        parameters=(
            {
                "name": "minutes",
                "label": "Range length (minutes)",
                "param_type": "integer",
                "default_value": "60",
                "minimum": "5",
                "maximum": "240",
                "step": "5",
            },
        ),
    ),
    ReportSpec(
        key="gap_fill",
        name="Gap fill",
        question="When a session gaps away from the previous close, does it trade back?",
        description=(
            "Compares each open with the previous close and, when the difference is large enough "
            "to matter, checks whether price returned to it before the session ended."
        ),
        parameters=(
            {
                "name": "minimum_percent",
                "label": "Smallest gap to count (%)",
                "param_type": "decimal",
                "default_value": "0.1",
                "minimum": "0.01",
                "maximum": "10",
                "step": "0.01",
            },
        ),
    ),
    ReportSpec(
        key="previous_day_levels",
        name="Previous day levels",
        question="Does today take out yesterday's high, yesterday's low, both, or neither?",
        description=(
            "Yesterday's extremes are fixed before today opens, so this is a clean test of how "
            "often a prior day's range contains the next one."
        ),
    ),
)

_BUILDERS = {
    "initial_balance": initial_balance,
    "gap_fill": gap_fill,
    "previous_day_levels": previous_day_levels,
}


def list_reports() -> tuple[ReportSpec, ...]:
    return REPORTS


def run_report(
    key: str,
    series: BarSeries,
    *,
    timezone: str = "UTC",
    parameters: Mapping[str, object] | None = None,
) -> ReportResult:
    """Run a registered report by key.

    Only keys in the registry can run — the same rule the strategy engine follows. A caller cannot
    name arbitrary code, and an unknown key fails loudly rather than returning an empty report that
    would read as "this never happens".
    """
    builder = _BUILDERS.get(key)
    if builder is None:
        raise KeyError(f"unknown report: {key}")

    kwargs: dict[str, object] = {"timezone": timezone}
    values = dict(parameters or {})

    if key == "initial_balance" and "minutes" in values:
        kwargs["minutes"] = int(values["minutes"])  # type: ignore[arg-type]
    if key == "gap_fill" and "minimum_percent" in values:
        kwargs["minimum_percent"] = Decimal(str(values["minimum_percent"]))

    return builder(series, **kwargs)  # type: ignore[operator]


__all__ = [
    "MAX_SESSIONS",
    "Level",
    "Outcome",
    "ReportResult",
    "ReportSpec",
    "Session",
    "SessionResult",
    "gap_fill",
    "group_sessions",
    "initial_balance",
    "list_reports",
    "previous_day_levels",
    "run_report",
]
