"""CSV inspection, column detection and value normalisation.

Pure functions over text — no database, no I/O. That keeps the messy part of importing (broker
CSVs are relentlessly inconsistent) exhaustively testable.

Nothing here ever discards a row. Every value that cannot be parsed produces a structured error
attached to its field, which the UI renders inline against the offending cell.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

from tradeloom.core.enums import OrderSide
from tradeloom.core.money import MoneyError, to_decimal
from tradeloom.core.timeutil import ensure_aware, get_zone

#: Canonical fields the pipeline understands, with the header spellings brokers actually use.
FIELD_SYNONYMS: dict[str, tuple[str, ...]] = {
    "external_id": (
        "id",
        "order id",
        "orderid",
        "trade id",
        "tradeid",
        "execution id",
        "exec id",
        "reference",
    ),
    "timestamp": (
        "time",
        "date",
        "datetime",
        "date/time",
        "timestamp",
        "fill time",
        "execution time",
        "transaction date",
        "trade date",
    ),
    "symbol": ("symbol", "ticker", "instrument", "contract", "market", "pair", "asset"),
    "side": ("side", "action", "direction", "buy/sell", "b/s", "type", "transaction type"),
    "quantity": (
        "quantity",
        "qty",
        "shares",
        "size",
        "volume",
        "contracts",
        "units",
        "filled qty",
        "amount",
    ),
    "price": (
        "price",
        "fill price",
        "avg price",
        "average price",
        "exec price",
        "trade price",
        "rate",
    ),
    "commission": ("commission", "commissions", "comm", "fee", "brokerage"),
    "fees": ("fees", "other fees", "exchange fee", "regulatory fee", "tax", "swap"),
    "stop_loss": ("stop", "stop loss", "sl", "stop price"),
    "take_profit": ("target", "take profit", "tp", "limit price", "profit target"),
    "notes": ("notes", "note", "comment", "comments", "description", "memo"),
    "account": ("account", "account id", "account number", "acct"),
}

#: Direction words seen in the wild, mapped to a side.
BUY_WORDS = frozenset(
    {"buy", "b", "bought", "bot", "long", "buy to open", "buy to close", "purchase", "1"}
)
SELL_WORDS = frozenset(
    {"sell", "s", "sold", "sld", "short", "sell to open", "sell to close", "sale", "-1", "2"}
)

#: Timestamp layouts tried in order, before falling back to ISO parsing.
TIMESTAMP_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y/%m/%d %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y %I:%M %p",
    "%d-%m-%Y %H:%M:%S",
    "%Y%m%d %H%M%S",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
MAX_PREVIEW_ROWS = 20
MAX_SAMPLE_VALUES = 5


def _normalise_header(value: str) -> str:
    return _NON_ALNUM.sub(" ", value.strip().lower()).strip()


@dataclass(slots=True)
class ColumnInfo:
    name: str
    index: int
    samples: list[str] = field(default_factory=list)
    #: Best-guess canonical field, with a 0..1 confidence the UI shows next to the mapping.
    detected_field: str | None = None
    confidence: float = 0.0
    non_empty_count: int = 0


@dataclass(slots=True)
class Inspection:
    delimiter: str
    headers: list[str]
    columns: list[ColumnInfo]
    total_rows: int
    preview: list[dict[str, str]]
    suggested_mapping: dict[str, str]
    detected_template: str | None = None
    encoding: str = "utf-8"

    def to_dict(self) -> dict[str, Any]:
        return {
            "delimiter": self.delimiter,
            "encoding": self.encoding,
            "headers": self.headers,
            "total_rows": self.total_rows,
            "preview": self.preview,
            "suggested_mapping": self.suggested_mapping,
            "detected_template": self.detected_template,
            "columns": [
                {
                    "name": column.name,
                    "index": column.index,
                    "samples": column.samples,
                    "detected_field": column.detected_field,
                    "confidence": round(column.confidence, 2),
                    "non_empty_count": column.non_empty_count,
                }
                for column in self.columns
            ],
        }


class CsvParseError(ValueError):
    """Raised when a file cannot be read as CSV at all."""


def decode_bytes(data: bytes) -> tuple[str, str]:
    """Decode with a BOM-aware fallback chain. Returns ``(text, encoding)``."""
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise CsvParseError("The file could not be decoded as text. Export it as UTF-8 CSV.")


def sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        # Sniffer fails on short or unusual files; fall back to the most frequent candidate.
        counts = {candidate: sample.count(candidate) for candidate in ",;\t|"}
        best = max(counts, key=lambda key: counts[key])
        return best if counts[best] else ","


def detect_field(header: str, samples: list[str]) -> tuple[str | None, float]:
    """Guess a canonical field from a header, corroborated by the sample values."""
    normalised = _normalise_header(header)
    if not normalised:
        return None, 0.0

    for canonical, synonyms in FIELD_SYNONYMS.items():
        if normalised in synonyms:
            return canonical, 1.0

    for canonical, synonyms in FIELD_SYNONYMS.items():
        for synonym in synonyms:
            if synonym in normalised or normalised in synonym:
                return canonical, 0.7

    # Header gave nothing; infer from the values themselves.
    non_empty = [value for value in samples if value.strip()]
    if not non_empty:
        return None, 0.0
    if all(_looks_like_side(value) for value in non_empty):
        return "side", 0.5
    if all(_looks_like_timestamp(value) for value in non_empty):
        return "timestamp", 0.5
    return None, 0.0


def _looks_like_side(value: str) -> bool:
    lowered = value.strip().lower()
    return lowered in BUY_WORDS or lowered in SELL_WORDS


def _looks_like_timestamp(value: str) -> bool:
    return parse_timestamp(value, "UTC") is not None


def inspect_csv(data: bytes, *, max_rows: int = 200_000) -> Inspection:
    """Read headers, sample values and suggest a mapping."""
    text, encoding = decode_bytes(data)
    if not text.strip():
        raise CsvParseError("The file is empty.")

    delimiter = sniff_delimiter(text[:8192])
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)

    try:
        headers = next(reader)
    except StopIteration as exc:
        raise CsvParseError("The file has no header row.") from exc

    headers = [header.strip() or f"column_{index + 1}" for index, header in enumerate(headers)]
    columns = [ColumnInfo(name=name, index=index) for index, name in enumerate(headers)]
    preview: list[dict[str, str]] = []
    total = 0

    for row in reader:
        total += 1
        if total > max_rows:
            raise CsvParseError(
                f"The file has more than {max_rows:,} rows. Split it into smaller files."
            )
        for index, column in enumerate(columns):
            value = row[index].strip() if index < len(row) else ""
            if value:
                column.non_empty_count += 1
                if len(column.samples) < MAX_SAMPLE_VALUES:
                    column.samples.append(value)
        if len(preview) < MAX_PREVIEW_ROWS:
            preview.append(
                {
                    header: (row[index].strip() if index < len(row) else "")
                    for index, header in enumerate(headers)
                }
            )

    suggested: dict[str, str] = {}
    for column in columns:
        detected, confidence = detect_field(column.name, column.samples)
        column.detected_field = detected
        column.confidence = confidence
        # First column wins a field; a later duplicate keeps its guess for display only.
        if detected and detected not in suggested:
            suggested[detected] = column.name

    return Inspection(
        delimiter=delimiter,
        headers=headers,
        columns=columns,
        total_rows=total,
        preview=preview,
        suggested_mapping=suggested,
        encoding=encoding,
    )


def iter_rows(data: bytes, delimiter: str) -> list[dict[str, str]]:
    """Every data row as a dict keyed by header. Ragged rows are padded, never dropped."""
    text, _ = decode_bytes(data)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        headers = [h.strip() or f"column_{i + 1}" for i, h in enumerate(next(reader))]
    except StopIteration as exc:
        raise CsvParseError("The file has no header row.") from exc

    rows: list[dict[str, str]] = []
    for raw in reader:
        rows.append(
            {
                header: (raw[index].strip() if index < len(raw) else "")
                for index, header in enumerate(headers)
            }
        )
    return rows


# --- value normalisation ----------------------------------------------------


def parse_timestamp(value: str, timezone: str) -> datetime | None:
    """Parse a broker timestamp and convert it to UTC.

    A naive timestamp is interpreted in the *source* timezone the user selected — never in the
    server's timezone, which would shift every trade for anyone outside UTC.
    """
    text = value.strip()
    if not text:
        return None

    if text.replace(".", "", 1).isdigit() and len(text.split(".")[0]) in (10, 13):
        # Unix epoch seconds or milliseconds.
        number = float(text)
        if len(text.split(".")[0]) == 13:
            number /= 1000
        try:
            return datetime.fromtimestamp(number, tz=get_zone("UTC"))
        except (OverflowError, OSError, ValueError):
            return None

    for fmt in TIMESTAMP_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return ensure_aware(parsed, assume=get_zone(timezone))

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ensure_aware(parsed, assume=get_zone(timezone))


def parse_side(value: str) -> OrderSide | None:
    lowered = value.strip().lower()
    if lowered in BUY_WORDS:
        return OrderSide.BUY
    if lowered in SELL_WORDS:
        return OrderSide.SELL
    return None


def parse_decimal(value: str, *, allow_negative: bool = True) -> Decimal | None:
    text = value.strip()
    if not text:
        return None
    # Strip currency symbols and thousands separators before parsing.
    cleaned = re.sub(r"[^\d.,()\-+]", "", text)
    if not cleaned:
        return None
    # European format: 1.234,56 -> 1234.56
    if "," in cleaned and "." in cleaned and cleaned.rfind(",") > cleaned.rfind("."):
        cleaned = cleaned.replace(".", "").replace(",", ".")
    try:
        parsed = to_decimal(cleaned)
    except MoneyError:
        return None
    if not allow_negative and parsed < 0:
        return abs(parsed)
    return parsed


def normalize_symbol(value: str) -> str:
    """Upper-case and strip decoration brokers add (``/ES``, ``ES.CME``, ``AAPL  US``)."""
    text = value.strip().upper()
    text = text.lstrip("/").replace(" ", "")
    if "." in text and not text.endswith("."):
        head, _, tail = text.rpartition(".")
        if head and tail.isalpha() and len(tail) <= 4:
            text = head
    return text


__all__ = [
    "BUY_WORDS",
    "FIELD_SYNONYMS",
    "MAX_PREVIEW_ROWS",
    "SELL_WORDS",
    "TIMESTAMP_FORMATS",
    "ColumnInfo",
    "CsvParseError",
    "Inspection",
    "decode_bytes",
    "detect_field",
    "inspect_csv",
    "iter_rows",
    "normalize_symbol",
    "parse_decimal",
    "parse_side",
    "parse_timestamp",
    "sniff_delimiter",
]
