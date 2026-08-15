"""Import OHLCV candles from a CSV export.

This is the bridge between the engine and reality. Everything downstream — reports, backtests,
replay — is only as truthful as the candles underneath it, and until a user can load their own,
every number the product shows is a statement about generated data.

The work is deliberately small, because the pieces already exist:

* :func:`~tradeloom.services.imports.parsing.parse_timestamp` handles broker date formats and
  converts from the source timezone to UTC.
* :meth:`~tradeloom.services.market_data.MarketDataService.ingest` inserts idempotently, skipping
  bars already stored, and refreshes coverage.
* :class:`~tradeloom.engine.bars.Bar` rejects a malformed candle outright — a high below its low,
  a close outside the range, a naive timestamp.

So this module parses, maps and hands over. It deliberately does *not* re-implement validation:
a bar that ``Bar`` refuses is reported as a rejected row rather than quietly repaired, because
silently "fixing" market data is how a backtest ends up trading prices that never existed.
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from tradeloom.core.enums import Timeframe
from tradeloom.core.errors import UnprocessableStateError
from tradeloom.engine.bars import Bar, BarValidationError
from tradeloom.services.imports import parsing

#: Header names seen in the wild, per field. Matching is case- and space-insensitive.
FIELD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "timestamp": (
        "time",
        "date",
        "datetime",
        "date/time",
        "date time",
        "time stamp",
        "timestamp",
        "bar time",
        "open time",
        "opened at",
    ),
    "open": ("open", "o", "open price"),
    "high": ("high", "h", "high price"),
    "low": ("low", "l", "low price"),
    "close": ("close", "c", "close price", "last"),
    "volume": ("volume", "vol", "v", "quantity", "tick volume"),
}

REQUIRED_FIELDS = ("timestamp", "open", "high", "low", "close")

#: Refuse absurd files rather than spend minutes parsing one.
MAX_ROWS = 500_000


def _normalise(header: str) -> str:
    return header.strip().lower().replace("_", " ").replace("-", " ")


def suggest_mapping(headers: builtins.list[str]) -> dict[str, str]:
    """Guess which column holds which field.

    Exact synonym matches only. A fuzzy guess that silently maps "low" to "close" would produce a
    plausible-looking series of wrong candles, which is far worse than asking the user to confirm.
    """
    mapping: dict[str, str] = {}
    for header in headers:
        normalised = _normalise(header)
        for canonical, synonyms in FIELD_SYNONYMS.items():
            if canonical in mapping:
                continue
            if normalised in synonyms:
                mapping[canonical] = header
                break
    return mapping


@dataclass(slots=True)
class RejectedRow:
    """A row that could not become a candle, and why."""

    row_number: int
    reason: str
    raw: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"row_number": self.row_number, "reason": self.reason, "raw": self.raw}


@dataclass(slots=True)
class ParseResult:
    """Candles that parsed cleanly, plus every row that did not."""

    bars: builtins.list[Bar]
    rejected: builtins.list[RejectedRow]
    total_rows: int

    @property
    def accepted(self) -> int:
        return len(self.bars)


def inspect(data: bytes) -> dict[str, Any]:
    """Read the header row and sample values so the user can confirm the mapping."""
    inspection = parsing.inspect_csv(data)
    return {
        "headers": inspection.headers,
        "delimiter": inspection.delimiter,
        "total_rows": inspection.total_rows,
        "preview": inspection.preview[:10],
        "suggested_mapping": suggest_mapping(inspection.headers),
    }


def _value_of(row: dict[str, str], mapping: dict[str, str], field_name: str) -> str:
    """The raw cell for a mapped field, or empty when the field is not mapped."""
    column = mapping.get(field_name)
    return row.get(column, "") if column else ""


def parse_candles(
    data: bytes,
    *,
    mapping: dict[str, str],
    timezone: str = "UTC",
    delimiter: str | None = None,
) -> ParseResult:
    """Turn a CSV into candles.

    Rows are rejected, never repaired. A candle whose high is below its low is not a rounding
    problem to be smoothed over — it means the mapping is wrong or the export is corrupt, and
    importing it would poison every statistic computed from the series.
    """
    missing = [field_name for field_name in REQUIRED_FIELDS if not mapping.get(field_name)]
    if missing:
        raise UnprocessableStateError(
            "Map every required column before importing: " + ", ".join(missing)
        )

    resolved_delimiter = delimiter or parsing.inspect_csv(data).delimiter
    rows = parsing.iter_rows(data, resolved_delimiter)
    if len(rows) > MAX_ROWS:
        raise UnprocessableStateError(
            f"This file has {len(rows):,} rows; the limit is {MAX_ROWS:,}. Split it by date range."
        )

    bars: builtins.list[Bar] = []
    rejected: builtins.list[RejectedRow] = []
    seen: set[Any] = set()

    for index, row in enumerate(rows, start=1):
        opened_at = parsing.parse_timestamp(_value_of(row, mapping, "timestamp"), timezone)
        if opened_at is None:
            rejected.append(RejectedRow(index, "Could not read the timestamp.", row))
            continue

        prices: dict[str, Decimal] = {}
        bad_field = None
        for field_name in ("open", "high", "low", "close"):
            parsed = parsing.parse_decimal(
                _value_of(row, mapping, field_name), allow_negative=False
            )
            if parsed is None or parsed <= 0:
                bad_field = field_name
                break
            prices[field_name] = parsed
        if bad_field is not None:
            rejected.append(RejectedRow(index, f"{bad_field} is missing or not a price.", row))
            continue

        volume = parsing.parse_decimal(
            _value_of(row, mapping, "volume"), allow_negative=False
        ) or Decimal(0)

        # Two bars for the same instant cannot both be right, and the storage layer's unique
        # constraint would reject the second anyway. Say so here instead.
        if opened_at in seen:
            rejected.append(RejectedRow(index, "Duplicate timestamp in this file.", row))
            continue

        try:
            bars.append(
                Bar(
                    opened_at=opened_at,
                    open=prices["open"],
                    high=prices["high"],
                    low=prices["low"],
                    close=prices["close"],
                    volume=volume,
                )
            )
        except BarValidationError as error:
            # Bar's own invariants: high >= low, open and close inside the range.
            rejected.append(RejectedRow(index, str(error), row))
            continue

        seen.add(opened_at)

    bars.sort(key=lambda bar: bar.opened_at)
    return ParseResult(bars=bars, rejected=rejected, total_rows=len(rows))


def summarise(result: ParseResult, timeframe: Timeframe) -> dict[str, Any]:
    """What the user needs to decide whether to commit this import."""
    return {
        "total_rows": result.total_rows,
        "accepted": result.accepted,
        "rejected": len(result.rejected),
        "timeframe": timeframe.value,
        "first_bar_at": result.bars[0].opened_at.isoformat() if result.bars else None,
        "last_bar_at": result.bars[-1].opened_at.isoformat() if result.bars else None,
        "rejected_rows": [row.to_dict() for row in result.rejected[:50]],
    }


__all__ = [
    "FIELD_SYNONYMS",
    "MAX_ROWS",
    "REQUIRED_FIELDS",
    "ParseResult",
    "RejectedRow",
    "inspect",
    "parse_candles",
    "suggest_mapping",
    "summarise",
]
