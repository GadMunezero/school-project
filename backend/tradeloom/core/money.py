"""Decimal money primitives and the project-wide rounding contract.

Rules (also documented in ``docs/FINANCIALS.md``):

1. Every monetary value, price, and quantity is a :class:`decimal.Decimal`. Floats are only used
   for statistical ratios (Sharpe, correlation, ...) where the inputs are already returns.
2. Intermediate arithmetic runs at :data:`WORKING_PRECISION` significant digits with
   ``ROUND_HALF_EVEN`` (banker's rounding) — the same rule used by clearing houses for tie
   breaking, and the only rounding mode used anywhere in Tradeloom.
3. Values are quantised only at well-defined boundaries:
   * prices  -> :data:`PRICE_SCALE` decimal places (10)
   * quantities -> :data:`QUANTITY_SCALE` decimal places (10)
   * stored money -> :data:`MONEY_SCALE` decimal places (10)
   * settled money (balances, realised P&L that hits a cash balance) -> the currency's minor
     unit, e.g. 2 for USD/EUR, 0 for JPY, 8 for BTC.
4. Division by zero never raises: :func:`safe_div` returns ``None`` so callers must decide what an
   undefined ratio means rather than silently producing ``0``.

Never construct a ``Decimal`` from a ``float`` outside :func:`to_decimal` — binary floats carry
representation error into the ledger.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from typing import Final

WORKING_PRECISION: Final[int] = 34
ROUNDING: Final[str] = ROUND_HALF_EVEN

PRICE_SCALE: Final[int] = 10
QUANTITY_SCALE: Final[int] = 10
MONEY_SCALE: Final[int] = 10
PERCENT_SCALE: Final[int] = 8
RATIO_SCALE: Final[int] = 6

ZERO: Final[Decimal] = Decimal(0)
ONE: Final[Decimal] = Decimal(1)
HUNDRED: Final[Decimal] = Decimal(100)

#: Minor units per currency. Anything unlisted falls back to :data:`DEFAULT_MINOR_UNITS`.
CURRENCY_MINOR_UNITS: Final[dict[str, int]] = {
    "USD": 2,
    "EUR": 2,
    "GBP": 2,
    "CHF": 2,
    "CAD": 2,
    "AUD": 2,
    "NZD": 2,
    "SEK": 2,
    "NOK": 2,
    "SGD": 2,
    "HKD": 2,
    "MXN": 2,
    "BRL": 2,
    "ZAR": 2,
    "INR": 2,
    "JPY": 0,
    "KRW": 0,
    "CLP": 0,
    "BTC": 8,
    "ETH": 8,
    "USDT": 6,
    "USDC": 6,
}
DEFAULT_MINOR_UNITS: Final[int] = 2

Number = Decimal | int | str | float


class MoneyError(ValueError):
    """Raised when a value cannot be interpreted as a decimal amount."""


def to_decimal(value: Number | None, *, default: Decimal | None = None) -> Decimal:
    """Coerce ``value`` to :class:`Decimal` without float representation error.

    Floats are routed through ``repr`` so ``0.1`` becomes ``Decimal("0.1")`` rather than the
    binary expansion. ``None`` returns ``default`` (or raises when no default is given).
    """
    if value is None:
        if default is None:
            raise MoneyError("expected a numeric value, got None")
        return default
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise MoneyError(f"non-finite decimal: {value}")
        return value
    if isinstance(value, bool):  # bool is an int subclass; refuse it explicitly
        raise MoneyError("boolean is not a valid monetary value")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise MoneyError(f"non-finite float: {value}")
        return Decimal(repr(value))
    text = str(value).strip().replace(",", "").replace("_", "")
    if text in {"", "-", "."}:
        if default is None:
            raise MoneyError(f"cannot parse decimal from {value!r}")
        return default
    if text.endswith("%"):
        text = text[:-1]
    if text.startswith("(") and text.endswith(")"):  # accounting negatives: (123.45)
        text = "-" + text[1:-1]
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:  # pragma: no cover - defensive
        if default is not None:
            return default
        raise MoneyError(f"cannot parse decimal from {value!r}") from exc
    if not parsed.is_finite():
        raise MoneyError(f"non-finite value: {value!r}")
    return parsed


def _quantize(value: Decimal, places: int) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = WORKING_PRECISION
        ctx.rounding = ROUNDING
        return value.quantize(Decimal(1).scaleb(-places), rounding=ROUNDING)


def quantize_price(value: Number) -> Decimal:
    return _quantize(to_decimal(value), PRICE_SCALE)


def quantize_quantity(value: Number) -> Decimal:
    return _quantize(to_decimal(value), QUANTITY_SCALE)


def quantize_money(value: Number) -> Decimal:
    """Storage-scale quantisation (10dp). Use for P&L, fees and any persisted amount."""
    return _quantize(to_decimal(value), MONEY_SCALE)


def quantize_percent(value: Number) -> Decimal:
    return _quantize(to_decimal(value), PERCENT_SCALE)


def quantize_ratio(value: Number) -> Decimal:
    return _quantize(to_decimal(value), RATIO_SCALE)


def minor_units(currency: str) -> int:
    return CURRENCY_MINOR_UNITS.get(currency.upper(), DEFAULT_MINOR_UNITS)


def settle(value: Number, currency: str) -> Decimal:
    """Round to the currency's minor unit. Use when money moves into a cash balance."""
    return _quantize(to_decimal(value), minor_units(currency))


