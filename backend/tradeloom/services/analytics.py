"""Journal analytics.

Everything here is computed from real trade rows. There is no synthetic data path, and an empty
workspace produces empty results rather than placeholder numbers.

**No duplicated maths.** Journal trades are mapped onto the same :class:`~tradeloom.engine.
portfolio.SimTrade` shape the backtester produces and fed through the same
:class:`~tradeloom.engine.performance.PerformanceAnalyzer`. A win rate means the same thing on the
dashboard as it does in a backtest report, because it is literally the same function.

Journal-specific breakdowns (by account, strategy, setup, tag) are computed in SQL because they
need joins the engine has no concept of.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core.enums import TradeStatus
from tradeloom.core.money import HUNDRED, ZERO, quantize_money, quantize_percent, safe_div
from tradeloom.core.timeutil import ensure_aware, trading_day, utcnow
from tradeloom.engine.performance import EquitySample, PerformanceAnalyzer
from tradeloom.engine.portfolio import SimTrade
from tradeloom.models.account import Account
from tradeloom.models.strategy import Setup, Strategy, Tag
from tradeloom.models.trading import Trade, TradeTag
from tradeloom.schemas.trade import TradeFilters
from tradeloom.services.trades import TradeService

#: Guard rail: analytics never loads an unbounded number of rows into memory. Beyond this the
#: response says so, and the user is expected to narrow the filter.
MAX_ANALYTICS_TRADES = 50_000


@dataclass(slots=True)
class AnalyticsResult:
    metrics: dict[str, Any]
    breakdowns: dict[str, Any]
    equity_curve: list[dict[str, Any]]
    drawdown_curve: list[dict[str, Any]]
    trade_count: int
    truncated: bool
    generated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": self.metrics,
            "breakdowns": self.breakdowns,
            "equity_curve": self.equity_curve,
            "drawdown_curve": self.drawdown_curve,
            "trade_count": self.trade_count,
            "truncated": self.truncated,
            "generated_at": self.generated_at.isoformat(),
        }


def _s(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


class AnalyticsService:
    def __init__(self, session: AsyncSession, organization_id: uuid.UUID) -> None:
        self.session = session
        self.organization_id = organization_id
        self.trades = TradeService(session, organization_id)

    # ------------------------------------------------------------------

    async def analyse(
        self, filters: TradeFilters, *, timezone: str | None = None
    ) -> AnalyticsResult:
        zone = timezone or await self._primary_timezone(filters)
        rows = await self._load_closed_trades(filters)
        truncated = len(rows) > MAX_ANALYTICS_TRADES
        if truncated:
            rows = rows[:MAX_ANALYTICS_TRADES]

        starting_capital = await self._starting_capital(filters)
        equity_curve = self._build_equity_curve(rows, starting_capital, zone)

        analyzer = PerformanceAnalyzer(
            trades=[_to_sim_trade(index + 1, trade) for index, trade in enumerate(rows)],
            equity_curve=equity_curve,
            initial_capital=starting_capital,
            # Journal equity is sampled per closed trade, so annualisation uses a daily
            # convention rather than a bar count.
            periods_per_year=252,
            timezone=zone,
        )
        report = analyzer.analyse()

        breakdowns = dict(report.breakdowns)
        breakdowns["by_symbol"] = _group_rows(rows, lambda t: t.symbol)
        breakdowns["by_account"] = await self._group_by_label(rows, Account, lambda t: t.account_id)
        breakdowns["by_strategy"] = await self._group_by_label(
            rows, Strategy, lambda t: t.strategy_id
        )
        breakdowns["by_setup"] = await self._group_by_label(rows, Setup, lambda t: t.setup_id)
        breakdowns["by_tag"] = await self._group_by_tag(rows)
        breakdowns["by_session"] = _group_rows(
            rows, lambda t: t.session.value if t.session else "unknown"
        )
        breakdowns["calendar"] = self._calendar(rows, zone)

        return AnalyticsResult(
            metrics=report.metrics,
            breakdowns=breakdowns,
            equity_curve=[
                {
                    "timestamp": sample.timestamp.isoformat(),
                    "equity": _s(sample.equity),
                    "realized_pnl": _s(sample.realized_pnl),
                }
                for sample in equity_curve
            ],
            drawdown_curve=self._drawdown_curve(equity_curve),
            trade_count=len(rows),
            truncated=truncated,
            generated_at=utcnow(),
        )

    # ------------------------------------------------------------------

    async def _load_closed_trades(self, filters: TradeFilters) -> list[Trade]:
        stmt: Select[tuple[Trade]] = self.trades._apply_filters(
            self.trades.trades.select(), filters
        ).where(Trade.status == TradeStatus.CLOSED)
        stmt = stmt.order_by(Trade.exit_timestamp.asc(), Trade.id.asc()).limit(
            MAX_ANALYTICS_TRADES + 1
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def _starting_capital(self, filters: TradeFilters) -> Decimal:
        """Sum the initial balances of the accounts in scope.

        Falls back to 0 when no account exists — the equity curve is then a pure cumulative-P&L
        line, which is the honest representation rather than inventing a starting balance.
        """
        stmt = select(func.coalesce(func.sum(Account.initial_balance), 0)).where(
            Account.organization_id == self.organization_id, Account.deleted_at.is_(None)
        )
        if filters.account_ids:
            stmt = stmt.where(Account.id.in_(filters.account_ids))
        total = await self.session.scalar(stmt)
        return quantize_money(total or 0)

    async def _primary_timezone(self, filters: TradeFilters) -> str:
        stmt = select(Account.timezone).where(
            Account.organization_id == self.organization_id, Account.deleted_at.is_(None)
        )
        if filters.account_ids:
            stmt = stmt.where(Account.id.in_(filters.account_ids))
        result = await self.session.execute(stmt.limit(1))
        return result.scalar_one_or_none() or "UTC"

    def _build_equity_curve(
        self, trades: list[Trade], starting_capital: Decimal, zone: str
    ) -> list[EquitySample]:
        """One sample per closed trade, plus an opening point.

        Trades are already ordered by exit time, so the running total is the realised equity path
        a trader actually experienced.

        Each sample carries the trading day of the trade that produced it, so daily returns bucket
        the same way the calendar does — by the market's session for futures and FX, by the local
        date for everything else.
        """
        if not trades:
            return []
        first = trades[0]
        opened_at = ensure_aware(first.entry_timestamp)
        samples = [
            EquitySample(
                timestamp=opened_at,
                equity=starting_capital,
                trading_day=trading_day(opened_at, first.asset_type, zone),
            )
        ]

        running = starting_capital
        for trade in trades:
            running = quantize_money(running + trade.net_pnl)
            closed_at = ensure_aware(trade.exit_timestamp or trade.entry_timestamp)
            samples.append(
                EquitySample(
                    timestamp=closed_at,
                    equity=running,
                    realized_pnl=quantize_money(running - starting_capital),
                    trading_day=trading_day(closed_at, trade.asset_type, zone),
                )
            )
        return samples

    def _drawdown_curve(self, samples: list[EquitySample]) -> list[dict[str, Any]]:
        curve: list[dict[str, Any]] = []
        peak = samples[0].equity if samples else ZERO
        for sample in samples:
            peak = max(peak, sample.equity)
            depth = quantize_money(sample.equity - peak)
            percent = safe_div(depth * HUNDRED, peak) if peak > 0 else None
            curve.append(
                {
                    "timestamp": sample.timestamp.isoformat(),
                    "drawdown": _s(depth),
                    "drawdown_percent": _s(
                        quantize_percent(percent) if percent is not None else ZERO
                    ),
                    "peak": _s(peak),
                }
            )
        return curve

    def _calendar(self, trades: list[Trade], zone: str) -> list[dict[str, Any]]:
        """Per-day P&L, the source for the dashboard's calendar heatmap."""
        buckets: dict[date, dict[str, Any]] = {}
        for trade in trades:
            # The same trading day the snapshots use, so a heatmap cell and an equity-curve point
            # for one date describe the same set of trades.
            day = trading_day(trade.exit_timestamp or trade.entry_timestamp, trade.asset_type, zone)
            entry = buckets.setdefault(
                day, {"date": day.isoformat(), "net_pnl": ZERO, "trades": 0, "wins": 0}
            )
            entry["net_pnl"] = quantize_money(entry["net_pnl"] + trade.net_pnl)
            entry["trades"] += 1
            if trade.net_pnl > 0:
                entry["wins"] += 1
        return [{**value, "net_pnl": _s(value["net_pnl"])} for _, value in sorted(buckets.items())]

    async def _group_by_label(
        self, trades: list[Trade], model, key_getter
    ) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
        ids = {key_getter(trade) for trade in trades if key_getter(trade) is not None}
        labels: dict[uuid.UUID, str] = {}
        if ids:
            result = await self.session.execute(
                select(model.id, model.name).where(
                    model.organization_id == self.organization_id, model.id.in_(ids)
                )
            )
            labels = {row[0]: row[1] for row in result.all()}
        return _group_rows(
            trades,
            lambda t: labels.get(key_getter(t), "Unassigned") if key_getter(t) else "Unassigned",
        )

    async def _group_by_tag(self, trades: list[Trade]) -> list[dict[str, Any]]:
        """A trade can carry several tags, so it contributes to each — the counts intentionally
        sum to more than the trade total."""
        if not trades:
            return []
        trade_ids = [trade.id for trade in trades]
        by_id = {trade.id: trade for trade in trades}

        rows = await self.session.execute(
            select(TradeTag.trade_id, Tag.name)
            .join(Tag, Tag.id == TradeTag.tag_id)
            .where(
                TradeTag.organization_id == self.organization_id,
                TradeTag.trade_id.in_(trade_ids),
                Tag.deleted_at.is_(None),
            )
        )
        grouped: dict[str, list[Trade]] = {}
        for trade_id, name in rows.all():
            trade = by_id.get(trade_id)
            if trade is not None:
                grouped.setdefault(name, []).append(trade)

        return [_summarise(label, bucket) for label, bucket in sorted(grouped.items())]

    # ------------------------------------------------------------------
    # Dashboard and comparisons
    # ------------------------------------------------------------------

    async def dashboard(self, filters: TradeFilters, *, days: int = 90) -> dict[str, Any]:
        """The dashboard payload: headline metrics plus the widgets' data, in one round trip."""
        window = TradeFilters(**filters.model_dump())
        if window.date_from is None:
            window.date_from = utcnow() - timedelta(days=days)

        analysis = await self.analyse(window)
        open_positions = await self._open_summary(filters)
        recent = await self._recent_trades(filters)

        return {
            **analysis.to_dict(),
            "open_positions": open_positions,
            "recent_trades": recent,
            "window_days": days,
        }

    async def _open_summary(self, filters: TradeFilters) -> dict[str, Any]:
        stmt = self.trades.trades.select().where(
            Trade.status.in_([TradeStatus.OPEN, TradeStatus.PARTIALLY_CLOSED])
        )
        if filters.account_ids:
            stmt = stmt.where(Trade.account_id.in_(filters.account_ids))
        result = await self.session.execute(stmt)
        open_trades = list(result.scalars().all())
        return {
            "count": len(open_trades),
            "symbols": sorted({trade.symbol for trade in open_trades}),
            "trades": [
                {
                    "id": str(trade.id),
                    "symbol": trade.symbol,
                    "direction": trade.direction.value,
                    "quantity": _s(trade.remaining_quantity),
                    "entry_price": _s(trade.entry_price),
                    "entry_timestamp": trade.entry_timestamp.isoformat(),
                }
                for trade in open_trades[:20]
            ],
        }

    async def _recent_trades(self, filters: TradeFilters, limit: int = 10) -> list[dict[str, Any]]:
        stmt = (
            self.trades._apply_filters(self.trades.trades.select(), filters)
            .where(Trade.status == TradeStatus.CLOSED)
            .order_by(Trade.exit_timestamp.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [
            {
                "id": str(trade.id),
                "symbol": trade.symbol,
                "direction": trade.direction.value,
                "net_pnl": _s(trade.net_pnl),
                "r_multiple": _s(trade.r_multiple),
                "exit_timestamp": (
                    trade.exit_timestamp.isoformat() if trade.exit_timestamp else None
                ),
            }
            for trade in result.scalars().all()
        ]

    async def day_detail(
        self, day: date, filters: TradeFilters, *, timezone: str | None = None
    ) -> dict[str, Any]:
        """Everything behind one calendar cell.

        The selection is made with ``trading_day`` rather than a SQL range on exit_timestamp, for
        the same reason the cell is built that way: a futures session opens at 18:00 the previous
        evening, so a trade closed at 19:00 on Monday belongs to Tuesday. Filtering by calendar
        date would hand back a different set of trades than the cell counted, and the drill-down
        would contradict the number the user clicked on.
        """
        zone = timezone or await self._primary_timezone(filters)
        rows = [
            trade
            for trade in await self._load_closed_trades(filters)
            if trading_day(trade.exit_timestamp or trade.entry_timestamp, trade.asset_type, zone)
            == day
        ]
        rows.sort(key=lambda t: (t.exit_timestamp or t.entry_timestamp))

        net = quantize_money(sum((trade.net_pnl for trade in rows), ZERO))
        gross = quantize_money(sum((trade.gross_pnl for trade in rows), ZERO))
        costs = quantize_money(
            sum(((trade.commission or ZERO) + (trade.fees or ZERO) for trade in rows), ZERO)
        )
        wins = [t for t in rows if t.net_pnl > 0]
        losses = [t for t in rows if t.net_pnl < 0]

        return {
            "date": day.isoformat(),
            "timezone": zone,
            "summary": {
                "net_pnl": _s(net),
                "gross_pnl": _s(gross),
                "costs": _s(costs),
                "trades": len(rows),
                "wins": len(wins),
                "losses": len(losses),
                # None rather than 0 when there is nothing to divide — an undefined rate is not
                # a rate of zero.
                "win_rate": _s(safe_div(Decimal(len(wins)) * HUNDRED, Decimal(len(rows)))),
                "best": _s(max((t.net_pnl for t in rows), default=None)) if rows else None,
                "worst": _s(min((t.net_pnl for t in rows), default=None)) if rows else None,
            },
            "by_symbol": _group_rows(rows, lambda t: t.symbol),
            "trades": [
                {
                    "id": str(trade.id),
                    "symbol": trade.symbol,
                    "asset_type": trade.asset_type.value if trade.asset_type else None,
                    "direction": trade.direction.value,
                    "quantity": _s(trade.closed_quantity),
                    "entry_price": _s(trade.entry_price),
                    "exit_price": _s(trade.exit_price),
                    "gross_pnl": _s(trade.gross_pnl),
                    "commission": _s(trade.commission),
                    "fees": _s(trade.fees),
                    "net_pnl": _s(trade.net_pnl),
                    "r_multiple": _s(trade.r_multiple),
                    "entry_timestamp": trade.entry_timestamp.isoformat(),
                    "exit_timestamp": (
                        trade.exit_timestamp.isoformat() if trade.exit_timestamp else None
                    ),
                }
                for trade in rows
            ],
        }

    async def compare(
        self, left: TradeFilters, right: TradeFilters, *, left_label: str, right_label: str
    ) -> dict[str, Any]:
        """Compare two arbitrary filter sets — period vs period, strategy vs strategy, account vs
        account are all the same operation with different filters."""
        left_result = await self.analyse(left)
        right_result = await self.analyse(right)
        return {
            "left": {"label": left_label, **left_result.to_dict()},
            "right": {"label": right_label, **right_result.to_dict()},
            "deltas": _metric_deltas(left_result.metrics, right_result.metrics),
        }


# ---------------------------------------------------------------------------


def _to_sim_trade(sequence: int, trade: Trade) -> SimTrade:
    """Map a journal trade onto the engine's trade shape so one analyzer serves both."""
    return SimTrade(
        sequence=sequence,
        # Carried so the weekday and monthly breakdowns use this market's trading day.
        asset_type=trade.asset_type,
        direction=trade.direction,
        entry_timestamp=ensure_aware(trade.entry_timestamp),
        exit_timestamp=ensure_aware(trade.exit_timestamp) if trade.exit_timestamp else None,
        entry_price=trade.entry_price,
        exit_price=trade.exit_price,
        quantity=trade.quantity,
        gross_pnl=trade.gross_pnl,
        commission=trade.commission,
        slippage=trade.slippage,
        net_pnl=trade.net_pnl,
        stop_loss=trade.initial_stop_loss or trade.stop_loss,
        take_profit=trade.take_profit,
        risk_amount=trade.risk_amount,
        r_multiple=trade.r_multiple,
        return_percentage=trade.return_percentage,
        holding_seconds=trade.holding_seconds,
        mfe_price=trade.mfe_price,
        mae_price=trade.mae_price,
        mfe_amount=trade.mfe_amount,
        mae_amount=trade.mae_amount,
        exit_reason=None,
    )


def _summarise(label: str, bucket: list[Trade]) -> dict[str, Any]:
    net = quantize_money(sum((t.net_pnl for t in bucket), ZERO))
    wins = [t for t in bucket if t.net_pnl > 0]
    losses = [t for t in bucket if t.net_pnl < 0]
    gross_profit = quantize_money(sum((t.net_pnl for t in wins), ZERO))
    gross_loss = abs(quantize_money(sum((t.net_pnl for t in losses), ZERO)))
    r_values = [t.r_multiple for t in bucket if t.r_multiple is not None]
    return {
        "label": label,
        "trades": len(bucket),
        "net_pnl": _s(net),
        "win_rate": _s(
            quantize_percent(safe_div(Decimal(len(wins)) * HUNDRED, Decimal(len(bucket))))
        ),
        "average_trade": _s(quantize_money(net / Decimal(len(bucket)))),
        "profit_factor": _s(safe_div(gross_profit, gross_loss) if gross_loss > 0 else None),
        "average_r": _s(
            safe_div(sum(r_values, ZERO), Decimal(len(r_values))) if r_values else None
        ),
    }


def _group_rows(trades: list[Trade], key) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    grouped: dict[str, list[Trade]] = {}
    for trade in trades:
        grouped.setdefault(key(trade), []).append(trade)
    rows = [_summarise(label, bucket) for label, bucket in grouped.items()]
    # Most profitable first — the ordering a trader actually reads these tables in.
    rows.sort(key=lambda row: Decimal(row["net_pnl"] or 0), reverse=True)
    return rows


_COMPARABLE = (
    "net_profit",
    "total_return_percent",
    "win_rate",
    "profit_factor",
    "expectancy",
    "average_trade",
    "max_drawdown_percent",
    "total_trades",
    "average_r",
)


def _metric_deltas(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    deltas: dict[str, Any] = {}
    for key in _COMPARABLE:
        lhs, rhs = left.get(key), right.get(key)
        if lhs is None or rhs is None:
            deltas[key] = None
            continue
        try:
            difference = Decimal(str(rhs)) - Decimal(str(lhs))
        except Exception:
            deltas[key] = None
            continue
        deltas[key] = format(difference.normalize(), "f")
    return deltas


__all__ = ["MAX_ANALYTICS_TRADES", "AnalyticsResult", "AnalyticsService"]
