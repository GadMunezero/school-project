"""Candle imports reject bad rows rather than repair them.

Market data is the foundation everything else stands on: a single silently-corrected candle
propagates into every report, backtest and replay computed from that series. So the tests here are
mostly about refusal — what the importer must *not* accept.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from tradeloom.core.enums import Timeframe
from tradeloom.core.errors import UnprocessableStateError
from tradeloom.services.market_import import (
    inspect,
    parse_candles,
    suggest_mapping,
    summarise,
)

pytestmark = pytest.mark.anyio

HEADER = "Date,Open,High,Low,Close,Volume\n"


def csv_bytes(rows: str, header: str = HEADER) -> bytes:
    return (header + rows).encode()


MAPPING = {
    "timestamp": "Date",
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
}


class TestMapping:
    def test_common_header_names_are_recognised(self) -> None:
        assert suggest_mapping(["Date", "Open", "High", "Low", "Close", "Volume"]) == MAPPING

    def test_matching_ignores_case_and_separators(self) -> None:
        mapping = suggest_mapping(["date_time", "OPEN", "high price", "Low", "close", "vol"])
        assert mapping["timestamp"] == "date_time"
        assert mapping["open"] == "OPEN"
        assert mapping["high"] == "high price"
        assert mapping["volume"] == "vol"

    def test_an_unrecognised_header_is_left_unmapped_rather_than_guessed(self) -> None:
        # A wrong guess produces a plausible series of incorrect candles, which is worse than
        # asking the user which column is which.
        mapping = suggest_mapping(["ts", "px_open", "px_high", "px_low", "px_close"])
        assert "open" not in mapping
        assert "high" not in mapping

    def test_inspect_reports_headers_and_a_preview(self) -> None:
        data = csv_bytes("2026-03-02 14:30,100,110,99,105,1000\n")
        report = inspect(data)

        assert report["headers"] == ["Date", "Open", "High", "Low", "Close", "Volume"]
        assert report["suggested_mapping"] == MAPPING
        assert len(report["preview"]) == 1


class TestParsing:
    def test_a_clean_file_becomes_candles(self) -> None:
        data = csv_bytes(
            "2026-03-02 14:30,100,110,99,105,1000\n2026-03-02 15:30,105,112,104,108,900\n"
        )
        result = parse_candles(data, mapping=MAPPING, timezone="UTC")

        assert result.accepted == 2
        assert result.rejected == []
        assert result.bars[0].open == Decimal("100")
        assert result.bars[0].high == Decimal("110")
        assert result.bars[1].volume == Decimal("900")

    def test_bars_come_back_in_time_order_whatever_the_file_says(self) -> None:
        data = csv_bytes(
            "2026-03-02 15:30,105,112,104,108,900\n2026-03-02 14:30,100,110,99,105,1000\n"
        )
        result = parse_candles(data, mapping=MAPPING, timezone="UTC")

        assert [bar.open for bar in result.bars] == [Decimal("100"), Decimal("105")]

    def test_a_naive_timestamp_is_read_in_the_source_timezone_not_the_servers(self) -> None:
        data = csv_bytes("2026-03-02 09:30,100,110,99,105,1000\n")
        result = parse_candles(data, mapping=MAPPING, timezone="America/New_York")

        # 09:30 in New York on that date is 14:30 UTC.
        assert result.bars[0].opened_at.hour == 14
        assert result.bars[0].opened_at.tzinfo is not None

    def test_a_candle_whose_high_is_below_its_low_is_rejected(self) -> None:
        data = csv_bytes("2026-03-02 14:30,100,95,99,97,1000\n")
        result = parse_candles(data, mapping=MAPPING, timezone="UTC")

        # Repairing this would invent a price. It is a mapping error or a corrupt export.
        assert result.accepted == 0
        assert len(result.rejected) == 1
        assert "high" in result.rejected[0].reason.lower()

    def test_a_close_outside_the_range_is_rejected(self) -> None:
        data = csv_bytes("2026-03-02 14:30,100,110,99,150,1000\n")
        result = parse_candles(data, mapping=MAPPING, timezone="UTC")

        assert result.accepted == 0
        assert len(result.rejected) == 1

    def test_an_unreadable_timestamp_is_rejected_with_its_row_number(self) -> None:
        data = csv_bytes("not a date,100,110,99,105,1000\n2026-03-02 14:30,100,110,99,105,1000\n")
        result = parse_candles(data, mapping=MAPPING, timezone="UTC")

        assert result.accepted == 1
        assert result.rejected[0].row_number == 1
        assert "timestamp" in result.rejected[0].reason.lower()

    def test_a_missing_or_zero_price_is_rejected(self) -> None:
        data = csv_bytes("2026-03-02 14:30,,110,99,105,1000\n2026-03-02 15:30,0,110,99,105,1000\n")
        result = parse_candles(data, mapping=MAPPING, timezone="UTC")

        assert result.accepted == 0
        assert len(result.rejected) == 2

    def test_a_duplicate_timestamp_is_rejected_rather_than_overwriting(self) -> None:
        data = csv_bytes(
            "2026-03-02 14:30,100,110,99,105,1000\n2026-03-02 14:30,101,111,98,106,1200\n"
        )
        result = parse_candles(data, mapping=MAPPING, timezone="UTC")

        # Two candles for the same instant cannot both be right; the second is reported, not
        # silently preferred.
        assert result.accepted == 1
        assert len(result.rejected) == 1
        assert "duplicate" in result.rejected[0].reason.lower()

    def test_a_missing_volume_column_is_allowed(self) -> None:
        header = "Date,Open,High,Low,Close\n"
        data = csv_bytes("2026-03-02 14:30,100,110,99,105\n", header=header)
        mapping = {k: v for k, v in MAPPING.items() if k != "volume"}
        result = parse_candles(data, mapping=mapping, timezone="UTC")

        assert result.accepted == 1
        assert result.bars[0].volume == Decimal(0)

    def test_an_unmapped_required_field_stops_the_import(self) -> None:
        data = csv_bytes("2026-03-02 14:30,100,110,99,105,1000\n")
        with pytest.raises(UnprocessableStateError) as error:
            parse_candles(data, mapping={"timestamp": "Date"}, timezone="UTC")

        assert "open" in str(error.value)

    def test_prices_keep_full_precision(self) -> None:
        data = csv_bytes("2026-03-02 14:30,1.084215,1.084998,1.083771,1.084550,0\n")
        result = parse_candles(data, mapping=MAPPING, timezone="UTC")

        # A five-decimal FX quote must survive intact; rounding it here would change every level
        # a report later measures.
        assert result.bars[0].close == Decimal("1.084550")


class TestSummary:
    def test_the_summary_reports_what_was_kept_and_what_was_not(self) -> None:
        data = csv_bytes(
            "2026-03-02 14:30,100,110,99,105,1000\n"
            "bad,1,1,1,1,1\n"
            "2026-03-02 15:30,105,112,104,108,900\n"
        )
        result = parse_candles(data, mapping=MAPPING, timezone="UTC")
        summary = summarise(result, Timeframe.H1)

        assert summary["total_rows"] == 3
        assert summary["accepted"] == 2
        assert summary["rejected"] == 1
        assert summary["first_bar_at"] < summary["last_bar_at"]
        assert len(summary["rejected_rows"]) == 1

    def test_an_empty_result_reports_no_range_rather_than_a_fake_one(self) -> None:
        data = csv_bytes("bad,1,1,1,1,1\n")
        summary = summarise(parse_candles(data, mapping=MAPPING, timezone="UTC"), Timeframe.D1)

        assert summary["accepted"] == 0
        assert summary["first_bar_at"] is None
        assert summary["last_bar_at"] is None


class TestImportEndpoint:
    """The round trip that matters: a CSV goes in, and reports can read it back."""

    CSV = (
        b"Date,Open,High,Low,Close,Volume\n"
        b"2026-03-02 14:30,100,110,99,105,1000\n"
        b"2026-03-02 15:30,105,112,104,108,900\n"
        b"2026-03-03 14:30,108,115,107,112,1100\n"
        b"2026-03-03 15:30,112,118,111,116,1200\n"
    )

    MAPPING = (
        '{"timestamp":"Date","open":"Open","high":"High","low":"Low",'
        '"close":"Close","volume":"Volume"}'
    )

    async def _instrument(self, alice) -> dict:  # type: ignore[no-untyped-def]
        response = await alice.post(
            "/api/v1/instruments",
            json={
                "symbol": "ESX",
                "name": "Test Index Future",
                "asset_type": "futures",
                "currency": "USD",
                "tick_size": "0.25",
                "contract_multiplier": "50",
                "lot_size": "1",
            },
        )
        assert response.status_code in (200, 201), response.text
        return response.json()["data"]

    async def test_inspect_suggests_a_mapping_from_the_header(self, alice) -> None:  # type: ignore[no-untyped-def]
        response = await alice.post(
            "/api/v1/market-data/import/inspect",
            files={"file": ("candles.csv", self.CSV, "text/csv")},
        )
        assert response.status_code == 200, response.text

        data = response.json()["data"]
        assert data["suggested_mapping"]["open"] == "Open"
        assert data["total_rows"] == 4

    async def test_a_dry_run_reports_without_storing_anything(self, alice) -> None:  # type: ignore[no-untyped-def]
        instrument = await self._instrument(alice)
        response = await alice.post(
            "/api/v1/market-data/import",
            files={"file": ("candles.csv", self.CSV, "text/csv")},
            data={
                "instrument_id": instrument["id"],
                "timeframe": "1h",
                "column_mapping": self.MAPPING,
                "source_timezone": "UTC",
                "dry_run": "true",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["accepted"] == 4
        assert response.json()["data"]["stored"] == 0

        # Nothing was written. With no stored series the candles endpoint has nothing to serve,
        # which it reports as an error rather than as an empty-but-valid series.
        candles = await alice.get(
            "/api/v1/market-data/candles",
            params={"instrument_id": instrument["id"], "timeframe": "1h"},
        )
        if candles.status_code == 200:
            assert candles.json()["data"]["candles"] == []
        else:
            assert candles.status_code in (404, 409, 422), candles.text

    async def test_imported_candles_are_stored_and_readable(self, alice) -> None:  # type: ignore[no-untyped-def]
        instrument = await self._instrument(alice)
        response = await alice.post(
            "/api/v1/market-data/import",
            files={"file": ("candles.csv", self.CSV, "text/csv")},
            data={
                "instrument_id": instrument["id"],
                "timeframe": "1h",
                "column_mapping": self.MAPPING,
                "source_timezone": "UTC",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["stored"] == 4

        candles = await alice.get(
            "/api/v1/market-data/candles",
            params={"instrument_id": instrument["id"], "timeframe": "1h"},
        )
        stored = candles.json()["data"]["candles"]
        assert len(stored) == 4
        # Prices survive the round trip exactly. Compared as Decimals: the API sends decimal
        # strings and "100" and "100.00" are the same price, so asserting the text would be
        # asserting a formatting choice rather than correctness.
        assert Decimal(stored[0]["open"]) == Decimal("100")
        assert Decimal(stored[-1]["close"]) == Decimal("116")

    async def test_re_importing_the_same_file_does_not_duplicate_candles(self, alice) -> None:  # type: ignore[no-untyped-def]
        instrument = await self._instrument(alice)
        payload = {
            "instrument_id": instrument["id"],
            "timeframe": "1h",
            "column_mapping": self.MAPPING,
            "source_timezone": "UTC",
        }
        summaries = []
        for _ in range(2):
            response = await alice.post(
                "/api/v1/market-data/import",
                files={"file": ("candles.csv", self.CSV, "text/csv")},
                data=payload,
            )
            assert response.status_code == 200, response.text
            summaries.append(response.json()["data"])

        candles = await alice.get(
            "/api/v1/market-data/candles",
            params={"instrument_id": instrument["id"], "timeframe": "1h"},
        )
        # The unique key is (source, instrument, timeframe, opened_at); a second pass tops up
        # rather than doubling the series.
        assert len(candles.json()["data"]["candles"]) == 4

        # And the second pass must say so. Reporting the parsed count as "stored" would tell a
        # user who re-uploaded an overlapping export that it wrote four new candles, when it
        # wrote none.
        assert (summaries[0]["stored"], summaries[0]["already_stored"]) == (4, 0)
        assert (summaries[1]["stored"], summaries[1]["already_stored"]) == (0, 4)

    async def test_a_report_can_run_on_imported_candles(self, alice) -> None:  # type: ignore[no-untyped-def]
        """The whole point: real candles in, a real statistic out."""
        instrument = await self._instrument(alice)
        await alice.post(
            "/api/v1/market-data/import",
            files={"file": ("candles.csv", self.CSV, "text/csv")},
            data={
                "instrument_id": instrument["id"],
                "timeframe": "1h",
                "column_mapping": self.MAPPING,
                "source_timezone": "UTC",
            },
        )

        response = await alice.get(
            "/api/v1/reports/previous_day_levels",
            params={
                "instrument_id": instrument["id"],
                "timeframe": "1h",
                "session_timezone": "UTC",
            },
        )
        assert response.status_code == 200, response.text

        data = response.json()["data"]
        # Day two opens at 108 and runs to 118, taking day one's high of 112 and not its low of 99.
        assert data["total_sessions"] == 1
        assert data["sessions"][0]["outcome"] == "broke_up_only"
        assert data["source"]["name"] == "Imported candles"

    async def test_a_bad_mapping_is_refused_rather_than_stored(self, alice) -> None:  # type: ignore[no-untyped-def]
        instrument = await self._instrument(alice)
        response = await alice.post(
            "/api/v1/market-data/import",
            files={"file": ("candles.csv", self.CSV, "text/csv")},
            data={
                "instrument_id": instrument["id"],
                "timeframe": "1h",
                # High and low swapped: every candle becomes impossible.
                "column_mapping": (
                    '{"timestamp":"Date","open":"Open","high":"Low","low":"High",'
                    '"close":"Close","volume":"Volume"}'
                ),
                "source_timezone": "UTC",
            },
        )
        assert response.status_code == 200, response.text

        data = response.json()["data"]
        assert data["accepted"] == 0
        assert data["rejected"] == 4
        assert data["stored"] == 0
