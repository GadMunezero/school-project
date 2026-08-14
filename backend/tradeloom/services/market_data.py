"""Market-data abstraction.

A :class:`MarketDataProvider` is anything that can answer "give me bars for this instrument, this
timeframe, this range". The database-backed provider is the only one shipped; adding a vendor
feed means implementing the protocol and registering it, with no change to the backtesting engine.

**Real-time honesty.** ``MarketDataSource.is_realtime`` is stored per source and surfaced in every
API response. Nothing in this codebase labels data as live unless a provider that actually streams
prices set that flag. The bundled ``seed`` source is explicitly historical and generated.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core.enums import CandleQualityIssue, Timeframe
from tradeloom.core.errors import NotFoundError, ValidationError
from tradeloom.core.timeutil import ensure_aware, floor_to_timeframe, utcnow
from tradeloom.engine.bars import Bar, BarSeries
from tradeloom.models.instrument import Instrument
from tradeloom.models.market_data import MarketData, MarketDataCoverage, MarketDataSource

#: Weekly bars anchor to Monday; daily and intraday bars divide the day evenly.
_MAX_EXPECTED_GAP_FACTOR = 3


@dataclass(slots=True)
class CandleRequest:
    instrument_id: uuid.UUID
    timeframe: Timeframe
    start: datetime | None = None
    end: datetime | None = None
    limit: int | None = None
    source_id: uuid.UUID | None = None


@dataclass(slots=True)
class QualityReport:
    """What a validation pass found. Reported to the user rather than silently repaired."""

    bar_count: int = 0
    first_bar_at: datetime | None = None
    last_bar_at: datetime | None = None
    issues: dict[str, int] = field(default_factory=dict)
    gaps: list[dict[str, Any]] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return not self.issues

    def to_dict(self) -> dict[str, Any]:
        return {
            "bar_count": self.bar_count,
            "first_bar_at": self.first_bar_at.isoformat() if self.first_bar_at else None,
            "last_bar_at": self.last_bar_at.isoformat() if self.last_bar_at else None,
            "issues": self.issues,
            "gaps": self.gaps[:50],
            "gap_count": len(self.gaps),
            "is_clean": self.is_clean,
        }


class MarketDataProvider(Protocol):
    """The interface a data source must satisfy.

    Implementations must return bars sorted ascending by ``opened_at`` with no duplicates; the
    engine relies on that and :class:`~tradeloom.engine.bars.BarSeries` enforces it.
    """

    key: str
    is_realtime: bool

    async def fetch(self, request: CandleRequest) -> list[Bar]: ...


class DatabaseMarketDataProvider:
    """Serves candles already stored in PostgreSQL. The default and only bundled provider."""

    key = "database"
    is_realtime = False

    def __init__(self, session: AsyncSession, source_id: uuid.UUID) -> None:
        self.session = session
        self.source_id = source_id

    async def fetch(self, request: CandleRequest) -> list[Bar]:
        stmt = select(MarketData).where(
            MarketData.source_id == (request.source_id or self.source_id),
            MarketData.instrument_id == request.instrument_id,
            MarketData.timeframe == request.timeframe,
        )
        if request.start is not None:
            stmt = stmt.where(MarketData.opened_at >= ensure_aware(request.start))
        if request.end is not None:
            stmt = stmt.where(MarketData.opened_at <= ensure_aware(request.end))
        stmt = stmt.order_by(MarketData.opened_at.asc())
        if request.limit:
            stmt = stmt.limit(request.limit)

        result = await self.session.execute(stmt)
        return [
            Bar(
                opened_at=row.opened_at,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
            )
            for row in result.scalars().all()
        ]


class MarketDataService:
    """Source registry, candle access, ingestion and validation.

    Market data is **not** tenant-scoped: candles are reference data shared by every workspace.
    Instruments referenced by a request still go through the tenant-scoped instrument repository,
    so a workspace cannot enumerate another workspace's private instruments through this service.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -- sources -------------------------------------------------------

    async def list_sources(self, *, enabled_only: bool = True) -> list[MarketDataSource]:
        stmt = select(MarketDataSource).order_by(MarketDataSource.name.asc())
        if enabled_only:
            stmt = stmt.where(MarketDataSource.is_enabled.is_(True))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_source(self, source_id: uuid.UUID) -> MarketDataSource:
        source = await self.session.get(MarketDataSource, source_id)
        if source is None or not source.is_enabled:
            raise NotFoundError("Market data source not found.")
        return source

    async def source_by_key(self, key: str) -> MarketDataSource | None:
        result = await self.session.execute(
            select(MarketDataSource).where(MarketDataSource.key == key)
        )
        return result.scalar_one_or_none()

    async def default_source(self) -> MarketDataSource:
        from tradeloom.core.config import get_settings

        source = await self.source_by_key(get_settings().market_data_default_source)
        if source is not None:
            return source
        sources = await self.list_sources()
        if not sources:
            raise NotFoundError(
                "No market data source is configured. Load the seed data or register a provider."
            )
        return sources[0]

    async def source_with_data(
        self, instrument_id: uuid.UUID, timeframe: Timeframe
    ) -> MarketDataSource | None:
        """Pick the source that actually holds this series, or ``None`` if nothing does.

        The configured default is only a fallback. Once a user imports their own candles for an
        instrument, those are what every report, backtest and replay must read — resolving to the
        configured default would silently keep serving seed data alongside real data and quietly
        answer questions about the wrong prices.

        Where several sources hold the same series, the most recently updated wins, on the
        assumption that a fresh import supersedes an older load.
        """
        result = await self.session.execute(
            select(MarketDataCoverage, MarketDataSource)
            .join(MarketDataSource, MarketDataSource.id == MarketDataCoverage.source_id)
            .where(
                MarketDataCoverage.instrument_id == instrument_id,
                MarketDataCoverage.timeframe == timeframe,
                MarketDataCoverage.bar_count > 0,
            )
            .order_by(MarketDataCoverage.last_bar_at.desc())
        )
        rows = result.all()
        if rows:
            return rows[0][1]
        # Nothing stored for this series yet. Fall back to the configured default so a caller
        # asking about a seeded instrument still reads from it — but a workspace with no sources
        # at all is not an error here. Returning None lets the caller say "no candles for this
        # symbol", which is the truth, instead of "no market data source is configured", which is
        # an operator's problem leaking into an end user's report screen.
        try:
            return await self.default_source()
        except NotFoundError:
            return None

    def provider_for(self, source: MarketDataSource) -> MarketDataProvider:
        """Resolve a source row to a provider implementation.

        Only ``static`` sources exist today; the branch is here so adding a vendor is a new
        ``elif`` rather than a rewrite of every caller.
        """
        if source.provider_type in ("static", "generated", "database"):
            return DatabaseMarketDataProvider(self.session, source.id)
        raise ValidationError(
            f"No provider is registered for source type '{source.provider_type}'."
        )

    # -- candles -------------------------------------------------------

    async def get_bars(
        self,
        instrument_id: uuid.UUID,
        timeframe: Timeframe,
        *,
        source: MarketDataSource | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int | None = None,
    ) -> tuple[BarSeries, MarketDataSource | None]:
        """Stored candles, and the source they came from.

        A ``None`` source always arrives with an empty series: there was nowhere to read from.
        Callers that render the source must therefore check the series first, which is the same
        check they already need before reporting on it.
        """
        resolved = source or await self.source_with_data(instrument_id, timeframe)
        if resolved is None:
            return BarSeries([]), None
        provider = self.provider_for(resolved)
        bars = await provider.fetch(
            CandleRequest(
                instrument_id=instrument_id,
                timeframe=timeframe,
                start=start,
                end=end,
                limit=limit,
                source_id=resolved.id,
            )
        )
        return BarSeries(bars), resolved

    async def coverage(
        self, instrument_id: uuid.UUID, timeframe: Timeframe, source_id: uuid.UUID
    ) -> MarketDataCoverage | None:
        result = await self.session.execute(
            select(MarketDataCoverage).where(
                MarketDataCoverage.instrument_id == instrument_id,
                MarketDataCoverage.timeframe == timeframe,
                MarketDataCoverage.source_id == source_id,
            )
        )
        return result.scalar_one_or_none()

    async def available_series(self, instrument_id: uuid.UUID) -> list[MarketDataCoverage]:
        result = await self.session.execute(
            select(MarketDataCoverage)
            .where(MarketDataCoverage.instrument_id == instrument_id)
            .order_by(MarketDataCoverage.timeframe.asc())
        )
        return list(result.scalars().all())

    # -- ingestion -----------------------------------------------------

    async def ingest(
        self,
        *,
        source: MarketDataSource,
        instrument: Instrument,
        timeframe: Timeframe,
        bars: Sequence[Bar],
        replace: bool = False,
    ) -> QualityReport:
        """Insert candles, skipping ones already present.

        Duplicate ``(source, instrument, timeframe, opened_at)`` rows are rejected by a unique
        constraint; this method avoids relying on that by checking first, so a partial re-import
        tops up a series instead of failing.
        """
        if not bars:
            return QualityReport()

        ordered = sorted(bars, key=lambda bar: bar.opened_at)
        report = self.validate(ordered, timeframe)

        existing_result = await self.session.execute(
            select(MarketData.opened_at).where(
                MarketData.source_id == source.id,
                MarketData.instrument_id == instrument.id,
                MarketData.timeframe == timeframe,
                MarketData.opened_at >= ordered[0].opened_at,
                MarketData.opened_at <= ordered[-1].opened_at,
            )
        )
        existing = {row[0] for row in existing_result.all()}

        for bar in ordered:
            if bar.opened_at in existing and not replace:
                continue
            self.session.add(
                MarketData(
                    source_id=source.id,
                    instrument_id=instrument.id,
                    timeframe=timeframe,
                    opened_at=bar.opened_at,
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                )
            )
        await self.session.flush()
        await self.refresh_coverage(source.id, instrument.id, timeframe, report)
        return report

    async def refresh_coverage(
        self,
        source_id: uuid.UUID,
        instrument_id: uuid.UUID,
        timeframe: Timeframe,
        report: QualityReport | None = None,
    ) -> MarketDataCoverage:
        aggregate = await self.session.execute(
            select(
                func.count(),
                func.min(MarketData.opened_at),
                func.max(MarketData.opened_at),
            ).where(
                MarketData.source_id == source_id,
                MarketData.instrument_id == instrument_id,
                MarketData.timeframe == timeframe,
            )
        )
        count, first, last = aggregate.one()

        coverage = await self.coverage(instrument_id, timeframe, source_id)
        if coverage is None:
            coverage = MarketDataCoverage(
                source_id=source_id, instrument_id=instrument_id, timeframe=timeframe
            )
            self.session.add(coverage)

        coverage.bar_count = int(count or 0)
        coverage.first_bar_at = first
        coverage.last_bar_at = last
        coverage.validated_at = utcnow()
        if report is not None:
            coverage.quality_report = report.to_dict()
        await self.session.flush()
        return coverage

    # -- validation ----------------------------------------------------

    def validate(self, bars: Sequence[Bar], timeframe: Timeframe) -> QualityReport:
        """Check ordering, duplicates, OHLC coherence and gaps.

        Problems are *reported*, not repaired. Silently patching a missing bar would invent price
        data, and a backtest run over invented data is worse than one that says "there is a gap
        here".
        """
        report = QualityReport(bar_count=len(bars))
        if not bars:
            return report

        report.first_bar_at = bars[0].opened_at
        report.last_bar_at = bars[-1].opened_at

        def flag(issue: CandleQualityIssue) -> None:
            report.issues[issue.value] = report.issues.get(issue.value, 0) + 1

        previous: Bar | None = None
        step = timedelta(seconds=timeframe.seconds)

        for bar in bars:
            if bar.high < bar.low:
                flag(CandleQualityIssue.INVALID_OHLC)
            if min(bar.open, bar.high, bar.low, bar.close) <= 0:
                flag(CandleQualityIssue.NON_POSITIVE_PRICE)
            if bar.volume < 0:
                flag(CandleQualityIssue.NEGATIVE_VOLUME)
            if bar.opened_at != floor_to_timeframe(bar.opened_at, timeframe):
                flag(CandleQualityIssue.OUT_OF_ORDER)

            if previous is not None:
                if bar.opened_at == previous.opened_at:
                    flag(CandleQualityIssue.DUPLICATE_TIMESTAMP)
                elif bar.opened_at < previous.opened_at:
                    flag(CandleQualityIssue.OUT_OF_ORDER)
                else:
                    delta = bar.opened_at - previous.opened_at
                    # Weekends and holidays make gaps normal in most markets, so only a gap
                    # several bars wide is worth reporting.
                    if delta > step * _MAX_EXPECTED_GAP_FACTOR:
                        flag(CandleQualityIssue.MISSING_BAR)
                        report.gaps.append(
                            {
                                "from": previous.opened_at.isoformat(),
                                "to": bar.opened_at.isoformat(),
                                "missing_bars": int(delta / step) - 1,
                            }
                        )
            previous = bar

        return report

    async def validate_series(
        self, instrument_id: uuid.UUID, timeframe: Timeframe, source_id: uuid.UUID
    ) -> QualityReport:
        series, _ = await self.get_bars(
            instrument_id, timeframe, source=await self.get_source(source_id)
        )
        report = self.validate(list(series), timeframe)
        await self.refresh_coverage(source_id, instrument_id, timeframe, report)
        return report

    # -- helpers -------------------------------------------------------

    @staticmethod
    def periods_per_year(timeframe: Timeframe) -> int:
        """Bars per year, used to annualise Sharpe and Sortino.

        Based on ~252 trading days and a 6.5-hour session for intraday timeframes. It is an
        approximation, stated as one, and it is stored with the run so a result can be recomputed
        under a different assumption.
        """
        per_day = {
            Timeframe.M1: 390,
            Timeframe.M5: 78,
            Timeframe.M15: 26,
            Timeframe.M30: 13,
            Timeframe.H1: 7,
            Timeframe.H4: 2,
            Timeframe.D1: 1,
        }
        if timeframe is Timeframe.W1:
            return 52
        return 252 * per_day.get(timeframe, 1)

    @staticmethod
    def to_chart_payload(series: BarSeries) -> list[dict[str, Any]]:
        """Chart-ready candles. Prices are strings so precision survives JSON."""
        return [
            {
                "time": bar.opened_at.isoformat().replace("+00:00", "Z"),
                "open": _s(bar.open),
                "high": _s(bar.high),
                "low": _s(bar.low),
                "close": _s(bar.close),
                "volume": _s(bar.volume),
            }
            for bar in series
        ]


def _s(value: Decimal) -> str:
    return format(value.normalize(), "f")


__all__ = [
    "CandleRequest",
    "DatabaseMarketDataProvider",
    "MarketDataProvider",
    "MarketDataService",
    "QualityReport",
]
