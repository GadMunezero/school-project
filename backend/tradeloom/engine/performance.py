"""Performance analytics.

Every metric's definition is stated here and mirrored in ``docs/FINANCIALS.md``. Two conventions
run through the whole module:

* **Money stays Decimal.** Ratios that feed statistics (Sharpe, Sortino) convert to float only at
  the point of taking a square root, and the result is converted back.
* **Undefined is ``None``, not zero.** A profit factor with no losing trades is undefined, not
  infinite and not zero. A Sharpe ratio over a single period is undefined. Returning 0 for these
  would silently corrupt every aggregate built on top of them.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from tradeloom.core.enums import Direction
from tradeloom.core.money import (
    HUNDRED,
    ZERO,
    mul,
    quantize_money,
    quantize_percent,
    quantize_ratio,
    safe_div,
)
from tradeloom.core.timeutil import to_zone, trading_day
from tradeloom.engine.portfolio import SimTrade

#: R-multiple histogram edges. Buckets are [-inf,-3), [-3,-2), ... [3, +inf).
R_BUCKET_EDGES: tuple[Decimal, ...] = (
    Decimal(-3),
    Decimal(-2),
    Decimal(-1),
    Decimal(0),
    Decimal(1),
    Decimal(2),
    Decimal(3),
)


@dataclass(slots=True)
class EquitySample:
    timestamp: datetime
    equity: Decimal
    cash: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    unrealized_pnl: Decimal = ZERO
    open_positions: int = 0
    exposure: Decimal = ZERO
    #: Which trading day this sample belongs to, when the caller knows the market it came from.
    #: Journal analytics stamps it from the trade's asset type so futures and FX land in the
    #: session that was actually open. Left unset, the day falls back to the local calendar date.
    trading_day: date | None = None


@dataclass(slots=True)
class DrawdownEpisode:
    started_at: datetime
    trough_at: datetime
    recovered_at: datetime | None
    peak_equity: Decimal
    trough_equity: Decimal
    depth: Decimal
    depth_percent: Decimal
    duration_seconds: int
    recovery_seconds: int | None


@dataclass(slots=True)
class PerformanceReport:
    metrics: dict[str, Any] = field(default_factory=dict)
    breakdowns: dict[str, Any] = field(default_factory=dict)
    drawdowns: list[DrawdownEpisode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"metrics": self.metrics, "breakdowns": self.breakdowns}


def _d(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


class PerformanceAnalyzer:
    """Computes the full metric set from closed trades plus an equity curve."""

    def __init__(
        self,
        *,
        trades: list[SimTrade],
        equity_curve: list[EquitySample],
        initial_capital: Decimal,
        periods_per_year: int = 252,
        risk_free_rate_percent: Decimal = ZERO,
        bars_in_market: int = 0,
        total_bars: int = 0,
        timezone: str = "UTC",
        symbol: str = "",
    ) -> None:
        self.trades = trades
        self.equity_curve = equity_curve
        self.initial_capital = initial_capital
        self.periods_per_year = max(1, periods_per_year)
        self.risk_free_rate_percent = risk_free_rate_percent
        self.bars_in_market = bars_in_market
        self.total_bars = total_bars
        self.timezone = timezone
        self.symbol = symbol

    # ------------------------------------------------------------------

    def analyse(self) -> PerformanceReport:
        drawdowns = self._drawdown_episodes()
        return PerformanceReport(
            metrics=self._metrics(drawdowns),
            breakdowns=self._breakdowns(),
            drawdowns=drawdowns,
        )

    # -- core metrics --------------------------------------------------

    def _metrics(self, drawdowns: list[DrawdownEpisode]) -> dict[str, Any]:
        trades = self.trades
        count = len(trades)
        pnls = [t.net_pnl for t in trades]
        winners = [t for t in trades if t.net_pnl > 0]
        losers = [t for t in trades if t.net_pnl < 0]
        breakeven = [t for t in trades if t.net_pnl == 0]

        gross_profit = quantize_money(sum((t.net_pnl for t in winners), ZERO))
        gross_loss = quantize_money(sum((t.net_pnl for t in losers), ZERO))  # negative
        net_profit = quantize_money(sum(pnls, ZERO))

        final_equity = self.equity_curve[-1].equity if self.equity_curve else self.initial_capital
        total_return_pct = safe_div(
            mul(final_equity - self.initial_capital, HUNDRED), self.initial_capital
        )

        win_rate = safe_div(Decimal(len(winners)) * HUNDRED, Decimal(count)) if count else None
        loss_rate = safe_div(Decimal(len(losers)) * HUNDRED, Decimal(count)) if count else None

        average_trade = safe_div(net_profit, Decimal(count)) if count else None
        average_winner = safe_div(gross_profit, Decimal(len(winners))) if winners else None
        average_loser = safe_div(gross_loss, Decimal(len(losers))) if losers else None

        # Expectancy per trade in account currency:
        #   P(win) * avg_win + P(loss) * avg_loss   (avg_loss is negative)
        expectancy = average_trade

        profit_factor = safe_div(gross_profit, abs(gross_loss)) if gross_loss != 0 else None
        payoff_ratio = (
            safe_div(average_winner, abs(average_loser))
            if average_winner is not None and average_loser not in (None, ZERO)
            else None
        )

        max_dd = max((d.depth for d in drawdowns), default=ZERO)
        max_dd_pct = max((d.depth_percent for d in drawdowns), default=ZERO)
        longest_dd = max((d.duration_seconds for d in drawdowns), default=0)

        r_values = [t.r_multiple for t in trades if t.r_multiple is not None]
        holding = [t.holding_seconds for t in trades if t.holding_seconds is not None]

        streak_wins, streak_losses = self._streaks()
        sharpe, sortino = self._risk_adjusted()
        cagr = self._cagr(final_equity)
        calmar = safe_div(cagr, max_dd_pct) if cagr is not None and max_dd_pct > 0 else None

        exposure = (
            safe_div(Decimal(self.bars_in_market) * HUNDRED, Decimal(self.total_bars))
            if self.total_bars
            else None
        )

        longs = [t for t in trades if t.direction is Direction.LONG]
        shorts = [t for t in trades if t.direction is Direction.SHORT]

        return {
            # headline
            "initial_capital": _d(self.initial_capital),
            "final_equity": _d(quantize_money(final_equity)),
            "net_profit": _d(net_profit),
            "total_return_percent": _d(
                quantize_percent(total_return_pct) if total_return_pct is not None else None
            ),
            "gross_profit": _d(gross_profit),
            "gross_loss": _d(gross_loss),
            # counts
            "total_trades": count,
            "winning_trades": len(winners),
            "losing_trades": len(losers),
            "breakeven_trades": len(breakeven),
            "win_rate": _d(quantize_percent(win_rate) if win_rate is not None else None),
            "loss_rate": _d(quantize_percent(loss_rate) if loss_rate is not None else None),
            # averages
            "average_trade": _d(
                quantize_money(average_trade) if average_trade is not None else None
            ),
            "average_winner": _d(
                quantize_money(average_winner) if average_winner is not None else None
            ),
            "average_loser": _d(
                quantize_money(average_loser) if average_loser is not None else None
            ),
            "expectancy": _d(quantize_money(expectancy) if expectancy is not None else None),
            "profit_factor": _d(
                quantize_ratio(profit_factor) if profit_factor is not None else None
            ),
            "payoff_ratio": _d(quantize_ratio(payoff_ratio) if payoff_ratio is not None else None),
            "largest_winner": _d(max((t.net_pnl for t in trades), default=None)),
            "largest_loser": _d(min((t.net_pnl for t in trades), default=None)),
            # risk
            "max_drawdown": _d(quantize_money(max_dd)),
            "max_drawdown_percent": _d(quantize_percent(max_dd_pct)),
            "max_drawdown_duration_seconds": longest_dd,
            "sharpe_ratio": _d(sharpe),
            "sortino_ratio": _d(sortino),
            "calmar_ratio": _d(quantize_ratio(calmar) if calmar is not None else None),
            "cagr_percent": _d(quantize_percent(cagr) if cagr is not None else None),
            "annualized_return_percent": _d(quantize_percent(cagr) if cagr is not None else None),
            "exposure_percent": _d(quantize_percent(exposure) if exposure is not None else None),
            # streaks
            "max_consecutive_wins": streak_wins,
            "max_consecutive_losses": streak_losses,
            # R statistics
            "average_r": _d(
                quantize_ratio(safe_div(sum(r_values, ZERO), Decimal(len(r_values))))
                if r_values
                else None
            ),
            "median_r": _d(quantize_ratio(_median(r_values)) if r_values else None),
            "total_r": _d(quantize_ratio(sum(r_values, ZERO)) if r_values else None),
            "trades_with_r": len(r_values),
            # excursions
            "average_mfe": _d(_mean([t.mfe_amount for t in trades])),
            "average_mae": _d(_mean([t.mae_amount for t in trades])),
            # duration
            "average_holding_seconds": int(sum(holding) / len(holding)) if holding else None,
            "median_holding_seconds": int(statistics.median(holding)) if holding else None,
            # costs
            "total_commission": _d(quantize_money(sum((t.commission for t in trades), ZERO))),
            "total_slippage": _d(quantize_money(sum((t.slippage for t in trades), ZERO))),
            # directional
            "long": _side_stats(longs),
            "short": _side_stats(shorts),
        }

    # -- drawdown ------------------------------------------------------

    def _drawdown_episodes(self) -> list[DrawdownEpisode]:
        """Every peak-to-trough-to-recovery episode in the equity curve.

        An episode opens when equity falls below the running peak and closes when equity reaches
        that peak again. An episode still open at the end of the run has ``recovered_at = None``,
        which is meaningfully different from "recovered on the last bar".
        """
        if len(self.equity_curve) < 2:
            return []

        episodes: list[DrawdownEpisode] = []
        peak = self.equity_curve[0].equity
        peak_at = self.equity_curve[0].timestamp
        trough = peak
        trough_at = peak_at
        in_drawdown = False

        for sample in self.equity_curve[1:]:
            if sample.equity >= peak:
                if in_drawdown:
                    episodes.append(_episode(peak, peak_at, trough, trough_at, sample.timestamp))
                    in_drawdown = False
                peak = sample.equity
                peak_at = sample.timestamp
                trough = sample.equity
                trough_at = sample.timestamp
                continue

            if not in_drawdown:
                in_drawdown = True
                trough = sample.equity
                trough_at = sample.timestamp
            elif sample.equity < trough:
                trough = sample.equity
                trough_at = sample.timestamp

        if in_drawdown:
            episodes.append(_episode(peak, peak_at, trough, trough_at, None))
        return episodes

    # -- risk-adjusted -------------------------------------------------

    def _period_returns(self) -> list[float]:
        """Simple per-period returns of the equity curve.

        Periods with zero starting equity are skipped rather than treated as -100%, which would
        happen only in a blown-up account and would poison the statistics.
        """
        returns: list[float] = []
        for previous, current in zip(self.equity_curve, self.equity_curve[1:], strict=False):
            if previous.equity <= 0:
                continue
            returns.append(float((current.equity - previous.equity) / previous.equity))
        return returns

    def _risk_adjusted(self) -> tuple[Decimal | None, Decimal | None]:
        """Annualised Sharpe and Sortino.

        Sharpe  = (mean(r) - rf_per_period) / stdev(r, sample) * sqrt(periods_per_year)
        Sortino = (mean(r) - rf_per_period) / downside_deviation * sqrt(periods_per_year)

        where downside deviation is the root-mean-square of returns below the MAR (the risk-free
        rate), computed over *all* periods — the standard definition, not the mean of negative
        returns only.
        """
        returns = self._period_returns()
        if len(returns) < 2:
            return None, None

        rf_per_period = float(self.risk_free_rate_percent) / 100.0 / self.periods_per_year
        mean = statistics.fmean(returns)
        excess = mean - rf_per_period
        annualiser = math.sqrt(self.periods_per_year)

        stdev = statistics.stdev(returns)
        sharpe = (
            Decimal(str(excess / stdev * annualiser)).quantize(Decimal("0.000001"))
            if stdev > 0
            else None
        )

        downside = [min(0.0, r - rf_per_period) for r in returns]
        downside_deviation = math.sqrt(sum(d * d for d in downside) / len(downside))
        sortino = (
            Decimal(str(excess / downside_deviation * annualiser)).quantize(Decimal("0.000001"))
            if downside_deviation > 0
            else None
        )
        return sharpe, sortino

    def _cagr(self, final_equity: Decimal) -> Decimal | None:
        """Compound annual growth rate from the first to the last equity sample.

        ``None`` for runs shorter than a day or starting from non-positive capital — annualising
        a three-hour backtest produces a number that looks authoritative and means nothing.
        """
        if len(self.equity_curve) < 2 or self.initial_capital <= 0 or final_equity <= 0:
            return None
        span = self.equity_curve[-1].timestamp - self.equity_curve[0].timestamp
        years = span.total_seconds() / (365.25 * 24 * 3600)
        if years < (1.0 / 365.25):
            return None
        growth = float(final_equity / self.initial_capital)
        return Decimal(str((growth ** (1 / years) - 1) * 100)).quantize(Decimal("0.00000001"))

    def _streaks(self) -> tuple[int, int]:
        best_wins = best_losses = current_wins = current_losses = 0
        for trade in self.trades:
            if trade.net_pnl > 0:
                current_wins += 1
                current_losses = 0
            elif trade.net_pnl < 0:
                current_losses += 1
                current_wins = 0
            else:
                current_wins = current_losses = 0
            best_wins = max(best_wins, current_wins)
            best_losses = max(best_losses, current_losses)
        return best_wins, best_losses

    # -- breakdowns ----------------------------------------------------

    def _trading_day(self, trade: SimTrade) -> date:
        """The session a trade was entered in, by the market's own clock where it has one."""
        return trading_day(trade.entry_timestamp, trade.asset_type, self.timezone)

    def _breakdowns(self) -> dict[str, Any]:
        return {
            "by_symbol": _group(self.trades, lambda t: self.symbol or "—"),
            "by_direction": _group(self.trades, lambda t: t.direction.value),
            # Weekday and month follow the trading day: a futures trade entered Sunday evening
            # belongs to Monday's session, and a breakdown row labelled "Sunday" would name a
            # session the market never held.
            "by_weekday": _group(self.trades, lambda t: str(self._trading_day(t).weekday())),
            # The hour is deliberately wall-clock. "What time do I enter?" is a question about the
            # clock the trader was watching, not about which session the fill was booked to.
            "by_hour": _group(
                self.trades, lambda t: str(to_zone(t.entry_timestamp, self.timezone).hour)
            ),
            "by_month": _group(self.trades, lambda t: self._trading_day(t).strftime("%Y-%m")),
            "by_exit_reason": _group(self.trades, lambda t: t.exit_reason or "unknown"),
            "r_distribution": self._r_distribution(),
            "monthly_returns": self._monthly_returns(),
            "daily_returns": self._daily_returns(),
            "mfe_mae": [
                {
                    "sequence": t.sequence,
                    "mfe": _d(t.mfe_amount),
                    "mae": _d(t.mae_amount),
                    "net_pnl": _d(t.net_pnl),
                    "r_multiple": _d(t.r_multiple),
                }
                for t in self.trades
            ],
        }

    def _r_distribution(self) -> list[dict[str, Any]]:
        buckets: list[dict[str, Any]] = []
        values = [t.r_multiple for t in self.trades if t.r_multiple is not None]
        edges = R_BUCKET_EDGES

        def label(low: Decimal | None, high: Decimal | None) -> str:
            if low is None:
                return f"< {high}R"
            if high is None:
                return f"≥ {low}R"
            return f"{low}R to {high}R"

        bounds: list[tuple[Decimal | None, Decimal | None]] = [(None, edges[0])]
        bounds += [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]
        bounds.append((edges[-1], None))

        for low, high in bounds:
            matched = [
                value
                for value in values
                if (low is None or value >= low) and (high is None or value < high)
            ]
            buckets.append(
                {
                    "label": label(low, high),
                    "lower": _d(low),
                    "upper": _d(high),
                    "count": len(matched),
                }
            )
        return buckets

    def _monthly_returns(self) -> list[dict[str, Any]]:
        if not self.equity_curve:
            return []
        by_month: dict[str, tuple[Decimal, Decimal]] = {}
        for sample in self.equity_curve:
            key = (
                sample.trading_day.strftime("%Y-%m")
                if sample.trading_day is not None
                else to_zone(sample.timestamp, self.timezone).strftime("%Y-%m")
            )
            if key not in by_month:
                by_month[key] = (sample.equity, sample.equity)
            else:
                first, _ = by_month[key]
                by_month[key] = (first, sample.equity)

        rows: list[dict[str, Any]] = []
        for key in sorted(by_month):
            start, end = by_month[key]
            change = safe_div(mul(end - start, HUNDRED), start) if start > 0 else None
            rows.append(
                {
                    "period": key,
                    "start_equity": _d(quantize_money(start)),
                    "end_equity": _d(quantize_money(end)),
                    "return_percent": _d(quantize_percent(change) if change is not None else None),
                    "net_change": _d(quantize_money(end - start)),
                }
            )
        return rows

    def _daily_returns(self) -> list[dict[str, Any]]:
        if not self.equity_curve:
            return []
        by_day: dict[str, tuple[Decimal, Decimal]] = {}
        for sample in self.equity_curve:
            # Prefer the trading day the sample was stamped with, so these rows agree with the
            # calendar breakdown for markets whose day is not a calendar day.
            key = (
                sample.trading_day.isoformat()
                if sample.trading_day is not None
                else to_zone(sample.timestamp, self.timezone).strftime("%Y-%m-%d")
            )
            if key not in by_day:
                by_day[key] = (sample.equity, sample.equity)
            else:
                first, _ = by_day[key]
                by_day[key] = (first, sample.equity)

        rows: list[dict[str, Any]] = []
        for key in sorted(by_day):
            start, end = by_day[key]
            rows.append(
                {
                    "date": key,
                    "equity": _d(quantize_money(end)),
                    "net_change": _d(quantize_money(end - start)),
                }
            )
        return rows


