"""Deterministic demo data.

Everything is generated from a fixed seed, so the demo workspace is identical on every machine —
which matters because the end-to-end tests assert against it.

The data is *synthetic and labelled as such*. Candles come from a seeded random walk on a
``MarketDataSource`` whose ``is_realtime`` flag is false and whose metadata records that it is
generated. No real market prices and no real person's trading history appear anywhere.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeloom.core import security
from tradeloom.core.enums import (
    AccountType,
    AssetType,
    CommissionModelType,
    MemberRole,
    MemberStatus,
    OrderSide,
    StrategyKind,
    StrategyStatus,
    SubscriptionPlan,
    SubscriptionStatus,
    Timeframe,
    TradeSource,
    UserRole,
    UserStatus,
)
from tradeloom.core.money import quantize_money, quantize_price
from tradeloom.core.timeutil import UTC, trading_day, utcnow
from tradeloom.engine.bars import Bar
from tradeloom.models.identity import User
from tradeloom.models.imports import ImportTemplate
from tradeloom.models.instrument import Instrument, InstrumentAlias
from tradeloom.models.journal import JournalEntry
from tradeloom.models.market_data import MarketDataSource
from tradeloom.models.organization import Organization, OrganizationMember
from tradeloom.models.platform import Subscription
from tradeloom.models.strategy import Setup, Strategy, Tag
from tradeloom.schemas.account import AccountCreate
from tradeloom.schemas.trade import FillInput
from tradeloom.services.accounts import AccountService
from tradeloom.services.catalog import slugify
from tradeloom.services.market_data import MarketDataService
from tradeloom.services.trades import TradeService

SEED_SOURCE_KEY = "seed"


@dataclass(slots=True)
class InstrumentSpec:
    symbol: str
    name: str
    asset_type: AssetType
    start_price: Decimal
    tick_size: Decimal
    multiplier: Decimal
    lot_size: Decimal
    #: Daily volatility as a fraction of price, used by the random walk.
    volatility: float
    drift: float
    aliases: tuple[str, ...] = ()
    #: The currency a price is quoted in, which is the currency P&L comes out in.
    #:
    #: Every instrument here quotes in USD, and that is a constraint rather than a coincidence:
    #: net P&L is (exit - entry) x quantity x multiplier with no conversion step anywhere, so an
    #: instrument quoting in anything else would produce a number in that currency and store it
    #: against an account denominated in USD. A JPY pair would read 50,000 for a trade worth $332.
    #: Adding one needs a conversion at close time first.
    currency: str = "USD"


INSTRUMENTS: tuple[InstrumentSpec, ...] = (
    InstrumentSpec(
        "NVLX",
        "Novalex Semiconductor",
        AssetType.EQUITY,
        Decimal("118.40"),
        Decimal("0.01"),
        Decimal(1),
        Decimal(1),
        0.021,
        0.00035,
        ("NVLX.US",),
    ),
    InstrumentSpec(
        "ARBOR",
        "Arbor Grid Energy",
        AssetType.EQUITY,
        Decimal("46.15"),
        Decimal("0.01"),
        Decimal(1),
        Decimal(1),
        0.017,
        0.00018,
        ("ARBOR.US",),
    ),
    InstrumentSpec(
        "HELIA",
        "Helia Biosciences",
        AssetType.EQUITY,
        Decimal("23.80"),
        Decimal("0.01"),
        Decimal(1),
        Decimal(1),
        0.028,
        -0.00012,
    ),
    InstrumentSpec(
        "MQ1",
        "Meridian Index Future",
        AssetType.FUTURES,
        Decimal("4820.00"),
        Decimal("0.25"),
        Decimal(50),
        Decimal(1),
        0.011,
        0.00022,
        ("/MQ1", "MQ1Z4"),
    ),
    InstrumentSpec(
        "BTCX",
        "Bitcoin Index Perpetual",
        AssetType.CRYPTO,
        Decimal("38250.00"),
        Decimal("0.50"),
        Decimal(1),
        Decimal("0.0001"),
        0.032,
        0.0004,
        ("BTC-USD",),
    ),
    InstrumentSpec(
        "EURG",
        "Euro / Guilder Cross",
        AssetType.FOREX,
        Decimal("1.0842"),
        Decimal("0.00001"),
        Decimal(100_000),
        Decimal("0.01"),
        0.006,
        -0.00004,
    ),
    # The four majors that quote in USD. That is the whole set of them, and the boundary is
    # deliberate: see InstrumentSpec.currency. USD/JPY, USD/CHF and USD/CAD quote in the other
    # currency and cannot be represented honestly until P&L is converted at close.
    #
    # Contract multiplier is one standard lot (100,000 units of the base currency) and lot_size
    # is a micro lot, so a quantity of 0.01 is 1,000 units — the granularity a retail account
    # actually trades. A one-pip move on one standard lot is therefore $10, as it should be.
    InstrumentSpec(
        "EURUSD",
        "Euro / US Dollar",
        AssetType.FOREX,
        Decimal("1.08500"),
        Decimal("0.00001"),
        Decimal(100_000),
        Decimal("0.01"),
        0.005,
        0.00002,
        ("EUR/USD", "EURUSD.FX"),
    ),
    InstrumentSpec(
        "GBPUSD",
        "British Pound / US Dollar",
        AssetType.FOREX,
        Decimal("1.26500"),
        Decimal("0.00001"),
        Decimal(100_000),
        Decimal("0.01"),
        0.006,
        -0.00003,
        ("GBP/USD", "CABLE"),
    ),
    InstrumentSpec(
        "AUDUSD",
        "Australian Dollar / US Dollar",
        AssetType.FOREX,
        Decimal("0.65800"),
        Decimal("0.00001"),
        Decimal(100_000),
        Decimal("0.01"),
        0.007,
        -0.00005,
        ("AUD/USD",),
    ),
    InstrumentSpec(
        "NZDUSD",
        "New Zealand Dollar / US Dollar",
        AssetType.FOREX,
        Decimal("0.61000"),
        Decimal("0.00001"),
        Decimal(100_000),
        Decimal("0.01"),
        0.007,
        -0.00004,
        ("NZD/USD", "KIWI"),
    ),
)

SETUPS = (
    ("Opening drive continuation", "Trend continuation off the first 30 minutes."),
    ("Failed breakout reversal", "Fade a breakout that cannot hold the level."),
    ("Value area rotation", "Mean reversion between the day's value boundaries."),
    ("Gap fill", "Fade an unsupported overnight gap back toward the prior close."),
    ("Trend pullback", "Enter a pullback to a rising average inside an established trend."),
)

TAGS = (
    ("A+ setup", "quality"),
    ("B setup", "quality"),
    ("Chased entry", "mistake"),
    ("Moved stop", "mistake"),
    ("Sized too large", "mistake"),
    ("Followed plan", "process"),
    ("Patient entry", "process"),
    ("News driven", "market"),
    ("Low liquidity", "market"),
    ("Revenge trade", "emotion"),
    ("Hesitated", "emotion"),
)

STRATEGIES = (
    (
        "Momentum continuation",
        StrategyKind.BUILTIN,
        "ema_cross",
        "Buys strength when the fast EMA crosses above the slow EMA, with an ATR stop.",
    ),
    (
        "Range fade",
        StrategyKind.BUILTIN,
        "rsi_reversion",
        "Fades exhausted moves back toward the midline.",
    ),
    (
        "Breakout expansion",
        StrategyKind.BUILTIN,
        "breakout",
        "Takes range breakouts and trails with the opposite extreme.",
    ),
    (
        "Discretionary swing",
        StrategyKind.JOURNAL_ONLY,
        None,
        "Manual multi-day swing trades. Journalled, not automated.",
    ),
)

BROKER_TEMPLATES = (
    {
        "key": "generic_executions",
        "name": "Generic execution export",
        "broker": None,
        "description": "Column names most brokers use for a filled-orders export.",
        "column_mapping": {
            "timestamp": "Time",
            "symbol": "Symbol",
            "side": "Side",
            "quantity": "Quantity",
            "price": "Price",
            "commission": "Commission",
            "external_id": "Order ID",
        },
        "options": {"timezone": "UTC"},
        "detection_headers": {"required": ["symbol", "side", "quantity", "price"]},
    },
    {
        "key": "us_equities_desktop",
        "name": "US equities desktop platform",
        "broker": "Generic US equities",
        "description": "Date/Time, Action and Qty columns, US Eastern timestamps.",
        "column_mapping": {
            "timestamp": "Date/Time",
            "symbol": "Symbol",
            "side": "Action",
            "quantity": "Qty",
            "price": "Price",
            "commission": "Commission",
            "fees": "Fees",
            "external_id": "Exec ID",
        },
        "options": {"timezone": "America/New_York"},
        "detection_headers": {"required": ["date/time", "action", "qty", "price"]},
    },
    {
        "key": "crypto_exchange",
        "name": "Crypto exchange fills",
        "broker": "Generic crypto exchange",
        "description": "Epoch timestamps, pair symbols, maker/taker fees.",
        "column_mapping": {
            "timestamp": "time",
            "symbol": "pair",
            "side": "side",
            "quantity": "amount",
            "price": "rate",
            "fees": "fee",
            "external_id": "id",
        },
        "options": {"timezone": "UTC"},
        "detection_headers": {"required": ["pair", "amount", "rate"]},
    },
)


#: The New York clock, which is where the futures and FX trading day is defined.
_MARKET_ZONE_NAME = "America/New_York"
_MARKET_ZONE = ZoneInfo(_MARKET_ZONE_NAME)


def _is_open(asset_type: AssetType, moment: datetime) -> bool:
    """Whether an intraday bar should exist at this instant.

    Generated data that ignores real trading hours quietly makes the product untestable: futures
    given a 09:30-16:00 shape never cross their 18:00 open, so the session-boundary handling looks
    correct in the demo workspace whether or not it is. These hours are approximations — no
    holiday calendar, no half days — but they put bars on the right side of the boundaries that
    the reports depend on.
    """
    if asset_type is AssetType.CRYPTO:
        return True

    local = moment.astimezone(_MARKET_ZONE)
    weekday = local.weekday()  # Monday is 0, Sunday is 6.

    if asset_type in (AssetType.FUTURES, AssetType.FOREX, AssetType.CFD):
        opens_at = 18 if asset_type is AssetType.FUTURES else 17
        if weekday == 4 and local.hour >= 17:  # Friday close.
            return False
        if weekday == 5:  # All day Saturday.
            return False
        if weekday == 6:  # Sunday, until the week reopens in the evening.
            return local.hour >= opens_at
        # Futures take an hour's maintenance break between the close and the next open.
        return not (asset_type is AssetType.FUTURES and local.hour == 17)

    # Equities, ETFs, indices and options: the US cash session on weekdays.
    return weekday < 5 and 9 <= local.hour <= 15


class DemoSeeder:
    """Builds the demo workspace. Idempotent: re-running finds the existing user and stops."""

    def __init__(self, session: AsyncSession, *, seed: int = 20_240_517) -> None:
        self.session = session
        self.random = random.Random(seed)
        self.seed = seed
        self.summary: dict[str, Any] = {}

    async def run(
        self, *, email: str, password: str, trade_count: int = 1200, candle_days: int = 540
    ) -> dict[str, Any]:
        existing = await self.session.execute(select(User).where(User.email == email.lower()))
        if existing.scalar_one_or_none() is not None:
            return {"skipped": True, "reason": "demo user already exists", "email": email}

        source = await self._market_data_source()
        instruments = await self._instruments()
        user, organization = await self._user_and_workspace(email, password)
        await self._import_templates(organization)

        tags = await self._tags(organization)
        setups = await self._setups(organization)
        strategies = await self._strategies(organization, user)

        await self._candles(source, instruments, days=candle_days)
        accounts = await self._accounts(organization, user)
        trades = await self._trades(
            organization, user, accounts, instruments, strategies, setups, tags, trade_count
        )
        await self._journal(organization, user, trades)
        backtests = await self._backtests(organization, user, instruments, strategies)

        self.summary = {
            "email": email,
            "password": password,
            "organization": organization.name,
            "accounts": len(accounts),
            "instruments": len(instruments),
            "strategies": len(strategies),
            "setups": len(setups),
            "tags": len(tags),
            "trades": trades,
            "backtests": backtests,
            "candle_days": candle_days,
        }
        return self.summary

    # ------------------------------------------------------------------

    async def _market_data_source(self) -> MarketDataSource:
        result = await self.session.execute(
            select(MarketDataSource).where(MarketDataSource.key == SEED_SOURCE_KEY)
        )
        source = result.scalar_one_or_none()
        if source is not None:
            return source

        source = MarketDataSource(
            key=SEED_SOURCE_KEY,
            name="Tradeloom sample data",
            description=(
                "Synthetic OHLCV generated by a seeded random walk. These are not real market "
                "prices and must not be used for research or trading decisions."
            ),
            provider_type="generated",
            # Explicitly not real time. The UI reads this flag rather than assuming.
            is_realtime=False,
            metadata_json={
                "generated": True,
                "generator": "seeded_random_walk",
                "seed": self.seed,
                "disclaimer": "Synthetic data for demonstration only.",
            },
        )
        self.session.add(source)
        await self.session.flush()
        return source

    async def _instruments(self) -> list[Instrument]:
        instruments: list[Instrument] = []
        for spec in INSTRUMENTS:
            result = await self.session.execute(
                select(Instrument).where(
                    Instrument.symbol == spec.symbol, Instrument.organization_id.is_(None)
                )
            )
            instrument = result.scalar_one_or_none()
            if instrument is None:
                instrument = Instrument(
                    organization_id=None,  # shared catalogue
                    symbol=spec.symbol,
                    name=spec.name,
                    asset_type=spec.asset_type,
                    exchange="TLX",
                    currency=spec.currency,
                    tick_size=spec.tick_size,
                    contract_multiplier=spec.multiplier,
                    lot_size=spec.lot_size,
                    price_precision=len(str(spec.tick_size).split(".")[-1]),
                    metadata_json={"synthetic": True},
                )
                self.session.add(instrument)
                await self.session.flush()
                for alias in spec.aliases:
                    self.session.add(
                        InstrumentAlias(
                            organization_id=None,
                            instrument_id=instrument.id,
                            alias=alias,
                            alias_normalized="".join(ch for ch in alias.upper() if ch.isalnum()),
                            source="*",
                        )
                    )
            instruments.append(instrument)
        await self.session.flush()
        return instruments

    async def _user_and_workspace(self, email: str, password: str) -> tuple[User, Organization]:
        user = User(
            email=email.lower(),
            password_hash=security.hash_password(password),
            full_name="Dana Reyes",
            display_name="Dana",
            role=UserRole.USER,
            status=UserStatus.ACTIVE,
            email_verified_at=utcnow(),
            timezone="America/New_York",
            password_changed_at=utcnow(),
        )
        self.session.add(user)
        await self.session.flush()

        organization = Organization(
            name="Reyes Trading",
            slug=slugify("Reyes Trading"),
            owner_user_id=user.id,
            is_personal=True,
            base_currency="USD",
            timezone="America/New_York",
        )
        self.session.add(organization)
        await self.session.flush()

        self.session.add(
            OrganizationMember(
                organization_id=organization.id,
                user_id=user.id,
                role=MemberRole.OWNER,
                status=MemberStatus.ACTIVE,
                joined_at=utcnow(),
            )
        )
        # The demo runs on Pro so replay, comparison and backtesting are all explorable.
        self.session.add(
            Subscription(
                organization_id=organization.id,
                plan=SubscriptionPlan.PRO,
                status=SubscriptionStatus.ACTIVE,
                entitlement_overrides={},
            )
        )
        await self.session.flush()
        return user, organization

    async def _import_templates(self, organization: Organization) -> None:
        for spec in BROKER_TEMPLATES:
            existing = await self.session.execute(
                select(ImportTemplate).where(
                    ImportTemplate.key == spec["key"], ImportTemplate.organization_id.is_(None)
                )
            )
            if existing.scalar_one_or_none() is not None:
                continue
            self.session.add(ImportTemplate(organization_id=None, is_system=True, **spec))
        await self.session.flush()

    async def _tags(self, organization: Organization) -> list[Tag]:
        tags = [
            Tag(
                organization_id=organization.id,
                name=name,
                slug=slugify(name),
                category=category,
            )
            for name, category in TAGS
        ]
        self.session.add_all(tags)
        await self.session.flush()
        return tags

    async def _setups(self, organization: Organization) -> list[Setup]:
        setups = [
            Setup(
                organization_id=organization.id,
                name=name,
                description=description,
                checklist={"items": ["Level identified", "Risk defined", "Size calculated"]},
            )
            for name, description in SETUPS
        ]
        self.session.add_all(setups)
        await self.session.flush()
        return setups

    async def _strategies(self, organization: Organization, user: User) -> list[Strategy]:
        from tradeloom.schemas.catalog import StrategyCreate
        from tradeloom.services.catalog import StrategyService

        service = StrategyService(self.session, organization.id, actor_user_id=user.id)
        created: list[Strategy] = []
        for name, kind, engine_key, description in STRATEGIES:
            strategy = await service.create(
                StrategyCreate(
                    name=name,
                    description=description,
                    kind=kind,
                    engine_key=engine_key,
                    parameters={},
                )
            )
            strategy.status = StrategyStatus.ACTIVE
            created.append(strategy)
        await self.session.flush()
        return created

    async def _candles(
        self, source: MarketDataSource, instruments: list[Instrument], *, days: int
    ) -> None:
        """Generate daily and hourly candles with a seeded geometric random walk."""
        service = MarketDataService(self.session)
        end = datetime.combine(utcnow().date(), time(0, 0), tzinfo=UTC)
        start = end - timedelta(days=days)

        for spec, instrument in zip(INSTRUMENTS, instruments, strict=True):
            daily = self._walk(spec, start, end, Timeframe.D1)
            await service.ingest(
                source=source, instrument=instrument, timeframe=Timeframe.D1, bars=daily
            )
            # Hourly data for the most recent stretch, which is what replay and intraday
            # backtests use. Generating it for the full range would be gigabytes for no benefit.
            hourly_start = end - timedelta(days=min(days, 120))
            hourly = self._walk(spec, hourly_start, end, Timeframe.H1)
            await service.ingest(
                source=source, instrument=instrument, timeframe=Timeframe.H1, bars=hourly
            )
        source.last_synced_at = utcnow()
        await self.session.flush()

    def _walk(
        self, spec: InstrumentSpec, start: datetime, end: datetime, timeframe: Timeframe
    ) -> list[Bar]:
        rng = random.Random(f"{self.seed}:{spec.symbol}:{timeframe.value}")
        step = timedelta(seconds=timeframe.seconds)
        # Volatility scales with the square root of time, so an hourly bar is not as wide as a
        # daily one.
        scale = math.sqrt(timeframe.seconds / 86_400)
        volatility = spec.volatility * scale
        drift = spec.drift * (timeframe.seconds / 86_400)

        price = float(spec.start_price)
        bars: list[Bar] = []
        moment = start
        while moment < end:
            # Daily bars carry a date, so only the weekend matters. Intraday bars have to respect
            # the market's actual hours, which differ by asset type.
            if timeframe is Timeframe.D1:
                if spec.asset_type is not AssetType.CRYPTO and moment.weekday() >= 5:
                    moment += step
                    continue
            elif not _is_open(spec.asset_type, moment):
                moment += step
                continue

            shock = rng.gauss(drift, volatility)
            open_price = price

            # Markets that close overnight reopen away from the last print. Without this the walk
            # is continuous, every session opens exactly where the previous one closed, and the
            # gap-fill report correctly reports that no gap ever occurred — true, but it leaves
            # the demo workspace unable to demonstrate the feature at all.
            #
            # The gap goes at the *session* boundary, not at midnight. A futures session runs
            # from 18:00 through the next afternoon, so a jump at midnight would land mid-session
            # and manufacture exactly the phantom gap the reports were fixed to stop reporting.
            if (
                bars
                and spec.asset_type is not AssetType.CRYPTO
                and trading_day(moment, spec.asset_type, _MARKET_ZONE_NAME)
                != trading_day(bars[-1].opened_at, spec.asset_type, _MARKET_ZONE_NAME)
                and rng.random() < 0.35
            ):
                open_price = max(0.01, price * (1 + rng.gauss(0, volatility * 2.5)))

            close_price = max(0.01, open_price * (1 + shock))
            wick = abs(rng.gauss(0, volatility * 0.6)) * price
            high = max(open_price, close_price) + wick
            low = max(0.005, min(open_price, close_price) - wick)
            volume = rng.randint(50_000, 900_000)

            bars.append(
                Bar(
                    opened_at=moment,
                    open=quantize_price(Decimal(f"{open_price:.6f}")),
                    high=quantize_price(Decimal(f"{high:.6f}")),
                    low=quantize_price(Decimal(f"{low:.6f}")),
                    close=quantize_price(Decimal(f"{close_price:.6f}")),
                    volume=Decimal(volume),
                )
            )
            price = close_price
            moment += step
        return bars

    async def _accounts(self, organization: Organization, user: User) -> list[Any]:
        service = AccountService(self.session, organization.id, actor_user_id=user.id)
        specs = [
            AccountCreate(
                name="Primary equities",
                broker="Northbridge Securities",
                account_type=AccountType.LIVE,
                currency="USD",
                initial_balance=Decimal("75000"),
                leverage=Decimal(2),
                timezone="America/New_York",
                commission_model=CommissionModelType.PER_SHARE,
                commission_config={"rate": "0.005", "minimum": "1.00"},
                default_risk_percent=Decimal("1.0"),
                is_default=True,
            ),
            AccountCreate(
                name="Futures evaluation",
                broker="Cascade Futures",
                account_type=AccountType.PROP_EVALUATION,
                currency="USD",
                initial_balance=Decimal("50000"),
                leverage=Decimal(1),
                timezone="America/Chicago",
                commission_model=CommissionModelType.PER_CONTRACT,
                commission_config={"rate": "2.10"},
                default_risk_percent=Decimal("0.75"),
            ),
            AccountCreate(
                name="Crypto swing",
                broker="Latitude Digital",
                account_type=AccountType.LIVE,
                currency="USD",
                initial_balance=Decimal("20000"),
                leverage=Decimal(3),
                timezone="UTC",
                commission_model=CommissionModelType.PERCENT_OF_NOTIONAL,
                commission_config={"rate": "0.075"},
                default_risk_percent=Decimal("1.5"),
            ),
            # Without this the FX instruments are catalogued and never traded, which is how the
            # 17:00 New York session boundary went unexercised by the demo data for so long.
            AccountCreate(
                name="FX spot",
                broker="Meridian FX",
                account_type=AccountType.LIVE,
                currency="USD",
                initial_balance=Decimal("30000"),
                leverage=Decimal(30),
                timezone="America/New_York",
                # FX commission is quoted per lot, which is this instrument's contract unit.
                commission_model=CommissionModelType.PER_CONTRACT,
                commission_config={"rate": "3.50"},
                default_risk_percent=Decimal("1.0"),
            ),
        ]
        accounts = [await service.create(spec) for spec in specs]

        # A funding history so the equity curve does not start from a single lump sum.
        for account in accounts:
            await service.add_cash_transaction(
                account.id,
                kind="deposit",
                amount=Decimal("5000"),
                occurred_at=utcnow() - timedelta(days=200),
                description="Additional funding",
            )
        return accounts

    async def _trades(
        self,
        organization: Organization,
        user: User,
        accounts: list[Any],
        instruments: list[Instrument],
        strategies: list[Strategy],
        setups: list[Setup],
        tags: list[Tag],
        count: int,
    ) -> int:
        """Generate realistic trades by walking fills through the real TradeService.

        Using the production write path means the demo data exercises exactly the same P&L,
        R-multiple and session logic a user's own trades would.
        """
        service = TradeService(self.session, organization.id, actor_user_id=user.id)
        rng = self.random
        created = 0
        now = utcnow()

        equity_instruments = [i for i in instruments if i.asset_type is AssetType.EQUITY]
        futures = [i for i in instruments if i.asset_type is AssetType.FUTURES]
        crypto = [i for i in instruments if i.asset_type is AssetType.CRYPTO]
        forex = [i for i in instruments if i.asset_type is AssetType.FOREX]
        pools = {
            accounts[0].id: equity_instruments,
            accounts[1].id: futures,
            accounts[2].id: crypto,
            accounts[3].id: forex,
        }

        # Entry times are drawn up front and sorted, so the demo history is recorded the way a
        # real one accumulates: forward in time. Ingestion continues whatever position is open for
        # a symbol, so feeding it back-dated fills would graft a trade onto a position that had
        # not opened yet — which the service now refuses outright.
        # The weekend shift happens *before* the sort, not after: nudging a Saturday back to the
        # preceding Thursday would otherwise reorder the sequence and reintroduce the back-dating.
        # It is applied to every instrument, including crypto, for the same reason.
        def _draw() -> datetime:
            moment = (now - timedelta(days=rng.randint(1, 400))).replace(
                hour=rng.choice([14, 15, 16, 17, 18, 19, 20]),
                minute=rng.choice([0, 5, 15, 30, 45]),
                second=0,
                microsecond=0,
            )
            return moment - timedelta(days=2) if moment.weekday() >= 5 else moment

        entry_times = sorted(_draw() for _ in range(count))

        # Which instrument each trade uses, decided up front so the loop can know which trade is
        # the *last* one for a given account and symbol.
        assignments: list[tuple[Any, Instrument]] = []
        for index in range(count):
            account = accounts[index % len(accounts)]
            pool = pools[account.id] or instruments
            assignments.append((account, rng.choice(pool)))

        final_index = {
            (account.id, instrument.symbol): index
            for index, (account, instrument) in enumerate(assignments)
        }

        #: The last fill recorded for each account and symbol. Sorted *entries* are not enough to
        #: keep a position's history moving forward: a trade holds for up to two days, so its exit
        #: can land after the next trade's entry. When that next trade closes an open position,
        #: the exit it carries becomes the new position's open — and the trade after that arrives
        #: back-dated against it. Ingestion refuses those outright, as it should.
        last_fill_at: dict[tuple[Any, str], datetime] = {}
        left_open: set[tuple[Any, str]] = set()

        for index in range(count):
            account, instrument = assignments[index]
            key = (account.id, instrument.symbol)

            entry_at = entry_times[index]
            # Nudge past whatever this symbol last recorded, keeping the sequence forward-only.
            earliest = last_fill_at.get(key)
            if earliest is not None and entry_at <= earliest:
                entry_at = earliest + timedelta(minutes=5)

            base = float(next(s.start_price for s in INSTRUMENTS if s.symbol == instrument.symbol))
            spec = next(s for s in INSTRUMENTS if s.symbol == instrument.symbol)

            # How far entries wander from the starting price, scaled by the instrument's own
            # volatility rather than one band for everything. A flat +-30% is reasonable for an
            # equity or crypto over the seeded year and nonsense for FX: it put GBP/USD at 1.70
            # against a 1.2650 start, a 34% move no major has ever made in a year.
            spread = min(0.35, spec.volatility * 20)
            entry_price = Decimal(
                f"{base * rng.uniform(1 - spread, 1 + spread):.{instrument.price_precision}f}"
            )

            is_long = rng.random() < 0.62
            side = OrderSide.BUY if is_long else OrderSide.SELL
            # Stops scale with volatility too — 0.8-3.5% of price is 80-350 pips on EUR/USD, which
            # is a swing stop on an intraday trade.
            stop_band = min(0.035, max(0.002, spec.volatility * 1.4))
            stop_distance = entry_price * Decimal(f"{rng.uniform(stop_band * 0.4, stop_band):.5f}")
            stop = entry_price - stop_distance if is_long else entry_price + stop_distance

            risk_fraction = Decimal(str(rng.uniform(0.004, 0.012)))
            risk_cash = quantize_money(account.initial_balance * risk_fraction)
            per_unit = stop_distance * instrument.contract_multiplier
            quantity = (risk_cash / per_unit) if per_unit > 0 else Decimal(1)
            if instrument.lot_size >= 1:
                quantity = Decimal(max(1, int(quantity)))
            else:
                quantity = Decimal(f"{max(float(instrument.lot_size), float(quantity)):.4f}")

            # Outcome distribution: a positive-expectancy discretionary trader.
            roll = rng.random()
            if roll < 0.44:
                r_result = Decimal(str(round(rng.uniform(0.8, 3.4), 2)))
            elif roll < 0.86:
                r_result = Decimal(str(round(-rng.uniform(0.6, 1.05), 2)))
            else:
                r_result = Decimal(str(round(rng.uniform(-0.25, 0.25), 2)))

            move = stop_distance * r_result
            exit_price = entry_price + move if is_long else entry_price - move
            exit_price = max(Decimal("0.0001"), quantize_price(exit_price))

            holding_minutes = rng.choice([12, 25, 47, 90, 180, 400, 1400, 2900])
            exit_at = entry_at + timedelta(minutes=holding_minutes)

            fills = [
                FillInput(
                    side=side,
                    quantity=quantity,
                    price=quantize_price(entry_price),
                    timestamp=entry_at,
                    commission=self._commission(spec, quantity, entry_price),
                )
            ]
            # A third of trades scale out in two pieces, so partial exits are represented.
            if rng.random() < 0.33 and quantity > 1:
                half = quantity / 2
                if instrument.lot_size >= 1:
                    half = Decimal(int(half)) or Decimal(1)
                fills.append(
                    FillInput(
                        side=side.opposite,
                        quantity=half,
                        price=quantize_price((entry_price + exit_price) / 2),
                        timestamp=entry_at + timedelta(minutes=holding_minutes // 2),
                        commission=self._commission(spec, half, exit_price),
                    )
                )
                fills.append(
                    FillInput(
                        side=side.opposite,
                        quantity=quantity - half,
                        price=exit_price,
                        timestamp=exit_at,
                        commission=self._commission(spec, quantity - half, exit_price),
                    )
                )
            else:
                fills.append(
                    FillInput(
                        side=side.opposite,
                        quantity=quantity,
                        price=exit_price,
                        timestamp=exit_at,
                        commission=self._commission(spec, quantity, exit_price),
                    )
                )

            # A few positions stay open so the open-positions view is populated — but only on the
            # very last trade for that account and symbol. Leaving one open earlier would make
            # every later trade in that symbol continue it, merging separate round trips into one
            # long, incoherent record.
            if index == final_index[key] and len(left_open) < 3:
                fills = fills[:1]
                left_open.add(key)

            last_fill_at[key] = max(fill.timestamp for fill in fills)

            trades = await service.ingest_fills(
                account=account,
                symbol=instrument.symbol,
                asset_type=instrument.asset_type,
                fills=fills,
                source=TradeSource.MANUAL,
                instrument=instrument,
                stop_loss=quantize_price(stop),
                strategy_id=rng.choice(strategies).id,
                setup_id=rng.choice(setups).id,
                notes=rng.choice(
                    [
                        None,
                        "Waited for the retest before entering.",
                        "Entry was early; the level had not confirmed.",
                        "Managed to the plan and took the full target.",
                        "Cut this one before the stop when momentum stalled.",
                    ]
                ),
                rating=rng.choice([None, 3, 4, 4, 5, 2]),
            )
            for trade in trades:
                chosen = rng.sample(tags, k=rng.randint(0, 3))
                if chosen:
                    await service.set_tags(trade, [tag.id for tag in chosen])
            created += len(trades)

            if index % 200 == 0:
                await self.session.flush()

        for account in accounts:
            await AccountService(self.session, organization.id, actor_user_id=user.id).recalculate(
                account.id
            )
        return created

    def _commission(self, spec: InstrumentSpec, quantity: Decimal, price: Decimal) -> Decimal:
        if spec.asset_type is AssetType.FUTURES:
            return quantize_money(quantity * Decimal("2.10"))
        if spec.asset_type is AssetType.CRYPTO:
            return quantize_money(quantity * price * Decimal("0.00075"))
        return quantize_money(max(Decimal("1.00"), quantity * Decimal("0.005")))

    async def _backtests(
        self,
        organization: Organization,
        user: User,
        instruments: list[Instrument],
        strategies: list[Strategy],
    ) -> int:
        """One backtest, actually run.

        A demo workspace whose backtester is empty cannot show what the feature produces, and the
        results page has nothing to render for anyone looking at it — a reviewer, a new user, or
        the end-to-end suite. The run goes through ``BacktestService.execute``, the same call the
        Celery worker makes, so these are real engine results rather than a fabricated row that
        merely looks like one.
        """
        from tradeloom.schemas.backtest import BacktestCreate
        from tradeloom.services.backtests import BacktestService

        executable = [s for s in strategies if s.kind is StrategyKind.BUILTIN]
        equity = next((i for i in instruments if i.asset_type is AssetType.EQUITY), None)
        if not executable or equity is None:
            return 0

        service = BacktestService(self.session, organization.id, actor_user_id=user.id)
        source = await MarketDataService(self.session).source_by_key(SEED_SOURCE_KEY)
        if source is None:
            return 0

        # Hourly rather than daily: the same span holds several times as many bars, so the run
        # produces a set of trades worth looking at instead of a single lonely round trip.
        coverage = await MarketDataService(self.session).coverage(
            equity.id, Timeframe.H1, source.id
        )
        if coverage is None or coverage.first_bar_at is None or coverage.last_bar_at is None:
            return 0

        backtest = await service.create(
            BacktestCreate(
                name=f"{executable[0].name} on {equity.symbol}",
                description="Seeded run so the results page has real engine output to show.",
                strategy_id=executable[0].id,
                instrument_id=equity.id,
                market_data_source_id=source.id,
                timeframe=Timeframe.H1,
                start_date=coverage.first_bar_at.date(),
                end_date=coverage.last_bar_at.date(),
                initial_capital=Decimal("100000"),
                risk_percent=Decimal(1),
                commission_config={"model": "per_share", "rate": "0.005", "minimum": "1"},
                slippage_config={"model": "fixed_ticks", "amount": "1"},
            )
        )
        await self.session.flush()

        run, _job = await service.submit(backtest.id)
        await self.session.flush()
        await service.execute(run.id)
        await self.session.flush()
        return 1

    async def _journal(self, organization: Organization, user: User, trade_count: int) -> None:
        entries = [
            (
                "Weekly review — process over outcome",
                "Win rate looked flat but average R improved. The difference was patience on "
                "entries; the trades I skipped were the ones that used to cost me.",
                "weekly",
            ),
            (
                "Position sizing audit",
                "Two outsized losses this month came from sizing off a stop I then widened. "
                "Rule going forward: the stop set at entry is the stop.",
                "note",
            ),
            (
                "Session focus",
                "Performance by hour shows a clear edge in the first two hours and a drag after "
                "lunch. Cutting the afternoon session for a month as an experiment.",
                "daily",
            ),
        ]
        for offset, (title, body, kind) in enumerate(entries):
            self.session.add(
                JournalEntry(
                    organization_id=organization.id,
                    author_user_id=user.id,
                    entry_date=(utcnow() - timedelta(days=offset * 7)).date(),
                    title=title,
                    body=body,
                    entry_type=kind,
                    discipline_rating=4,
                    lessons={"items": ["Stick to the plan", "Size from the stop"]},
                )
            )
        await self.session.flush()


__all__ = ["INSTRUMENTS", "SEED_SOURCE_KEY", "DemoSeeder", "InstrumentSpec"]
