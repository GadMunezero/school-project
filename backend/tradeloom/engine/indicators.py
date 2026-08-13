"""Streaming indicators.

Every indicator here is **incremental**: it is fed one bar at a time and can only ever have seen
bars up to the current simulation timestamp. That is a structural guarantee against look-ahead
bias — an indicator physically cannot reach a future value, because that value has not been
pushed into it yet. Vectorised indicators computed over a whole array are the classic source of
this bug, so the engine does not use them.

Each indicator exposes ``value`` (``None`` until it has enough data) and ``is_ready``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from decimal import Decimal

from tradeloom.core.money import ZERO, safe_div
from tradeloom.engine.bars import Bar


class Indicator(ABC):
    """Base class. ``update`` returns the new value (or ``None`` while warming up)."""

    def __init__(self, period: int) -> None:
        if period < 1:
            raise ValueError("indicator period must be at least 1")
        self.period = period
        self._value: Decimal | None = None
        self._samples = 0

    @property
    def value(self) -> Decimal | None:
        return self._value

    @property
    def is_ready(self) -> bool:
        return self._value is not None

    @abstractmethod
    def update(self, bar: Bar) -> Decimal | None: ...

    def reset(self) -> None:
        self._value = None
        self._samples = 0


class SMA(Indicator):
    """Simple moving average of closes."""

    def __init__(self, period: int) -> None:
        super().__init__(period)
        self._window: deque[Decimal] = deque(maxlen=period)
        self._sum = ZERO

    def update(self, bar: Bar) -> Decimal | None:
        if len(self._window) == self.period:
            self._sum -= self._window[0]
        self._window.append(bar.close)
        self._sum += bar.close
        self._samples += 1
        if len(self._window) < self.period:
            return None
        self._value = safe_div(self._sum, Decimal(self.period))
        return self._value

    def reset(self) -> None:
        super().reset()
        self._window.clear()
        self._sum = ZERO


class EMA(Indicator):
    """Exponential moving average.

    Seeded with the SMA of the first ``period`` closes — the standard convention, and the reason
    an EMA needs ``period`` bars before it reports a value rather than starting from bar one.
    """

    def __init__(self, period: int) -> None:
        super().__init__(period)
        self._multiplier = safe_div(Decimal(2), Decimal(period + 1)) or ZERO
        self._seed: list[Decimal] = []

    def update(self, bar: Bar) -> Decimal | None:
        self._samples += 1
        if self._value is None:
            self._seed.append(bar.close)
            if len(self._seed) < self.period:
                return None
            self._value = safe_div(sum(self._seed, ZERO), Decimal(self.period))
            return self._value
        self._value = (bar.close - self._value) * self._multiplier + self._value
        return self._value

    def reset(self) -> None:
        super().reset()
        self._seed.clear()


class RSI(Indicator):
    """Wilder's relative strength index.

    Uses Wilder smoothing (not a simple average of gains/losses), which is what every charting
    platform means by "RSI(14)".
    """

    def __init__(self, period: int = 14) -> None:
        super().__init__(period)
        self._previous_close: Decimal | None = None
        self._avg_gain: Decimal | None = None
        self._avg_loss: Decimal | None = None
        self._gains: list[Decimal] = []
        self._losses: list[Decimal] = []

    def update(self, bar: Bar) -> Decimal | None:
        self._samples += 1
        if self._previous_close is None:
            self._previous_close = bar.close
            return None

        change = bar.close - self._previous_close
        self._previous_close = bar.close
        gain = change if change > 0 else ZERO
        loss = -change if change < 0 else ZERO

        if self._avg_gain is None:
            self._gains.append(gain)
            self._losses.append(loss)
            if len(self._gains) < self.period:
                return None
            self._avg_gain = safe_div(sum(self._gains, ZERO), Decimal(self.period))
            self._avg_loss = safe_div(sum(self._losses, ZERO), Decimal(self.period))
        else:
            divisor = Decimal(self.period)
            self._avg_gain = (self._avg_gain * (divisor - 1) + gain) / divisor
            self._avg_loss = ((self._avg_loss or ZERO) * (divisor - 1) + loss) / divisor

        if self._avg_loss is None or self._avg_loss == 0:
            # No losses in the window: RSI is defined as 100, not undefined.
            self._value = Decimal(100)
            return self._value
        rs = safe_div(self._avg_gain or ZERO, self._avg_loss)
        if rs is None:  # pragma: no cover - guarded above
            return self._value
        self._value = Decimal(100) - safe_div(Decimal(100), Decimal(1) + rs)  # type: ignore[operator]
        return self._value

    def reset(self) -> None:
        super().reset()
        self._previous_close = None
        self._avg_gain = None
        self._avg_loss = None
        self._gains.clear()
        self._losses.clear()


class ATR(Indicator):
    """Average true range, Wilder-smoothed. Used for volatility-scaled stops."""

    def __init__(self, period: int = 14) -> None:
        super().__init__(period)
        self._previous_close: Decimal | None = None
        self._seed: list[Decimal] = []

    def update(self, bar: Bar) -> Decimal | None:
        self._samples += 1
        if self._previous_close is None:
            true_range = bar.high - bar.low
        else:
            true_range = max(
                bar.high - bar.low,
                abs(bar.high - self._previous_close),
                abs(bar.low - self._previous_close),
            )
        self._previous_close = bar.close

        if self._value is None:
            self._seed.append(true_range)
            if len(self._seed) < self.period:
                return None
            self._value = safe_div(sum(self._seed, ZERO), Decimal(self.period))
            return self._value

        divisor = Decimal(self.period)
        self._value = (self._value * (divisor - 1) + true_range) / divisor
        return self._value

    def reset(self) -> None:
        super().reset()
        self._previous_close = None
        self._seed.clear()


class RollingHigh(Indicator):
    """Highest high over the last ``period`` *completed* bars.

    ``exclude_current`` matters for breakout strategies: a breakout above "the 20-bar high" must
    compare against the high of the 20 bars *before* this one, otherwise the current bar's own
    high makes the condition unsatisfiable.
    """

    def __init__(self, period: int, exclude_current: bool = True) -> None:
        super().__init__(period)
        self.exclude_current = exclude_current
        self._window: deque[Decimal] = deque(maxlen=period)

    def update(self, bar: Bar) -> Decimal | None:
        self._samples += 1
        if self.exclude_current:
            self._value = max(self._window) if len(self._window) == self.period else None
            self._window.append(bar.high)
            return self._value
        self._window.append(bar.high)
        self._value = max(self._window) if len(self._window) == self.period else None
        return self._value

    def reset(self) -> None:
        super().reset()
        self._window.clear()


class RollingLow(Indicator):
    """Lowest low over the last ``period`` completed bars. See :class:`RollingHigh`."""

    def __init__(self, period: int, exclude_current: bool = True) -> None:
        super().__init__(period)
        self.exclude_current = exclude_current
        self._window: deque[Decimal] = deque(maxlen=period)

    def update(self, bar: Bar) -> Decimal | None:
        self._samples += 1
        if self.exclude_current:
            self._value = min(self._window) if len(self._window) == self.period else None
            self._window.append(bar.low)
            return self._value
        self._window.append(bar.low)
        self._value = min(self._window) if len(self._window) == self.period else None
        return self._value

    def reset(self) -> None:
        super().reset()
        self._window.clear()


class StandardDeviation(Indicator):
    """Population standard deviation of closes over the window."""

    def __init__(self, period: int) -> None:
        super().__init__(period)
        self._window: deque[Decimal] = deque(maxlen=period)

    def update(self, bar: Bar) -> Decimal | None:
        self._window.append(bar.close)
        self._samples += 1
        if len(self._window) < self.period:
            return None
        mean = safe_div(sum(self._window, ZERO), Decimal(self.period)) or ZERO
        variance = safe_div(
            sum(((value - mean) ** 2 for value in self._window), ZERO), Decimal(self.period)
        )
        if variance is None or variance < 0:  # pragma: no cover
            return None
        self._value = Decimal(str(float(variance) ** 0.5))
        return self._value

    def reset(self) -> None:
        super().reset()
        self._window.clear()


__all__ = [
    "ATR",
    "EMA",
    "RSI",
    "SMA",
    "Indicator",
    "RollingHigh",
    "RollingLow",
    "StandardDeviation",
]