def mul(a: Number, b: Number) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = WORKING_PRECISION
        ctx.rounding = ROUNDING
        return to_decimal(a) * to_decimal(b)


def add(*values: Number) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = WORKING_PRECISION
        ctx.rounding = ROUNDING
        total = ZERO
        for value in values:
            total += to_decimal(value)
        return total


def safe_div(numerator: Number, denominator: Number) -> Decimal | None:
    """Divide, returning ``None`` when the denominator is zero.

    Callers must handle ``None`` explicitly. Returning ``0`` for an undefined ratio (a profit
    factor with no losses, an R multiple with no risk) is a silent lie, so we refuse to do it.
    """
    den = to_decimal(denominator)
    if den == 0:
        return None
    with localcontext() as ctx:
        ctx.prec = WORKING_PRECISION
        ctx.rounding = ROUNDING
        return to_decimal(numerator) / den


def percent_change(from_value: Number, to_value: Number) -> Decimal | None:
    """``(to - from) / |from| * 100``. ``None`` when the base is zero."""
    base = abs(to_decimal(from_value))
    if base == 0:
        return None
    ratio = safe_div(to_decimal(to_value) - to_decimal(from_value), base)
    return quantize_percent(mul(ratio, HUNDRED)) if ratio is not None else None


def clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


def is_zero(value: Number, *, tolerance: Decimal = Decimal("1e-10")) -> bool:
    """Quantity comparison helper — quantities are stored at 10dp so anything below is noise."""
    return abs(to_decimal(value)) <= tolerance


def decimal_to_float(value: Decimal | None) -> float | None:
    """Only for statistics and JSON chart payloads — never for ledger arithmetic."""
    return None if value is None else float(value)


__all__ = [
    "CURRENCY_MINOR_UNITS",
    "HUNDRED",
    "MONEY_SCALE",
    "ONE",
    "PERCENT_SCALE",
    "PRICE_SCALE",
    "QUANTITY_SCALE",
    "ROUNDING",
    "WORKING_PRECISION",
    "ZERO",
    "MoneyError",
    "add",
    "clamp",
    "decimal_to_float",
    "is_zero",
    "minor_units",
    "mul",
    "percent_change",
    "quantize_money",
    "quantize_percent",
    "quantize_price",
    "quantize_quantity",
    "quantize_ratio",
    "safe_div",
    "settle",
    "to_decimal",
]
