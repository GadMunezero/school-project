"""Backtesting engine tests.

Fill prices are asserted exactly. The engine's value depends entirely on whether a stop fills at
the stop price or at a gapped open, so these cases are hand-computed rather than approximated.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradeloom.core.enums import (
    AssetType,
    CommissionModelType,
    ExecutionModelType,
    IntrabarPriority,
    PositionSizingType,
    SlippageModelType,
)
from tradeloom.engine.bars import Bar, BarSeries, BarValidationError, BarWindow, LookAheadError
from tradeloom.engine.config import (
    BacktestConfig,
    CommissionConfig,
    RiskConfig,
    SessionConfig,
    SlippageConfig,
    SpreadConfig,
)
from tradeloom.engine.indicators import ATR, EMA, RSI, SMA, RollingHigh
from tradeloom.engine.registry import (
    STRATEGY_REGISTRY,
    UnknownStrategyError,
    build_strategy,
    get_strategy,
)
from tradeloom.engine.runner import BacktestRunner
from tradeloom.engine.strategy import Strategy, StrategyContext, StrategyParameterError

D = Decimal
T0 = datetime(2024, 1, 2, tzinfo=UTC)


def bar(day: int, o: str, h: str, low: str, c: str) -> Bar:
    return Bar(
        opened_at=T0 + timedelta(days=day),
        open=D(o),
        high=D(h),
        low=D(low),
        close=D(c),
        volume=D(1000),
    )


def base_config(**overrides) -> BacktestConfig:  # type: ignore[no-untyped-def]
    defaults = {
        "symbol": "TEST",
        "initial_capital": D("100000"),
        "risk": RiskConfig(sizing=PositionSizingType.FIXED_QUANTITY, value=D(100)),
    }
    defaults.update(overrides)
    return BacktestConfig(**defaults)  # type: ignore[arg-type]


class EntryOnFirstBar(Strategy):
    """Enters long on bar 0 with a fixed size, stop and target."""

    key = "test_entry"
    name = "test"
    description = ""
    parameters = ()

    def __init__(self, quantity=D(100), stop=None, target=None, short=False) -> None:  # type: ignore[no-untyped-def]
        super().__init__({})
        self.quantity = quantity
        self.stop = stop
        self.target = target
        self.short = short
        self.fired = False

    def on_bar(self, ctx: StrategyContext) -> None:
        if self.fired or ctx.index != 0:
            return
        self.fired = True
        if self.short:
            ctx.enter_short(quantity=self.quantity, stop_loss=self.stop, take_profit=self.target)
        else:
            ctx.enter_long(quantity=self.quantity, stop_loss=self.stop, take_profit=self.target)


class TestBarSeries:
    def test_invalid_ohlc_is_rejected(self) -> None:
        with pytest.raises(BarValidationError):
            Bar(opened_at=T0, open=D(10), high=D(9), low=D(11), close=D(10))

    def test_close_outside_range_is_rejected(self) -> None:
        with pytest.raises(BarValidationError):
            Bar(opened_at=T0, open=D(10), high=D(11), low=D(9), close=D(15))

    def test_naive_timestamps_are_rejected(self) -> None:
        with pytest.raises(BarValidationError):
            Bar(
                opened_at=datetime(2024, 1, 1),
                open=D(10),
                high=D(11),
                low=D(9),
                close=D(10),
            )

    def test_out_of_order_series_is_rejected(self) -> None:
        with pytest.raises(BarValidationError):
            BarSeries([bar(1, "10", "11", "9", "10"), bar(0, "10", "11", "9", "10")])

    def test_duplicate_timestamps_are_rejected(self) -> None:
        with pytest.raises(BarValidationError):
            BarSeries([bar(0, "10", "11", "9", "10"), bar(0, "10", "11", "9", "10")])


class TestLookAheadPrevention:
    def test_window_refuses_future_bars(self) -> None:
        series = BarSeries([bar(i, "10", "11", "9", "10") for i in range(5)])
        window = BarWindow(series, 2)
        assert len(window) == 3
        assert window[2].opened_at == series[2].opened_at
        with pytest.raises(LookAheadError):
            _ = window[3]

    def test_negative_index_reads_the_current_bar(self) -> None:
        series = BarSeries([bar(i, "10", str(20 + i), "9", str(10 + i)) for i in range(5)])
        window = BarWindow(series, 2)
        assert window[-1].close == D(12)

    def test_rolling_high_excludes_the_current_bar(self) -> None:
        # A breakout above "the 3-bar high" must not be measured against this bar's own high.
        indicator = RollingHigh(3, exclude_current=True)
        for value in ("10", "12", "11", "20"):
            indicator.update(bar(0, value, value, value, value))
        assert indicator.value == D(12)


class TestIndicators:
    def test_sma_needs_a_full_window(self) -> None:
        sma = SMA(3)
        assert sma.update(bar(0, "1", "1", "1", "1")) is None
        assert sma.update(bar(1, "2", "2", "2", "2")) is None
        assert sma.update(bar(2, "3", "3", "3", "3")) == D(2)

    def test_ema_is_seeded_with_the_sma(self) -> None:
        ema = EMA(3)
        for value in ("1", "2", "3"):
            result = ema.update(bar(0, value, value, value, value))
        assert result == D(2)

    def test_rsi_of_a_pure_uptrend_is_100(self) -> None:
        rsi = RSI(3)
        for i in range(1, 8):
            rsi.update(bar(i, str(i), str(i), str(i), str(i)))
        assert rsi.value == D(100)

    def test_atr_is_positive_for_a_ranging_market(self) -> None:
        atr = ATR(3)
        for i in range(5):
            atr.update(bar(i, "10", "12", "8", "11"))
        assert atr.value is not None and atr.value > 0


class TestExecutionModel:
    def test_next_bar_open_is_the_default(self) -> None:
        series = BarSeries(
            [
                bar(0, "100", "101", "99", "100"),
                bar(1, "105", "106", "104", "105"),
                bar(2, "110", "111", "109", "110"),
            ]
        )
        result = BacktestRunner(config=base_config(), strategy=EntryOnFirstBar(), bars=series).run()
        # Signal on bar 0's close fills at bar 1's open, not bar 0's close.
        assert result.trades[0].entry_price == D("105")

    def test_current_bar_close_fills_immediately(self) -> None:
        series = BarSeries(
            [
                bar(0, "100", "101", "99", "100"),
                bar(1, "105", "106", "104", "105"),
                bar(2, "110", "111", "109", "110"),
            ]
        )
        result = BacktestRunner(
            config=base_config(execution_model=ExecutionModelType.CURRENT_BAR_CLOSE),
            strategy=EntryOnFirstBar(),
            bars=series,
        ).run()
        assert result.trades[0].entry_price == D("100")

    def test_stop_fills_at_the_stop_when_it_trades_within_the_bar(self) -> None:
        series = BarSeries(
            [
                bar(0, "100", "101", "99", "100"),
                bar(1, "100", "102", "99", "101"),
                bar(2, "98", "99", "94", "96"),
                bar(3, "96", "97", "95", "96"),
            ]
        )
        result = BacktestRunner(
            config=base_config(), strategy=EntryOnFirstBar(stop=D("95")), bars=series
        ).run()
        trade = result.trades[0]
        assert trade.exit_price == D("95")
        assert trade.exit_reason == "stop_loss"

    def test_gap_through_the_stop_fills_at_the_open(self) -> None:
        series = BarSeries(
            [
                bar(0, "100", "101", "99", "100"),
                bar(1, "100", "102", "99", "101"),
                bar(2, "90", "91", "88", "89"),
                bar(3, "89", "90", "88", "89"),
            ]
        )
        result = BacktestRunner(
            config=base_config(), strategy=EntryOnFirstBar(stop=D("95")), bars=series
        ).run()
        # The market never traded at 95 on that bar; filling there would be fiction.
        assert result.trades[0].exit_price == D("90")

    def test_target_fills_at_the_limit(self) -> None:
        series = BarSeries(
            [
                bar(0, "100", "101", "99", "100"),
                bar(1, "100", "102", "99", "101"),
                bar(2, "103", "112", "102", "110"),
                bar(3, "110", "111", "109", "110"),
            ]
        )
        result = BacktestRunner(
            config=base_config(), strategy=EntryOnFirstBar(target=D("108")), bars=series
        ).run()
        assert result.trades[0].exit_price == D("108")
        assert result.trades[0].exit_reason == "take_profit"

    def test_stop_wins_when_a_bar_spans_both_levels(self) -> None:
        # The bar's range covers stop and target; the pessimistic default assumes the stop.
        series = BarSeries(
            [
                bar(0, "100", "101", "99", "100"),
                bar(1, "100", "102", "99", "101"),
                bar(2, "100", "115", "90", "100"),
            ]
        )
        result = BacktestRunner(
            config=base_config(intrabar_priority=IntrabarPriority.STOP_FIRST),
            strategy=EntryOnFirstBar(stop=D("95"), target=D("110")),
            bars=series,
        ).run()
        assert result.trades[0].exit_reason == "stop_loss"

    def test_target_first_can_be_chosen_explicitly(self) -> None:
        series = BarSeries(
            [
                bar(0, "100", "101", "99", "100"),
                bar(1, "100", "102", "99", "101"),
                bar(2, "100", "115", "90", "100"),
            ]
        )
        result = BacktestRunner(
            config=base_config(intrabar_priority=IntrabarPriority.TARGET_FIRST),
            strategy=EntryOnFirstBar(stop=D("95"), target=D("110")),
            bars=series,
        ).run()
        assert result.trades[0].exit_reason == "take_profit"

    def test_open_position_is_closed_at_the_final_bar(self) -> None:
        series = BarSeries(
            [
                bar(0, "100", "101", "99", "100"),
                bar(1, "100", "102", "99", "101"),
                bar(2, "101", "103", "100", "102"),
            ]
        )
        result = BacktestRunner(config=base_config(), strategy=EntryOnFirstBar(), bars=series).run()
        assert len(result.trades) == 1
        assert result.trades[0].exit_reason == "end_of_data"
        assert any("open position was closed" in w for w in result.warnings)


class TestCosts:
    def test_commission_is_charged_on_both_fills(self) -> None:
        series = BarSeries(
            [
                bar(0, "100", "101", "99", "100"),
                bar(1, "100", "102", "99", "101"),
                bar(2, "110", "111", "109", "110"),
            ]
        )
        result = BacktestRunner(
            config=base_config(
                commission=CommissionConfig(model=CommissionModelType.PER_TRADE, rate=D("5"))
            ),
            strategy=EntryOnFirstBar(),
            bars=series,
        ).run()
        assert result.trades[0].commission == D("10")

    def test_slippage_always_works_against_the_trade(self) -> None:
        series = BarSeries(
            [
                bar(0, "100", "101", "99", "100"),
                bar(1, "100", "102", "99", "101"),
                bar(2, "110", "111", "109", "110"),
            ]
        )
        result = BacktestRunner(
            config=base_config(
                slippage=SlippageConfig(
                    model=SlippageModelType.FIXED_TICKS, amount=D("10"), tick_size=D("0.01")
                )
            ),
            strategy=EntryOnFirstBar(),
            bars=series,
        ).run()
        trade = result.trades[0]
        # Buy fills 0.10 higher, sell fills 0.10 lower.
        assert trade.entry_price == D("100.1")
        assert trade.exit_price == D("109.9")

    def test_spread_costs_half_on_each_side(self) -> None:
        series = BarSeries(
            [
                bar(0, "100", "101", "99", "100"),
                bar(1, "100", "102", "99", "101"),
                bar(2, "110", "111", "109", "110"),
            ]
        )
        result = BacktestRunner(
            config=base_config(spread=SpreadConfig(absolute=D("0.20"))),
            strategy=EntryOnFirstBar(),
            bars=series,
        ).run()
        assert result.trades[0].entry_price == D("100.1")
        assert result.trades[0].exit_price == D("109.9")


class TestRiskManagement:
    def test_percent_risk_sizing_uses_the_stop_distance(self) -> None:
        series = BarSeries(
            [
                bar(0, "100", "101", "99", "100"),
                bar(1, "100", "102", "99", "101"),
                bar(2, "110", "111", "109", "110"),
            ]
        )
        config = base_config(risk=RiskConfig(sizing=PositionSizingType.PERCENT_RISK, value=D("1")))
        # 1% of 100,000 = 1,000 risk; stop 5 points below the 100 close -> 200 shares.
        result = BacktestRunner(
            config=config, strategy=EntryOnFirstBar(quantity=None, stop=D("95")), bars=series
        ).run()
        assert result.trades[0].quantity == D("200")

    def test_risk_sizing_without_a_stop_rejects_the_signal(self) -> None:
        series = BarSeries(
            [
                bar(0, "100", "101", "99", "100"),
                bar(1, "100", "102", "99", "101"),
                bar(2, "110", "111", "109", "110"),
            ]
        )
        config = base_config(risk=RiskConfig(sizing=PositionSizingType.PERCENT_RISK, value=D("1")))
        result = BacktestRunner(
            config=config, strategy=EntryOnFirstBar(quantity=None, stop=None), bars=series
        ).run()
        # No trade, and the reason is reported rather than left mysterious.
        assert result.trades == []
        assert any("rounded to zero" in w or "size" in w for w in result.warnings)

    def test_buying_power_limits_the_position(self) -> None:
        series = BarSeries(
            [
                bar(0, "100", "101", "99", "100"),
                bar(1, "100", "102", "99", "101"),
                bar(2, "110", "111", "109", "110"),
            ]
        )
        config = BacktestConfig(
            symbol="TEST",
            initial_capital=D("1000"),
            risk=RiskConfig(sizing=PositionSizingType.FIXED_QUANTITY, value=D(1000)),
        )
        result = BacktestRunner(
            config=config, strategy=EntryOnFirstBar(quantity=None), bars=series
        ).run()
        # 1,000 capital at 1x leverage cannot buy 1,000 shares at ~100.
        assert result.trades == [] or result.trades[0].quantity <= D(10)


class TestDeterminism:
    def _series(self) -> BarSeries:
        bars = []
        price = 100.0
        for i in range(300):
            price += (1.7 if i % 3 else -1.1) * (1 + (i % 7) / 10)
            price = max(20.0, price)
            bars.append(
                bar(
                    i,
                    f"{price:.2f}",
                    f"{price + 1.5:.2f}",
                    f"{price - 1.5:.2f}",
                    f"{price + 0.4:.2f}",
                )
            )
        return BarSeries(bars)

    @pytest.mark.parametrize("key", sorted(STRATEGY_REGISTRY))
    def test_every_builtin_runs_and_repeats_exactly(self, key: str) -> None:
        series = self._series()
        config = base_config(risk=RiskConfig(sizing=PositionSizingType.PERCENT_RISK, value=D("1")))
        first = BacktestRunner(config=config, strategy=build_strategy(key), bars=series).run()
        second = BacktestRunner(config=config, strategy=build_strategy(key), bars=series).run()

        assert first.report.metrics == second.report.metrics
        assert first.input_digest == second.input_digest
        assert first.bars_processed == 300

    def test_a_different_parameter_changes_the_digest(self) -> None:
        series = self._series()
        config = base_config()
        a = BacktestRunner(
            config=config, strategy=build_strategy("ema_cross", {"fast_period": 5}), bars=series
        ).run()
        b = BacktestRunner(
            config=config, strategy=build_strategy("ema_cross", {"fast_period": 8}), bars=series
        ).run()
        assert a.input_digest != b.input_digest


class TestStrategySafety:
    def test_unknown_strategy_key_is_refused(self) -> None:
        with pytest.raises(UnknownStrategyError):
            get_strategy("os.system")

    def test_out_of_range_parameter_is_refused(self) -> None:
        with pytest.raises(StrategyParameterError):
            build_strategy("ema_cross", {"fast_period": 9999})

    def test_unknown_parameter_is_refused_not_ignored(self) -> None:
        # Silently dropping a misspelled parameter would run a different strategy than configured.
        with pytest.raises(StrategyParameterError):
            build_strategy("ema_cross", {"fastperiod": 12})

    def test_defaults_are_applied_for_omitted_parameters(self) -> None:
        strategy = build_strategy("ema_cross", {})
        assert strategy.params["fast_period"] == 12
        assert strategy.params["slow_period"] == 26


class TestEmptyAndEdgeCases:
    def test_no_bars_produces_an_empty_but_valid_report(self) -> None:
        result = BacktestRunner(
            config=base_config(), strategy=EntryOnFirstBar(), bars=BarSeries([])
        ).run()
        assert result.report.metrics["total_trades"] == 0
        assert result.report.metrics["profit_factor"] is None
        assert any("no market data" in w for w in result.warnings)

    def test_all_winning_trades_have_no_profit_factor(self) -> None:
        series = BarSeries(
            [
                bar(0, "100", "101", "99", "100"),
                bar(1, "100", "102", "99", "101"),
                bar(2, "110", "111", "109", "110"),
            ]
        )
        result = BacktestRunner(config=base_config(), strategy=EntryOnFirstBar(), bars=series).run()
        metrics = result.report.metrics
        assert metrics["winning_trades"] == 1
        assert metrics["losing_trades"] == 0
        # Undefined, not infinite and not zero.
        assert metrics["profit_factor"] is None
        assert metrics["win_rate"] == "100"

    def test_short_trades_are_supported(self) -> None:
        series = BarSeries(
            [
                bar(0, "100", "101", "99", "100"),
                bar(1, "100", "102", "99", "101"),
                bar(2, "90", "91", "89", "90"),
            ]
        )
        result = BacktestRunner(
            config=base_config(), strategy=EntryOnFirstBar(short=True), bars=series
        ).run()
        trade = result.trades[0]
        assert trade.direction.value == "short"
        assert trade.net_pnl == D("1000")  # 100 shares * 10 points


class TestBacktestTradingDay:
    """A backtest on futures must report the session's day, not the calendar's.

    The instrument is known for the whole run, so every trade and every equity sample can say
    which trading day it belongs to. Without that, a run on a contract that opens at 18:00 New
    York reports entries on Sunday — a session the exchange never held — and splits each session's
    return across two rows of the daily table.
    """

    #: Sunday 19:00 New York, which is Monday's futures session. March, so ET is UTC-4.
    SUNDAY_EVENING = datetime(2026, 3, 15, 23, 0, tzinfo=UTC)

    def _hourly(self, count: int = 6) -> BarSeries:
        # A gently rising walk; the range has to contain both ends or Bar refuses the candle.
        return BarSeries(
            [
                Bar(
                    opened_at=self.SUNDAY_EVENING + timedelta(hours=offset),
                    open=D(100) + D(offset),
                    high=D(102) + D(offset),
                    low=D(99) + D(offset),
                    close=D(101) + D(offset),
                    volume=D(1000),
                )
                for offset in range(count)
            ]
        )

    def _run(self, asset_type):  # type: ignore[no-untyped-def]
        return BacktestRunner(
            config=base_config(
                asset_type=asset_type,
                session=SessionConfig(timezone="America/New_York"),
            ),
            strategy=EntryOnFirstBar(),
            bars=self._hourly(),
        ).run()

    def test_a_futures_entry_on_sunday_evening_is_a_monday_trade(self) -> None:
        weekdays = {
            row["label"]: row["trades"]
            for row in self._run(AssetType.FUTURES).report.breakdowns["by_weekday"]
        }

        # 0 is Monday. Under calendar grouping this said 6 — Sunday.
        assert weekdays == {"0": 1}

    def test_an_equity_run_is_unaffected(self) -> None:
        weekdays = {
            row["label"]: row["trades"]
            for row in self._run(AssetType.EQUITY).report.breakdowns["by_weekday"]
        }

        assert weekdays == {"6": 1}

    def test_the_hour_breakdown_stays_on_the_wall_clock(self) -> None:
        """ "What time do I enter?" is a question about the clock, not about the session.

        20:00, not 19:00: the signal is raised on the first bar and the default execution model
        fills it at the next bar's open, so the position opens an hour later.
        """
        for asset_type in (AssetType.FUTURES, AssetType.EQUITY):
            hours = {
                row["label"]: row["trades"]
                for row in self._run(asset_type).report.breakdowns["by_hour"]
            }
            assert hours == {"20": 1}, asset_type

    def test_one_session_is_one_row_in_the_daily_table(self) -> None:
        """Six hourly bars spanning midnight are one futures session, so one daily return."""
        result = self._run(AssetType.FUTURES)
        daily = result.report.breakdowns["daily_returns"]

        assert [row["date"] for row in daily] == ["2026-03-16"]

        # The same bars split into two calendar days without the boundary.
        split = self._run(AssetType.EQUITY).report.breakdowns["daily_returns"]
        assert [row["date"] for row in split] == ["2026-03-15", "2026-03-16"]

    def test_every_equity_sample_carries_its_session(self) -> None:
        result = self._run(AssetType.FUTURES)

        assert result.equity_curve
        assert {s.trading_day.isoformat() for s in result.equity_curve} == {"2026-03-16"}

    def test_a_run_without_an_asset_type_keeps_the_old_grouping(self) -> None:
        """Nothing that omits the field changes behaviour."""
        result = self._run(None)

        assert [row["date"] for row in result.report.breakdowns["daily_returns"]] == [
            "2026-03-15",
            "2026-03-16",
        ]
