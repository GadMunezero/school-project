"""Fills must be ingested in the order they happened.

Ingestion is incremental: new fills continue whatever position is open for a symbol rather than
rebuilding its history. A back-dated fill would therefore be folded into a position it predates,
producing a trade whose exit is earlier than its entry — which then propagates into every derived
figure (holding time, session bucketing, the equity curve's ordering).

These tests pin both halves: the aggregator handles an interleaved stream correctly when it sees
it all at once, and the service refuses to accept a fill that arrives out of order.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.conftest import ApiUser, create_account
from tradeloom.core.enums import OrderSide
from tradeloom.services.trading.position_builder import Fill, build_trades

pytestmark = pytest.mark.anyio


def _at(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


def test_round_trips_are_separate_trades_not_one_merged_record() -> None:
    """Three flat-to-flat cycles are three trades, each internally consistent."""
    fills = [
        Fill(
            side=OrderSide.BUY,
            quantity=Decimal(1),
            price=Decimal("5431.36"),
            timestamp=_at("2025-10-02T15:00:00"),
        ),
        Fill(
            side=OrderSide.SELL,
            quantity=Decimal(1),
            price=Decimal("5143.28"),
            timestamp=_at("2025-11-07T15:05:00"),
        ),
        Fill(
            side=OrderSide.BUY,
            quantity=Decimal(1),
            price=Decimal("5202.72"),
            timestamp=_at("2025-11-07T15:52:00"),
        ),
        Fill(
            side=OrderSide.SELL,
            quantity=Decimal(1),
            price=Decimal("3780.26"),
            timestamp=_at("2026-02-13T17:35:00"),
        ),
        Fill(
            side=OrderSide.SELL,
            quantity=Decimal(1),
            price=Decimal("4580.04"),
            timestamp=_at("2026-02-27T15:15:00"),
        ),
        Fill(
            side=OrderSide.BUY,
            quantity=Decimal(1),
            price=Decimal("4602.93"),
            timestamp=_at("2026-02-27T15:40:00"),
        ),
    ]

    trades = build_trades(fills, contract_multiplier=Decimal(20)).all_trades

    assert len(trades) == 3
    assert [trade.direction.value for trade in trades] == ["long", "long", "short"]
    for trade in trades:
        assert trade.exit_timestamp is not None
        assert trade.exit_timestamp >= trade.entry_timestamp, "a trade exited before it opened"


def test_shuffled_fills_produce_the_same_trades_in_one_call() -> None:
    """Order within a single call does not matter — the builder sorts before walking."""
    fills = [
        Fill(
            side=OrderSide.BUY,
            quantity=Decimal(100),
            price=Decimal("50"),
            timestamp=_at("2026-01-05T14:30:00"),
            sequence=0,
        ),
        Fill(
            side=OrderSide.SELL,
            quantity=Decimal(100),
            price=Decimal("55"),
            timestamp=_at("2026-01-06T14:30:00"),
            sequence=1,
        ),
        Fill(
            side=OrderSide.BUY,
            quantity=Decimal(50),
            price=Decimal("52"),
            timestamp=_at("2026-01-08T14:30:00"),
            sequence=2,
        ),
        Fill(
            side=OrderSide.SELL,
            quantity=Decimal(50),
            price=Decimal("58"),
            timestamp=_at("2026-01-09T14:30:00"),
            sequence=3,
        ),
    ]

    ordered = build_trades(fills, contract_multiplier=Decimal(1)).all_trades
    shuffled = build_trades(
        [fills[2], fills[0], fills[3], fills[1]], contract_multiplier=Decimal(1)
    ).all_trades

    assert len(ordered) == len(shuffled) == 2
    assert [t.net_pnl for t in ordered] == [t.net_pnl for t in shuffled]
    assert [t.entry_timestamp for t in ordered] == [t.entry_timestamp for t in shuffled]


async def test_a_backdated_fill_is_refused_rather_than_corrupting_the_open_trade(
    alice: ApiUser,
) -> None:
    """A fill older than the open position it would join must be rejected, and change nothing."""
    account = await create_account(alice)
    opened_at = datetime(2026, 6, 1, 14, 30, tzinfo=UTC)

    # Open a position and leave it open.
    response = await alice.post(
        "/api/v1/trades",
        json={
            "account_id": account["id"],
            "symbol": "NVLX",
            "asset_type": "equity",
            "fills": [
                {
                    "side": "buy",
                    "quantity": "100",
                    "price": "120.00",
                    "timestamp": opened_at.isoformat(),
                }
            ],
        },
    )
    assert response.status_code in (200, 201), response.text

    # Now submit a fill dated months earlier for the same symbol.
    stale = (opened_at - timedelta(days=90)).isoformat()
    response = await alice.post(
        "/api/v1/trades",
        json={
            "account_id": account["id"],
            "symbol": "NVLX",
            "asset_type": "equity",
            "fills": [{"side": "sell", "quantity": "100", "price": "130.00", "timestamp": stale}],
        },
    )
    assert response.status_code == 409, response.text  # UnprocessableStateError -> Conflict
    assert "before the open" in response.json()["error"]["message"]

    # The open position is untouched, and no trade with an impossible ordering exists.
    listing = await alice.get("/api/v1/trades", params={"page_size": 50})
    assert listing.status_code == 200
    trades = listing.json()["data"]
    assert len(trades) == 1
    assert trades[0]["status"] == "open"
    for trade in trades:
        if trade["exit_timestamp"]:
            assert trade["exit_timestamp"] >= trade["entry_timestamp"]


async def test_fills_recorded_forward_in_time_are_accepted(alice: ApiUser) -> None:
    """The ordinary case still works: a later fill closes the open position."""
    account = await create_account(alice)
    opened_at = datetime(2026, 6, 1, 14, 30, tzinfo=UTC)

    await alice.post(
        "/api/v1/trades",
        json={
            "account_id": account["id"],
            "symbol": "ARBOR",
            "asset_type": "equity",
            "fills": [
                {
                    "side": "buy",
                    "quantity": "10",
                    "price": "40.00",
                    "timestamp": opened_at.isoformat(),
                }
            ],
        },
    )
    response = await alice.post(
        "/api/v1/trades",
        json={
            "account_id": account["id"],
            "symbol": "ARBOR",
            "asset_type": "equity",
            "fills": [
                {
                    "side": "sell",
                    "quantity": "10",
                    "price": "44.00",
                    "timestamp": (opened_at + timedelta(hours=3)).isoformat(),
                }
            ],
        },
    )
    assert response.status_code in (200, 201), response.text

    listing = await alice.get("/api/v1/trades", params={"page_size": 50})
    trades = listing.json()["data"]
    assert len(trades) == 1
    assert trades[0]["status"] == "closed"
    assert trades[0]["exit_timestamp"] >= trades[0]["entry_timestamp"]
    assert Decimal(trades[0]["gross_pnl"]) == Decimal("40.00")