# ---------------------------------------------------------------------------


def _episode(
    peak: Decimal,
    peak_at: datetime,
    trough: Decimal,
    trough_at: datetime,
    recovered_at: datetime | None,
) -> DrawdownEpisode:
    depth = quantize_money(peak - trough)
    depth_percent = safe_div(mul(depth, HUNDRED), peak) if peak > 0 else None
    return DrawdownEpisode(
        started_at=peak_at,
        trough_at=trough_at,
        recovered_at=recovered_at,
        peak_equity=quantize_money(peak),
        trough_equity=quantize_money(trough),
        depth=depth,
        depth_percent=quantize_percent(depth_percent) if depth_percent is not None else ZERO,
        duration_seconds=int(((recovered_at or trough_at) - peak_at).total_seconds()),
        recovery_seconds=(
            int((recovered_at - trough_at).total_seconds()) if recovered_at else None
        ),
    )


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def _mean(values: list[Decimal | None]) -> Decimal | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return quantize_money(sum(present, ZERO) / Decimal(len(present)))


def _side_stats(trades: list[SimTrade]) -> dict[str, Any]:
    if not trades:
        return {"trades": 0, "net_pnl": None, "win_rate": None, "average_trade": None}
    net = quantize_money(sum((t.net_pnl for t in trades), ZERO))
    wins = [t for t in trades if t.net_pnl > 0]
    return {
        "trades": len(trades),
        "net_pnl": _d(net),
        "win_rate": _d(
            quantize_percent(safe_div(Decimal(len(wins)) * HUNDRED, Decimal(len(trades))))
        ),
        "average_trade": _d(quantize_money(net / Decimal(len(trades)))),
    }


