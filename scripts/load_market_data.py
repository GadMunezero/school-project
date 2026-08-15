#!/usr/bin/env python
"""Load real historical candles into a workspace from a CSV URL or file.

The platform never needs a live feed: reports, backtesting and replay are all historical. This
script is the fastest way to replace generated candles with real ones so those figures start
describing a market that existed.

    scripts/load_market_data.py --url https://…/vix-daily.csv --symbol VIX \\
        --name "CBOE Volatility Index" --asset-type index --since 2021-01-01

It reuses the same parser the upload endpoint uses, so a file that loads here loads there, and a
row rejected here is rejected there for the same stated reason.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import urllib.request
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent.parent / "backend")]

from sqlalchemy import select  # noqa: E402

from tradeloom.core.enums import AssetType, Timeframe  # noqa: E402
from tradeloom.db.session import dispose_engine, session_scope  # noqa: E402
from tradeloom.models.instrument import Instrument  # noqa: E402
from tradeloom.models.market_data import MarketDataSource  # noqa: E402
from tradeloom.models.organization import Organization  # noqa: E402
from tradeloom.services import market_import  # noqa: E402
from tradeloom.services.market_data import MarketDataService  # noqa: E402

SOURCE_KEY = "imported"


def fetch(url: str) -> bytes:
    if url.startswith(("http://", "https://")):
        with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310
            return response.read()
    return Path(url).read_bytes()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="CSV URL or local path")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--name", default=None)
    parser.add_argument("--asset-type", default="index", choices=[a.value for a in AssetType])
    parser.add_argument("--timeframe", default="1d", choices=[t.value for t in Timeframe])
    parser.add_argument("--timezone", default="UTC", help="Timezone of naive timestamps")
    parser.add_argument("--since", default=None, help="Drop bars before this date (YYYY-MM-DD)")
    parser.add_argument("--tick-size", default="0.01")
    parser.add_argument("--multiplier", default="1")
    args = parser.parse_args()

    print(f"fetching {args.url}")
    raw = fetch(args.url)

    inspection = market_import.inspect(raw)
    mapping = inspection["suggested_mapping"]
    missing = [f for f in market_import.REQUIRED_FIELDS if f not in mapping]
    if missing:
        print(f"  headers: {inspection['headers']}")
        print(f"error: could not map {', '.join(missing)} from this file's headers")
        return 1

    parsed = market_import.parse_candles(raw, mapping=mapping, timezone=args.timezone)
    bars = parsed.bars
    if args.since:
        cutoff = datetime.fromisoformat(args.since).replace(tzinfo=UTC)
        bars = [bar for bar in bars if bar.opened_at >= cutoff]

    print(f"  parsed {parsed.accepted} candles, rejected {len(parsed.rejected)}")
    if not bars:
        print("error: nothing to load after filtering")
        return 1
    print(f"  loading {len(bars)} from {bars[0].opened_at.date()} to {bars[-1].opened_at.date()}")

    async with session_scope() as db:
        organization = (await db.execute(select(Organization).limit(1))).scalars().first()
        if organization is None:
            print("error: no workspace. Run `python -m tradeloom.cli seed --demo` first.")
            return 1

        instrument = (
            (
                await db.execute(
                    select(Instrument).where(
                        Instrument.organization_id == organization.id,
                        Instrument.symbol == args.symbol,
                    )
                )
            )
            .scalars()
            .first()
        )
        if instrument is None:
            instrument = Instrument(
                organization_id=organization.id,
                symbol=args.symbol,
                name=args.name or args.symbol,
                asset_type=AssetType(args.asset_type),
                currency="USD",
                tick_size=Decimal(args.tick_size),
                contract_multiplier=Decimal(args.multiplier),
                lot_size=Decimal(1),
            )
            db.add(instrument)
            await db.flush()
            print(f"  created instrument {instrument.symbol}")

        service = MarketDataService(db)
        source = await service.source_by_key(SOURCE_KEY)
        if source is None:
            source = MarketDataSource(
                key=SOURCE_KEY,
                name="Imported candles",
                description="Real OHLCV loaded from a CSV export or public dataset.",
                provider_type="static",
                # Historical history is never a live feed, and must never be presented as one.
                is_realtime=False,
            )
            db.add(source)
            await db.flush()

        result = await service.ingest(
            source=source,
            instrument=instrument,
            timeframe=Timeframe(args.timeframe),
            bars=bars,
        )
        await db.commit()

    print(f"  wrote {result.written}, already stored {result.skipped}")
    print(f"  quality: {result.quality.to_dict()}")
    await dispose_engine()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
