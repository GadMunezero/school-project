"""Per-trade derived values: risk, R multiple, excursions.

Each function states its definition precisely and returns ``None`` rather than a misleading zero
when the inputs do not define the quantity. ``docs/FINANCIALS.md`` mirrors these definitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from tradeloom.core.enums import Direction
from tradeloom.core.money import ONE, mul, quantize_money, quantize_price, quantize_ratio, safe_div


def risk_per_unit(
    entry_price: Decimal, stop_loss: Decimal | None, direction: Direction
) -> Decimal | None:
    """Distance from entry to stop, in price terms.

    ``None`` when there is no stop. A stop on the wrong side of entry (a long with a stop above
    the entry) is also ``None``: it does not describe risk, and treating it as risk would produce
    a nonsensical negative R.
    """
    if stop_loss is None:
        return None
    distance = (entry_price - stop_loss) * Decimal(direction.sign)
    if distance <= 0:
        return None
    return quantize_price(distance)


def risk_amount(
    entry_price: Decimal,
    stop_loss: Decimal | None,
    quantity: Decimal,
    direction: Direction,
    contract_multiplier: Decimal = ONE,
) -> Decimal | None:
    """Cash at risk if the initial stop is hit: ``risk_per_unit * quantity * multiplier``."""
    per_unit = risk_per_unit(entry_price, stop_loss, direction)
    if per_unit is None:
        return None
    return quantize_money(mul(mul(per_unit, quantity), contract_multiplier))


def r_multiple(net_pnl: Decimal, risk: Decimal | None) -> Decimal | None:
    """``net_pnl / risk_amount``.

    Undefined without a risk amount — a trade with no stop and no declared risk has no R, and
    reporting 0R would drag every R-based average toward zero with fabricated data.
    """
    if risk is None or risk <= 0:
        return None
    ratio = safe_div(net_pnl, risk)
    return None if ratio is None else quantize_ratio(ratio)


def planned_reward_risk(
    entry_price: Decimal,
    stop_loss: Decimal | None,
    take_profit: Decimal | None,
    direction: Direction,
) -> Decimal | None:
    """Reward-to-risk implied by the plan, before any execution."""
    risk = risk_per_unit(entry_price, stop_loss, direction)
    if risk is None or take_profit is None:
        return None
    reward = (take_profit - entry_price) * Decimal(direction.sign)
    if reward <= 0:
        return None
    ratio = safe_div(reward, risk)
    return None if ratio is None else quantize_ratio(ratio)


@dataclass(slots=True)
class Excursion:
    """Maximum favourable/adverse excursion over a holding period.

    Prices are the extreme *prices* reached; amounts are those extremes converted to account
    currency at the trade's size. Both are relative to the average entry price.
    """

    mfe_price: Decimal | None
    mae_price: Decimal | None
    mfe_amount: Decimal | None
    mae_amount: Decimal | None
    source: str | None


@dataclass(slots=True)
class Bar:
    opened_at: datetime
    high: Decimal
    low: Decimal


def compute_excursion(
    bars: list[Bar],
    *,
    entry_price: Decimal,
    quantity: Decimal,
    direction: Direction,
    contract_multiplier: Decimal = ONE,
    entry_timestamp: datetime | None = None,
    exit_timestamp: datetime | None = None,
    source: str | None = None,
) -> Excursion:
    """Scan candles covering the holding period for the best and worst prices reached.

    Bars are filtered to ``[entry_timestamp, exit_timestamp]`` when those are supplied. With no
    covering bars every field is ``None`` — MFE/MAE are simply unknown for that trade, and the UI
    says so rather than showing a zero.
    """
    window = [
        bar
        for bar in bars
        if (entry_timestamp is None or bar.opened_at >= entry_timestamp)
        and (exit_timestamp is None or bar.opened_at <= exit_timestamp)
    ]
    if not window:
        return Excursion(None, None, None, None, None)

    highest = max(bar.high for bar in window)
    lowest = min(bar.low for bar in window)

    if direction is Direction.LONG:
        favourable_price, adverse_price = highest, lowest
    else:
        favourable_price, adverse_price = lowest, highest

    sign = Decimal(direction.sign)
    mfe_move = (favourable_price - entry_price) * sign
    mae_move = (entry_price - adverse_price) * sign

    # Excursions are magnitudes: a trade that never moved in your favour has an MFE of 0, not a
    # negative number.
    mfe_move = max(mfe_move, Decimal(0))
    mae_move = max(mae_move, Decimal(0))

    scale = mul(quantity, contract_multiplier)
    return Excursion(
        mfe_price=quantize_price(favourable_price),
        mae_price=quantize_price(adverse_price),
        mfe_amount=quantize_money(mul(mfe_move, scale)),
        mae_amount=quantize_money(mul(mae_move, scale)),
        source=source,
    )


def efficiency_ratio(net_pnl: Decimal, mfe_amount: Decimal | None) -> Decimal | None:
    """How much of the best available move the trade actually captured.

    ``net_pnl / mfe_amount``. 1.0 means the exit was at the high-water mark; 0.3 means two thirds
    of the favourable move was given back.
    """
    if mfe_amount is None or mfe_amount <= 0:
        return None
    ratio = safe_div(net_pnl, mfe_amount)
    return None if ratio is None else quantize_ratio(ratio)


__all__ = [
    "Bar",
    "Excursion",
    "compute_excursion",
    "efficiency_ratio",
    "planned_reward_risk",
    "r_multiple",
    "risk_amount",
    "risk_per_unit",
]