def _group(trades: list[SimTrade], key) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    grouped: dict[str, list[SimTrade]] = {}
    for trade in trades:
        grouped.setdefault(key(trade), []).append(trade)

    rows: list[dict[str, Any]] = []
    for label in sorted(grouped):
        bucket = grouped[label]
        net = quantize_money(sum((t.net_pnl for t in bucket), ZERO))
        wins = [t for t in bucket if t.net_pnl > 0]
        losses = [t for t in bucket if t.net_pnl < 0]
        gross_profit = quantize_money(sum((t.net_pnl for t in wins), ZERO))
        gross_loss = abs(quantize_money(sum((t.net_pnl for t in losses), ZERO)))
        rows.append(
            {
                "label": label,
                "trades": len(bucket),
                "net_pnl": _d(net),
                "win_rate": _d(
                    quantize_percent(safe_div(Decimal(len(wins)) * HUNDRED, Decimal(len(bucket))))
                ),
                "average_trade": _d(quantize_money(net / Decimal(len(bucket)))),
                "profit_factor": _d(
                    quantize_ratio(safe_div(gross_profit, gross_loss)) if gross_loss > 0 else None
                ),
            }
        )
    return rows


__all__ = [
    "R_BUCKET_EDGES",
    "DrawdownEpisode",
    "EquitySample",
    "PerformanceAnalyzer",
    "PerformanceReport",
]
