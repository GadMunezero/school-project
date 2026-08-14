"""Financial correctness tests.

Every case here asserts an exact Decimal, computed by hand. If a refactor changes a number, this
file fails — which is the point: P&L is not something to eyeball.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tradeloom.core.enums import Direction, OrderSide, TradeStatus
from tradeloom.core.money import (
    MoneyError,
    percent_change,
    safe_div,
    settle,
    to_decimal,
)
from tradeloom.services.trading.calculations import (
    Bar,
    compute_excursion,
    efficiency_ratio,
    planned_reward_risk,
    r_multiple,
    risk_amount,
    risk_per_unit,
)
from tradeloom.services.trading.position_builder import (
    Fill,
    PositionBuildError,
    build_trades,
)

T0 = datetime(2024, 3, 1, 14, 30, tzinfo=UTC)
D = Decimal


def fill(
    side: OrderSide, qty: str, price: str, minutes: int = 0, commission: str = "0", fees: str = "0"
) -> Fill:
    return Fill(
        timestamp=T0 + timedelta(minutes=minutes),
        side=side,
        quantity=D(qty),
        price=D(price),
        commission=D(commission),
        fees=D(fees),
        sequence=minutes,
    )


# --- money primitives -------------------------------------------------------


class TestMoneyPrimitives:
    def test_float_is_parsed_without_binary_error(self) -> None:
        # Decimal(0.1) would be 0.1000000000000000055511151231257827.
        assert to_decimal(0.1) == D("0.1")

    def test_accounting_negatives_and_separators(self) -> None:
        assert to_decimal("(1,234.56)") == D("-1234.56")
        assert to_decimal("1_000") == D("1000")

    def test_currency_symbols_are_rejected_by_the_ledger_primitive(self) -> None:
        # `to_decimal` guards the ledger, so decorated text is an error rather than a guess.
        # Stripping symbols is the importer's job — see `test_imports.py`.
        with pytest.raises(MoneyError):
            to_decimal("$2,500")

    def test_booleans_are_rejected(self) -> None:
        with pytest.raises(MoneyError):
            to_decimal(True)

    def test_safe_div_returns_none_rather_than_zero(self) -> None:
        # An undefined ratio must not masquerade as zero.
        assert safe_div(D(5), D(0)) is None
        assert safe_div(D(5), D(2)) == D("2.5")

    def test_percent_change_undefined_from_zero_base(self) -> None:
        assert percent_change(D(0), D(10)) is None
        assert percent_change(D(200), D(250)) == D("25")

    def test_settlement_uses_currency_minor_units(self) -> None:
        assert settle(D("10.005"), "USD") == D("10.00")  # banker's rounding, ties to even
        assert settle(D("10.015"), "USD") == D("10.02")
        assert settle(D("1234.56"), "JPY") == D("1235")
        assert settle(D("0.123456789"), "BTC") == D("0.12345679")


# --- single round trips -----------------------------------------------------


class TestSingleTrades:
    def test_winning_long(self) -> None:
        result = build_trades(
            [fill(OrderSide.BUY, "100", "50", 0, "1"), fill(OrderSide.SELL, "100", "55", 10, "1")]
        )
        trade = result.closed_trades[0]
        assert trade.direction is Direction.LONG
        assert trade.gross_pnl == D("500")
        assert trade.net_pnl == D("498")
        assert trade.status is TradeStatus.CLOSED
        # 498 / (50 * 100) = 9.96%
        assert trade.return_percentage() == D("9.96")
        assert trade.holding_seconds == 600

    def test_losing_long(self) -> None:
        result = build_trades(
            [fill(OrderSide.BUY, "50", "80", 0), fill(OrderSide.SELL, "50", "74.50", 5)]
        )
        assert result.closed_trades[0].net_pnl == D("-275")

    def test_winning_short(self) -> None:
        result = build_trades(
            [fill(OrderSide.SELL, "200", "30", 0, "2"), fill(OrderSide.BUY, "200", "27.25", 8, "2")]
        )
        trade = result.closed_trades[0]
        assert trade.direction is Direction.SHORT
        assert trade.gross_pnl == D("550")
        assert trade.net_pnl == D("546")

    def test_losing_short(self) -> None:
        result = build_trades(
            [fill(OrderSide.SELL, "10", "100", 0), fill(OrderSide.BUY, "10", "104.40", 3)]
        )
        assert result.closed_trades[0].net_pnl == D("-44")

    def test_breakeven_trade_is_neither_win_nor_loss(self) -> None:
        result = build_trades(
            [fill(OrderSide.BUY, "10", "25", 0), fill(OrderSide.SELL, "10", "25", 1)]
        )
        trade = result.closed_trades[0]
        assert trade.net_pnl == D("0")
        assert trade.gross_pnl == D("0")

    def test_futures_multiplier_scales_pnl(self) -> None:
        result = build_trades(
            [
                fill(OrderSide.BUY, "2", "4500", 0, "4"),
                fill(OrderSide.SELL, "2", "4512.50", 6, "4"),
            ],
            contract_multiplier=D("50"),
        )
        trade = result.closed_trades[0]
        # 12.50 points * 2 contracts * $50 = $1,250
        assert trade.gross_pnl == D("1250")
        assert trade.net_pnl == D("1242")

    def test_fees_and_commission_are_both_deducted(self) -> None:
        result = build_trades(
            [
                fill(OrderSide.BUY, "100", "10", 0, commission="1", fees="0.35"),
                fill(OrderSide.SELL, "100", "11", 5, commission="1", fees="0.40"),
            ]
        )
        trade = result.closed_trades[0]
        assert trade.gross_pnl == D("100")
        assert trade.commission == D("2")
        assert trade.fees == D("0.75")
        assert trade.net_pnl == D("97.25")


# --- scaling ----------------------------------------------------------------


class TestScaling:
    def test_scale_in_uses_weighted_average_cost(self) -> None:
        result = build_trades(
            [
                fill(OrderSide.BUY, "100", "10", 0),
                fill(OrderSide.BUY, "100", "20", 1),
                fill(OrderSide.SELL, "200", "30", 2),
            ]
        )
        trade = result.closed_trades[0]
        assert trade.average_entry_price == D("15.0000000000")
        assert trade.gross_pnl == D("3000")  # 200 * (30 - 15)

    def test_partial_exit_leaves_basis_unchanged(self) -> None:
        result = build_trades(
            [
                fill(OrderSide.BUY, "100", "10", 0),
                fill(OrderSide.BUY, "100", "20", 1),
                fill(OrderSide.SELL, "50", "30", 2),
            ]
        )
        open_trade = result.open_trade
        assert open_trade is not None
        assert open_trade.status is TradeStatus.PARTIALLY_CLOSED
        assert open_trade.open_quantity == D("150.0000000000")
        assert open_trade.average_entry_price == D("15.0000000000")  # unchanged by the exit
        assert open_trade.gross_pnl == D("750")  # 50 * (30 - 15)

    def test_multiple_partial_exits_blend_the_exit_price(self) -> None:
        result = build_trades(
            [
                fill(OrderSide.BUY, "100", "10", 0),
                fill(OrderSide.SELL, "50", "12", 1),
                fill(OrderSide.SELL, "50", "16", 2),
            ]
        )
        trade = result.closed_trades[0]
        assert trade.average_exit_price == D("14.0000000000")
        assert trade.gross_pnl == D("400")

    def test_commission_is_prorated_across_a_split_fill(self) -> None:
        # A 150-share sell closes 100 and opens 50; the $3 commission splits 2/1.
        result = build_trades(
            [fill(OrderSide.BUY, "100", "50", 0), fill(OrderSide.SELL, "150", "55", 1, "3")]
        )
        closed = result.closed_trades[0]
        opened = result.open_trade
        assert closed.commission == D("2")
        assert opened is not None
        assert opened.commission == D("1")

    def test_flip_closes_then_reopens_in_the_other_direction(self) -> None:
        result = build_trades(
            [fill(OrderSide.SELL, "100", "100", 0), fill(OrderSide.BUY, "150", "90", 1)]
        )
        assert len(result.closed_trades) == 1
        assert result.closed_trades[0].gross_pnl == D("1000")
        assert result.open_trade is not None
        assert result.open_trade.direction is Direction.LONG
        assert result.open_trade.open_quantity == D("50.0000000000")
        assert result.open_trade.average_entry_price == D("90.0000000000")

    def test_repeated_flips_produce_a_trade_each(self) -> None:
        result = build_trades(
            [
                fill(OrderSide.BUY, "10", "10", 0),
                fill(OrderSide.SELL, "20", "12", 1),
                fill(OrderSide.BUY, "20", "11", 2),
                fill(OrderSide.SELL, "10", "13", 3),
            ]
        )
        assert len(result.closed_trades) == 3
        assert result.open_trade is None
        assert [t.direction for t in result.closed_trades] == [
            Direction.LONG,
            Direction.SHORT,
            Direction.LONG,
        ]

    def test_fills_are_applied_in_timestamp_order(self) -> None:
        # Supplied out of order; the builder must sort before folding.
        result = build_trades(
            [fill(OrderSide.SELL, "100", "55", 10), fill(OrderSide.BUY, "100", "50", 0)]
        )
        assert result.closed_trades[0].direction is Direction.LONG
        assert result.closed_trades[0].gross_pnl == D("500")


class TestValidation:
    def test_zero_quantity_is_rejected(self) -> None:
        with pytest.raises(PositionBuildError):
            fill(OrderSide.BUY, "0", "10")

    def test_non_positive_price_is_rejected(self) -> None:
        with pytest.raises(PositionBuildError):
            fill(OrderSide.BUY, "10", "0")

    def test_no_fills_produces_no_trades(self) -> None:
        result = build_trades([])
        assert result.closed_trades == [] and result.open_trade is None


# --- risk and R -------------------------------------------------------------


class TestRiskMetrics:
    def test_risk_amount_from_stop(self) -> None:
        assert risk_amount(D("50"), D("48"), D("100"), Direction.LONG) == D("200")
        assert risk_amount(D("50"), D("52"), D("100"), Direction.SHORT) == D("200")

    def test_stop_on_the_wrong_side_is_undefined_not_negative(self) -> None:
        assert risk_per_unit(D("50"), D("52"), Direction.LONG) is None
        assert risk_amount(D("50"), D("52"), D("100"), Direction.LONG) is None

    def test_r_multiple_without_risk_is_none(self) -> None:
        # A trade with no stop has no R. Reporting 0R would corrupt every R average.
        assert r_multiple(D("500"), None) is None
        assert r_multiple(D("500"), D("0")) is None

    def test_r_multiple_exact(self) -> None:
        assert r_multiple(D("498"), D("200")) == D("2.49")
        assert r_multiple(D("-200"), D("200")) == D("-1")

    def test_planned_reward_risk(self) -> None:
        assert planned_reward_risk(D("100"), D("98"), D("106"), Direction.LONG) == D("3")
        # A target on the wrong side is not a plan.
        assert planned_reward_risk(D("100"), D("98"), D("99"), Direction.LONG) is None

    def test_efficiency_ratio(self) -> None:
        assert efficiency_ratio(D("300"), D("600")) == D("0.5")
        assert efficiency_ratio(D("300"), None) is None


class TestExcursions:
    def _bars(self) -> list[Bar]:
        return [
            Bar(opened_at=T0 + timedelta(minutes=i), high=D(high), low=D(low))
            for i, (high, low) in enumerate([("102", "99"), ("108", "101"), ("105", "96")])
        ]

    def test_long_excursions(self) -> None:
        result = compute_excursion(
            self._bars(), entry_price=D("100"), quantity=D("10"), direction=Direction.LONG
        )
        assert result.mfe_price == D("108")
        assert result.mae_price == D("96")
        assert result.mfe_amount == D("80")  # (108 - 100) * 10
        assert result.mae_amount == D("40")  # (100 - 96) * 10

    def test_short_excursions_invert(self) -> None:
        result = compute_excursion(
            self._bars(), entry_price=D("100"), quantity=D("10"), direction=Direction.SHORT
        )
        assert result.mfe_price == D("96")
        assert result.mae_price == D("108")
        assert result.mfe_amount == D("40")
        assert result.mae_amount == D("80")

    def test_no_covering_bars_yields_none_not_zero(self) -> None:
        result = compute_excursion(
            [], entry_price=D("100"), quantity=D("10"), direction=Direction.LONG
        )
        assert result.mfe_amount is None and result.mae_amount is None

    def test_excursions_are_magnitudes(self) -> None:
        # A trade that never moved favourably has MFE 0, not a negative number.
        bars = [Bar(opened_at=T0, high=D("99"), low=D("95"))]
        result = compute_excursion(
            bars, entry_price=D("100"), quantity=D("10"), direction=Direction.LONG
        )
        assert result.mfe_amount == D("0")
        assert result.mae_amount == D("50")


class TestWireSerialisation:
    """What the API puts on the wire, for the types it is opinionated about.

    Both rules exist for the same reason: the frontend displays what the backend computed and must
    never re-derive it. A Decimal sent as a JSON number loses precision in the parse; a datetime
    sent as a Unix timestamp is not a date the client can read at all.
    """

    def test_decimals_go_out_as_strings(self) -> None:
        from tradeloom.schemas.common import TradeloomModel

        class Money(TradeloomModel):
            amount: Decimal

        assert Money(amount=Decimal("0.10")).model_dump_json() == '{"amount":"0.1"}'

    def test_datetimes_go_out_as_iso_8601(self) -> None:
        """Not a Unix timestamp.

        The base model claims every field for serialisation, which switches off Pydantic's own
        datetime encoder. Until this was handled explicitly, every datetime that a router did not
        format by hand reached the client as a number — including a backtest's drawdown episodes,
        which made the results page throw.
        """
        from tradeloom.schemas.common import TradeloomModel

        class Moment(TradeloomModel):
            when: datetime

        payload = Moment(when=datetime(2026, 3, 16, 23, 0, tzinfo=UTC)).model_dump_json()
        assert payload == '{"when":"2026-03-16T23:00:00Z"}'

    def test_a_naive_datetime_is_published_as_utc(self) -> None:
        """Everything is stored in UTC, so an unlabelled value is UTC rather than server-local."""
        from tradeloom.schemas.common import TradeloomModel

        class Moment(TradeloomModel):
            when: datetime

        payload = Moment(when=datetime(2026, 3, 16, 23, 0)).model_dump_json()
        assert payload == '{"when":"2026-03-16T23:00:00Z"}'
