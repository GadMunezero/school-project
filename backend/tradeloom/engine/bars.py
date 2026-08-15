"""Bars and bar series.

``opened_at`` is the bar's *opening* timestamp in UTC. A bar labelled 09:30 on a 5-minute series
covers ``[09:30, 09:35)``. That convention is used everywhere — storage, engine, charts — so
"the price at 09:30" is never ambiguous.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


class BarValidationError(ValueError):
    """Raised when a series violates an invariant the engine relies on."""


@dataclass(frozen=True, slots=True)
class Bar:
    opened_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if self.high < self.low:
            raise BarValidationError(f"bar at {self.opened_at}: high {self.high} < low {self.low}")
        if not (self.low <= self.open <= self.high):
            raise BarValidationError(
                f"bar at {self.opened_at}: open {self.open} outside [low, high]"
            )
        if not (self.low <= self.close <= self.high):
            raise BarValidationError(
                f"bar at {self.opened_at}: close {self.close} outside [low, high]"
            )
        if self.opened_at.tzinfo is None:
            raise BarValidationError("bar timestamps must be timezone aware")

    @property
    def typical_price(self) -> Decimal:
        return (self.high + self.low + self.close) / Decimal(3)

    @property
    def range(self) -> Decimal:
        return self.high - self.low

    def contains(self, price: Decimal) -> bool:
        """Whether a price traded within this bar's range."""
        return self.low <= price <= self.high


class BarSeries(Sequence[Bar]):
    """An immutable, ascending, gap-tolerant sequence of bars.

    Construction validates strict ascending order. A backtest that silently processed
    out-of-order or duplicated bars would produce nonsense, so the failure is loud and early.
    """

    __slots__ = ("_bars", "_timestamps")

    def __init__(self, bars: Sequence[Bar]) -> None:
        previous: datetime | None = None
        for bar in bars:
            if previous is not None and bar.opened_at <= previous:
                raise BarValidationError(
                    f"bars must be strictly ascending; {bar.opened_at} follows {previous}"
                )
            previous = bar.opened_at
        self._bars: tuple[Bar, ...] = tuple(bars)
        self._timestamps: tuple[datetime, ...] = tuple(bar.opened_at for bar in self._bars)

    def __len__(self) -> int:
        return len(self._bars)

    def __iter__(self) -> Iterator[Bar]:
        return iter(self._bars)

    def __getitem__(self, index):  # type: ignore[no-untyped-def]
        if isinstance(index, slice):
            return BarSeries(self._bars[index])
        return self._bars[index]

    @property
    def first(self) -> Bar | None:
        return self._bars[0] if self._bars else None

    @property
    def last(self) -> Bar | None:
        return self._bars[-1] if self._bars else None

    def index_at_or_after(self, timestamp: datetime) -> int:
        return bisect_left(self._timestamps, timestamp)

    def index_at_or_before(self, timestamp: datetime) -> int:
        return bisect_right(self._timestamps, timestamp) - 1

    def slice_between(self, start: datetime | None, end: datetime | None) -> BarSeries:
        low = 0 if start is None else self.index_at_or_after(start)
        high = len(self._bars) if end is None else self.index_at_or_before(end) + 1
        return BarSeries(self._bars[low:high])

    def closes(self) -> list[Decimal]:
        return [bar.close for bar in self._bars]

    def digest_source(self) -> str:
        """Stable text fingerprint of the series, hashed into a run's ``input_digest``."""
        if not self._bars:
            return "empty"
        first, last = self._bars[0], self._bars[-1]
        return (
            f"{len(self._bars)}|{first.opened_at.isoformat()}|{first.open}|"
            f"{last.opened_at.isoformat()}|{last.close}"
        )


class BarWindow(Sequence[Bar]):
    """A read-only view of a series truncated at the current bar.

    This is what a strategy receives. It wraps the full series without copying — indexing past
    the current bar raises :class:`LookAheadError` rather than quietly returning a future price,
    so a look-ahead bug fails a test instead of inflating a backtest.
    """

    __slots__ = ("_limit", "_series")

    def __init__(self, series: BarSeries, limit_index: int) -> None:
        self._series = series
        self._limit = limit_index

    def _advance(self, index: int) -> None:
        self._limit = index

    def __len__(self) -> int:
        return self._limit + 1

    def __iter__(self) -> Iterator[Bar]:
        for offset in range(self._limit + 1):
            yield self._series[offset]

    def __getitem__(self, index):  # type: ignore[no-untyped-def]
        if isinstance(index, slice):
            start, stop, step = index.indices(self._limit + 1)
            return [self._series[i] for i in range(start, stop, step)]
        resolved = index if index >= 0 else self._limit + 1 + index
        if resolved > self._limit:
            raise LookAheadError(
                f"bar {resolved} is in the future; the current bar is {self._limit}"
            )
        if resolved < 0:
            raise IndexError(index)
        return self._series[resolved]

    @property
    def current(self) -> Bar:
        return self._series[self._limit]

    def lookback(self, count: int) -> list[Bar]:
        """The last ``count`` bars up to and including the current one."""
        start = max(0, self._limit + 1 - count)
        return [self._series[i] for i in range(start, self._limit + 1)]


class LookAheadError(IndexError):
    """Raised when strategy code reaches for a bar the simulation has not reached yet."""


__all__ = ["Bar", "BarSeries", "BarValidationError", "BarWindow", "LookAheadError"]
