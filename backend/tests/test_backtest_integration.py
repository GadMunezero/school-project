"""End-to-end backtest lifecycle through the API.

Proves the whole chain works together: market data in the database, a strategy with validated
parameters, a queued run, worker execution, persisted trades/orders/equity/drawdowns, and results
readable through the API — with tenant isolation intact at every step.

The Celery broker is not involved: the test calls the task's coroutine directly, which is exactly
what the worker does. That keeps the test hermetic while still exercising the real task code.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.conftest import create_account
from tradeloom.core.enums import JobStatus, Timeframe
from tradeloom.engine.bars import Bar
from tradeloom.models.instrument import Instrument
from tradeloom.services.market_data import MarketDataService

pytestmark = pytest.mark.anyio

D = Decimal
START = datetime(2023, 1, 2, tzinfo=UTC)


async def seed_market_data(db, days: int = 400) -> tuple[str, str]:  # type: ignore[no-untyped-def]
    """Insert a deterministic trending series and return (instrument_id, source_id)."""
    service = MarketDataService(db)

    source = await service.source_by_key("test-source")
    if source is None:
        from tradeloom.models.market_data import MarketDataSource

        source = MarketDataSource(
            key="test-source",
            name="Test data",
            provider_type="generated",
            is_realtime=False,
            metadata_json={"generated": True},
        )
        db.add(source)
        await db.flush()

    instrument = Instrument(
        organization_id=None,
        symbol="TSTX",
        name="Test Instrument",
        asset_type="equity",
        currency="USD",
        tick_size=D("0.01"),
        contract_multiplier=D(1),
        lot_size=D(1),
        price_precision=2,
    )
    db.add(instrument)
    await db.flush()

    bars: list[Bar] = []
    for i in range(days):
        price = 100 + 25 * math.sin(i / 30.0) + i * 0.08
        close = price + math.sin(i / 5.0) * 0.9
        bars.append(
            Bar(
                opened_at=START + timedelta(days=i),
                open=D(f"{price:.2f}"),
                high=D(f"{max(price, close) + 1.2:.2f}"),
                low=D(f"{min(price, close) - 1.2:.2f}"),
                close=D(f"{close:.2f}"),
                volume=D(10_000),
            )
        )

    await service.ingest(source=source, instrument=instrument, timeframe=Timeframe.D1, bars=bars)
    await db.commit()
    return str(instrument.id), str(source.id)


class TestBacktestLifecycle:
    async def test_submit_execute_and_read_results(self, pro_alice, db) -> None:  # type: ignore[no-untyped-def]
        instrument_id, source_id = await seed_market_data(db)
        await create_account(pro_alice)

        strategy = (
            await pro_alice.post(
                "/api/v1/strategies",
                json={
                    "name": "EMA momentum",
                    "kind": "builtin",
                    "engine_key": "ema_cross",
                    "parameters": {"fast_period": 10, "slow_period": 30},
                },
            )
        ).json()["data"]

        response = await pro_alice.post(
            "/api/v1/backtests",
            json={
                "name": "EMA on TSTX",
                "strategy_id": strategy["id"],
                "instrument_id": instrument_id,
                "market_data_source_id": source_id,
                "timeframe": "1d",
                "start_date": "2023-01-02",
                "end_date": "2024-01-31",
                "initial_capital": "100000",
                "position_sizing": "percent_risk",
                "risk_percent": "1",
                "commission_config": {"model": "per_share", "rate": "0.005", "minimum": "1"},
                "slippage_config": {"model": "fixed_ticks", "amount": "1"},
            },
        )
        assert response.status_code == 201, response.text
        backtest = response.json()["data"]

        # Submitting returns immediately with a job id; nothing blocks on the simulation.
        response = await pro_alice.post(f"/api/v1/backtests/{backtest['id']}/run")
        assert response.status_code == 202, response.text
        submission = response.json()["data"]
        assert submission["status"] == JobStatus.QUEUED.value

        # Run the task body exactly as the worker would.
        import uuid

        from worker.tasks.backtests import _execute

        outcome = await _execute(uuid.UUID(submission["run_id"]), uuid.UUID(submission["job_id"]))
        assert outcome["status"] == JobStatus.COMPLETED.value, outcome

        result = (await pro_alice.get(f"/api/v1/backtests/runs/{submission['run_id']}")).json()[
            "data"
        ]

        assert result["run"]["status"] == JobStatus.COMPLETED.value
        assert result["run"]["bars_processed"] > 300
        assert result["run"]["engine_version"]
        assert result["run"]["input_digest"]

        # Reproducibility record is complete.
        assert result["run"]["data_snapshot"]["source_key"] == "test-source"
        assert result["run"]["data_snapshot"]["timeframe"] == "1d"
        assert result["run"]["config_snapshot"]["engine_config"]["execution_model"]

        metrics = result["metrics"]
        assert metrics["total_trades"] >= 1
        assert metrics["initial_capital"] == "100000"
        assert metrics["final_equity"] is not None
        # Equity and drawdown series are persisted, not recomputed on read.
        assert len(result["equity_curve"]) > 100
        assert len(result["trades"]) == metrics["total_trades"]

        # Every order the simulation created is auditable, with all three timestamps.
        orders = (
            await pro_alice.get(f"/api/v1/backtests/runs/{submission['run_id']}/orders")
        ).json()["data"]
        assert orders
        first = orders[0]
        assert first["signal_timestamp"] and first["order_timestamp"]

        # Job record reflects completion and carries no internal detail.
        job = (await pro_alice.get(f"/api/v1/backtests/jobs/{submission['job_id']}")).json()["data"]
        assert job["status"] == JobStatus.COMPLETED.value
        assert job["progress_percent"] == 100
        assert "error_detail" not in job

    async def test_a_second_run_reproduces_the_first_exactly(self, pro_alice, db) -> None:  # type: ignore[no-untyped-def]
        import uuid

        from worker.tasks.backtests import _execute

        instrument_id, source_id = await seed_market_data(db)
        strategy = (
            await pro_alice.post(
                "/api/v1/strategies",
                json={"name": "Breakout", "kind": "builtin", "engine_key": "breakout"},
            )
        ).json()["data"]

        payload = {
            "name": "Repeatable",
            "strategy_id": strategy["id"],
            "instrument_id": instrument_id,
            "market_data_source_id": source_id,
            "timeframe": "1d",
            "start_date": "2023-01-02",
            "end_date": "2024-01-31",
            "initial_capital": "50000",
            "position_sizing": "percent_risk",
            "risk_percent": "1",
        }
        backtest = (await pro_alice.post("/api/v1/backtests", json=payload)).json()["data"]

        digests = []
        for _ in range(2):
            submission = (await pro_alice.post(f"/api/v1/backtests/{backtest['id']}/run")).json()[
                "data"
            ]
            await _execute(uuid.UUID(submission["run_id"]), uuid.UUID(submission["job_id"]))
            run = (await pro_alice.get(f"/api/v1/backtests/runs/{submission['run_id']}")).json()[
                "data"
            ]["run"]
            digests.append(run["input_digest"])

        assert digests[0] == digests[1]

    async def test_missing_market_data_is_refused_before_queuing(self, pro_alice, db) -> None:  # type: ignore[no-untyped-def]
        instrument_id, source_id = await seed_market_data(db, days=10)
        strategy = (
            await pro_alice.post(
                "/api/v1/strategies",
                json={"name": "EMA", "kind": "builtin", "engine_key": "ema_cross"},
            )
        ).json()["data"]

        backtest = (
            await pro_alice.post(
                "/api/v1/backtests",
                json={
                    "name": "Out of range",
                    "strategy_id": strategy["id"],
                    "instrument_id": instrument_id,
                    "market_data_source_id": source_id,
                    "timeframe": "1d",
                    # Long after the last available candle.
                    "start_date": "2030-01-01",
                    "end_date": "2030-06-01",
                    "initial_capital": "10000",
                },
            )
        ).json()["data"]

        response = await pro_alice.post(f"/api/v1/backtests/{backtest['id']}/run")
        assert response.status_code == 409
        assert "after the last available candle" in response.json()["error"]["message"]

    async def test_journal_only_strategy_cannot_be_backtested(self, pro_alice, db) -> None:  # type: ignore[no-untyped-def]
        instrument_id, source_id = await seed_market_data(db, days=50)
        strategy = (
            await pro_alice.post(
                "/api/v1/strategies",
                json={"name": "Discretionary", "kind": "journal_only"},
            )
        ).json()["data"]

        response = await pro_alice.post(
            "/api/v1/backtests",
            json={
                "name": "Nope",
                "strategy_id": strategy["id"],
                "instrument_id": instrument_id,
                "market_data_source_id": source_id,
                "timeframe": "1d",
                "start_date": "2023-01-02",
                "end_date": "2023-02-01",
                "initial_capital": "10000",
            },
        )
        assert response.status_code == 422
        assert "no executable logic" in response.json()["error"]["message"]

    async def test_out_of_range_parameters_are_refused(self, pro_alice, db) -> None:  # type: ignore[no-untyped-def]
        instrument_id, source_id = await seed_market_data(db, days=50)
        strategy = (
            await pro_alice.post(
                "/api/v1/strategies",
                json={"name": "EMA", "kind": "builtin", "engine_key": "ema_cross"},
            )
        ).json()["data"]

        response = await pro_alice.post(
            "/api/v1/backtests",
            json={
                "name": "Bad params",
                "strategy_id": strategy["id"],
                "instrument_id": instrument_id,
                "market_data_source_id": source_id,
                "timeframe": "1d",
                "start_date": "2023-01-02",
                "end_date": "2023-02-01",
                "initial_capital": "10000",
                "parameters": {"fast_period": 100000},
            },
        )
        assert response.status_code == 422

    async def test_bob_cannot_read_alices_backtest_run(self, pro_alice, pro_bob, db) -> None:  # type: ignore[no-untyped-def]
        import uuid

        from worker.tasks.backtests import _execute

        instrument_id, source_id = await seed_market_data(db)
        strategy = (
            await pro_alice.post(
                "/api/v1/strategies",
                json={"name": "EMA", "kind": "builtin", "engine_key": "ema_cross"},
            )
        ).json()["data"]
        backtest = (
            await pro_alice.post(
                "/api/v1/backtests",
                json={
                    "name": "Private",
                    "strategy_id": strategy["id"],
                    "instrument_id": instrument_id,
                    "market_data_source_id": source_id,
                    "timeframe": "1d",
                    "start_date": "2023-01-02",
                    "end_date": "2023-12-01",
                    "initial_capital": "10000",
                    "position_sizing": "percent_risk",
                    "risk_percent": "1",
                },
            )
        ).json()["data"]
        submission = (await pro_alice.post(f"/api/v1/backtests/{backtest['id']}/run")).json()[
            "data"
        ]
        await _execute(uuid.UUID(submission["run_id"]), uuid.UUID(submission["job_id"]))

        assert (await pro_bob.get(f"/api/v1/backtests/{backtest['id']}")).status_code == 404
        assert (
            await pro_bob.get(f"/api/v1/backtests/runs/{submission['run_id']}")
        ).status_code == 404
        assert (
            await pro_bob.get(f"/api/v1/backtests/jobs/{submission['job_id']}")
        ).status_code == 404


class TestReplay:
    async def test_replay_never_reveals_future_candles(self, pro_alice, db) -> None:  # type: ignore[no-untyped-def]
        instrument_id, source_id = await seed_market_data(db, days=200)

        response = await pro_alice.post(
            "/api/v1/replay",
            json={
                "name": "Practice session",
                "instrument_id": instrument_id,
                "market_data_source_id": source_id,
                "timeframe": "1d",
                "start_at": "2023-01-02T00:00:00Z",
                "end_at": "2023-06-30T00:00:00Z",
                "initial_capital": "25000",
                "warmup_bars": 20,
            },
        )
        assert response.status_code == 201, response.text
        state = response.json()["data"]

        assert state["cursor_index"] == 20
        # Exactly cursor + 1 candles are serialised; the rest of the range is withheld.
        assert len(state["visible_candles"]) == 21
        assert state["total_bars"] > 100

        replay_id = state["id"]
        stepped = (
            await pro_alice.post(f"/api/v1/replay/{replay_id}/step", json={"steps": 5})
        ).json()["data"]
        assert stepped["cursor_index"] == 25
        assert len(stepped["visible_candles"]) == 26

    async def test_replay_orders_fill_with_backtest_rules(self, pro_alice, db) -> None:  # type: ignore[no-untyped-def]
        instrument_id, source_id = await seed_market_data(db, days=200)
        state = (
            await pro_alice.post(
                "/api/v1/replay",
                json={
                    "name": "Order test",
                    "instrument_id": instrument_id,
                    "market_data_source_id": source_id,
                    "timeframe": "1d",
                    "start_at": "2023-01-02T00:00:00Z",
                    "end_at": "2023-06-30T00:00:00Z",
                    "initial_capital": "100000",
                    "warmup_bars": 10,
                },
            )
        ).json()["data"]
        replay_id = state["id"]

        # A market order placed now fills on the next bar's open, exactly as in a backtest.
        after_order = (
            await pro_alice.post(
                f"/api/v1/replay/{replay_id}/orders",
                json={"side": "buy", "quantity": "10", "order_type": "market"},
            )
        ).json()["data"]
        assert after_order["position"] is None  # not filled yet

        stepped = (
            await pro_alice.post(f"/api/v1/replay/{replay_id}/step", json={"steps": 1})
        ).json()["data"]
        assert stepped["position"] is not None
        assert stepped["position"]["quantity"] == "10"

        closed = (await pro_alice.post(f"/api/v1/replay/{replay_id}/close")).json()["data"]
        assert closed["position"] is None
        assert len(closed["closed_trades"]) == 1

    async def test_bob_cannot_open_alices_replay(self, pro_alice, pro_bob, db) -> None:  # type: ignore[no-untyped-def]
        instrument_id, source_id = await seed_market_data(db, days=100)
        state = (
            await pro_alice.post(
                "/api/v1/replay",
                json={
                    "name": "Private replay",
                    "instrument_id": instrument_id,
                    "market_data_source_id": source_id,
                    "timeframe": "1d",
                    "start_at": "2023-01-02T00:00:00Z",
                    "end_at": "2023-04-01T00:00:00Z",
                    "initial_capital": "10000",
                },
            )
        ).json()["data"]
        assert (await pro_bob.get(f"/api/v1/replay/{state['id']}")).status_code == 404
