"""CSV import pipeline tests.

The importer's job is to be forgiving about *format* and strict about *meaning*: it should parse
whatever a broker exports, but never invent a value it could not read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from tradeloom.core.enums import ImportStatus, OrderSide
from tradeloom.services.imports import parsing

D = Decimal


class TestValueParsing:
    def test_currency_symbols_and_separators_are_stripped(self) -> None:
        assert parsing.parse_decimal("$2,500.75") == D("2500.75")
        assert parsing.parse_decimal("1 234.50") == D("1234.50")

    def test_european_decimal_format(self) -> None:
        assert parsing.parse_decimal("1.234,56") == D("1234.56")

    def test_unparseable_value_is_none_not_zero(self) -> None:
        # Returning 0 here would silently import a free trade.
        assert parsing.parse_decimal("n/a") is None
        assert parsing.parse_decimal("") is None

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Buy", OrderSide.BUY),
            ("SLD", OrderSide.SELL),
            ("B", OrderSide.BUY),
            ("sell to close", OrderSide.SELL),
            ("bought", OrderSide.BUY),
            ("SHORT", OrderSide.SELL),
        ],
    )
    def test_direction_synonyms(self, text: str, expected: OrderSide) -> None:
        assert parsing.parse_side(text) is expected

    def test_unknown_direction_is_none(self) -> None:
        assert parsing.parse_side("transfer") is None

    def test_symbol_normalisation(self) -> None:
        assert parsing.normalize_symbol(" /es ") == "ES"
        assert parsing.normalize_symbol("AAPL.US") == "AAPL"
        assert parsing.normalize_symbol("btc-usd") == "BTC-USD"

    def test_naive_timestamps_use_the_source_timezone(self) -> None:
        # 09:30 in New York is 13:30 UTC on this date, not 09:30 UTC.
        parsed = parsing.parse_timestamp("2024-05-06 09:30:00", "America/New_York")
        assert parsed == datetime(2024, 5, 6, 13, 30, tzinfo=UTC)

    def test_us_and_european_date_orders(self) -> None:
        assert parsing.parse_timestamp("05/06/2024 14:30:00", "UTC").day in (5, 6)
        assert parsing.parse_timestamp("2024-05-06T14:30:00Z", "UTC") == datetime(
            2024, 5, 6, 14, 30, tzinfo=UTC
        )

    def test_epoch_timestamps(self) -> None:
        assert parsing.parse_timestamp("1714999800", "UTC") == datetime(
            2024, 5, 6, 12, 50, tzinfo=UTC
        )

    def test_garbage_timestamp_is_none(self) -> None:
        assert parsing.parse_timestamp("not a date", "UTC") is None


class TestInspection:
    CSV = (
        "Date/Time,Symbol,Action,Qty,Price,Commission,Exec ID\n"
        "2024-05-06 09:30:00,NVLX,BUY,100,118.40,1.00,E-1\n"
        "2024-05-06 11:15:00,NVLX,SELL,100,121.10,1.00,E-2\n"
    )

    def test_headers_and_delimiter_are_detected(self) -> None:
        inspection = parsing.inspect_csv(self.CSV.encode())
        assert inspection.delimiter == ","
        assert inspection.total_rows == 2
        assert "Symbol" in inspection.headers

    def test_fields_are_auto_mapped(self) -> None:
        inspection = parsing.inspect_csv(self.CSV.encode())
        mapping = inspection.suggested_mapping
        assert mapping["timestamp"] == "Date/Time"
        assert mapping["symbol"] == "Symbol"
        assert mapping["side"] == "Action"
        assert mapping["quantity"] == "Qty"
        assert mapping["price"] == "Price"

    def test_semicolon_delimited_files(self) -> None:
        csv = "time;pair;side;amount;rate\n2024-05-06 10:00:00;BTCX;buy;0.5;38000\n"
        inspection = parsing.inspect_csv(csv.encode())
        assert inspection.delimiter == ";"
        assert inspection.total_rows == 1

    def test_bom_prefixed_utf8(self) -> None:
        inspection = parsing.inspect_csv(b"\xef\xbb\xbf" + self.CSV.encode())
        assert inspection.headers[0] == "Date/Time"

    def test_empty_file_is_rejected(self) -> None:
        with pytest.raises(parsing.CsvParseError):
            parsing.inspect_csv(b"")

    def test_ragged_rows_are_padded_not_dropped(self) -> None:
        csv = "a,b,c\n1,2\n3,4,5\n"
        rows = parsing.iter_rows(csv.encode(), ",")
        assert len(rows) == 2
        assert rows[0] == {"a": "1", "b": "2", "c": ""}


@pytest.mark.anyio
class TestPipeline:
    async def _setup(self, client, alice):  # type: ignore[no-untyped-def]
        from tests.conftest import create_account

        return await create_account(alice, "Import target")

    async def _upload(self, alice, account_id: str, csv: str, filename: str = "fills.csv"):  # type: ignore[no-untyped-def]
        response = await alice.post(
            "/api/v1/imports",
            files={"file": (filename, csv.encode(), "text/csv")},
            data={"account_id": account_id},
        )
        assert response.status_code == 201, response.text
        return response.json()["data"]

    async def test_full_import_creates_trades(self, client, alice) -> None:  # type: ignore[no-untyped-def]
        account = await self._setup(client, alice)
        csv = (
            "Time,Symbol,Side,Quantity,Price,Commission,Order ID\n"
            "2024-05-06 14:30:00,NVLX,Buy,100,100.00,1.00,A-1\n"
            "2024-05-06 16:00:00,NVLX,Sell,100,110.00,1.00,A-2\n"
        )
        record = await self._upload(alice, account["id"], csv)
        assert record["status"] == ImportStatus.MAPPING.value
        assert record["total_rows"] == 2

        mapping = record["inspection"]["suggested_mapping"]
        response = await alice.request(
            "PUT",
            f"/api/v1/imports/{record['id']}/mapping",
            json={"column_mapping": mapping, "options": {"timezone": "UTC"}},
        )
        assert response.status_code == 200, response.text

        response = await alice.post(f"/api/v1/imports/{record['id']}/validate")
        assert response.status_code == 200, response.text
        validated = response.json()["data"]
        assert validated["valid_rows"] == 2
        assert validated["invalid_rows"] == 0

        response = await alice.post(f"/api/v1/imports/{record['id']}/commit")
        assert response.status_code == 200, response.text
        committed = response.json()["data"]
        assert committed["status"] == ImportStatus.COMPLETED.value
        assert committed["created_trade_count"] == 1

        trades = (await alice.get("/api/v1/trades")).json()["data"]
        assert len(trades) == 1
        assert trades[0]["net_pnl"] == "998"  # 1000 gross - 2 commission

    async def test_invalid_rows_are_reported_not_discarded(self, client, alice) -> None:  # type: ignore[no-untyped-def]
        account = await self._setup(client, alice)
        csv = (
            "Time,Symbol,Side,Quantity,Price,Order ID\n"
            "2024-05-06 14:30:00,NVLX,Buy,100,100.00,B-1\n"
            "not-a-date,NVLX,Buy,50,100.00,B-2\n"
            "2024-05-06 15:00:00,,Buy,50,100.00,B-3\n"
            "2024-05-06 15:30:00,NVLX,teleport,50,100.00,B-4\n"
            "2024-05-06 16:00:00,NVLX,Sell,100,-5,B-5\n"
        )
        record = await self._upload(alice, account["id"], csv)
        await alice.request(
            "PUT",
            f"/api/v1/imports/{record['id']}/mapping",
            json={
                "column_mapping": record["inspection"]["suggested_mapping"],
                "options": {"timezone": "UTC"},
            },
        )
        validated = (await alice.post(f"/api/v1/imports/{record['id']}/validate")).json()["data"]
        assert validated["total_rows"] == 5
        assert validated["invalid_rows"] == 4

        preview = (await alice.get(f"/api/v1/imports/{record['id']}/preview")).json()["data"]
        # Every bad row keeps its raw values and a field-level reason.
        assert len(preview["invalid_rows"]) == 4
        codes = {error["code"] for row in preview["invalid_rows"] for error in row["errors"]}
        assert "unparseable_timestamp" in codes
        assert "missing_symbol" in codes
        assert "unknown_side" in codes
        assert "non_positive_price" in codes

    async def test_duplicate_execution_ids_are_flagged(self, client, alice) -> None:  # type: ignore[no-untyped-def]
        account = await self._setup(client, alice)
        csv = (
            "Time,Symbol,Side,Quantity,Price,Order ID\n"
            "2024-05-06 14:30:00,NVLX,Buy,100,100.00,DUP-1\n"
            "2024-05-06 14:31:00,NVLX,Buy,100,100.00,DUP-1\n"
        )
        record = await self._upload(alice, account["id"], csv)
        await alice.request(
            "PUT",
            f"/api/v1/imports/{record['id']}/mapping",
            json={
                "column_mapping": record["inspection"]["suggested_mapping"],
                "options": {"timezone": "UTC"},
            },
        )
        validated = (await alice.post(f"/api/v1/imports/{record['id']}/validate")).json()["data"]
        assert validated["duplicate_rows"] == 1
        assert validated["valid_rows"] == 1

    async def test_revert_removes_exactly_what_it_created(self, client, alice) -> None:  # type: ignore[no-untyped-def]
        account = await self._setup(client, alice)
        csv = (
            "Time,Symbol,Side,Quantity,Price,Order ID\n"
            "2024-05-06 14:30:00,NVLX,Buy,100,100.00,R-1\n"
            "2024-05-06 16:00:00,NVLX,Sell,100,110.00,R-2\n"
        )
        record = await self._upload(alice, account["id"], csv)
        await alice.request(
            "PUT",
            f"/api/v1/imports/{record['id']}/mapping",
            json={
                "column_mapping": record["inspection"]["suggested_mapping"],
                "options": {"timezone": "UTC"},
            },
        )
        await alice.post(f"/api/v1/imports/{record['id']}/validate")
        await alice.post(f"/api/v1/imports/{record['id']}/commit")

        assert len((await alice.get("/api/v1/trades")).json()["data"]) == 1

        response = await alice.post(f"/api/v1/imports/{record['id']}/revert")
        assert response.status_code == 200, response.text
        assert response.json()["data"]["status"] == ImportStatus.REVERTED.value
        assert (await alice.get("/api/v1/trades")).json()["data"] == []

        # A second revert is refused rather than silently deleting more.
        assert (await alice.post(f"/api/v1/imports/{record['id']}/revert")).status_code == 409

    async def test_timezone_conversion_on_import(self, client, alice) -> None:  # type: ignore[no-untyped-def]
        account = await self._setup(client, alice)
        csv = (
            "Time,Symbol,Side,Quantity,Price,Order ID\n"
            "2024-05-06 09:30:00,NVLX,Buy,10,100.00,TZ-1\n"
            "2024-05-06 10:30:00,NVLX,Sell,10,101.00,TZ-2\n"
        )
        record = await self._upload(alice, account["id"], csv)
        await alice.request(
            "PUT",
            f"/api/v1/imports/{record['id']}/mapping",
            json={
                "column_mapping": record["inspection"]["suggested_mapping"],
                "options": {"timezone": "America/New_York"},
            },
        )
        await alice.post(f"/api/v1/imports/{record['id']}/validate")
        await alice.post(f"/api/v1/imports/{record['id']}/commit")

        trade = (await alice.get("/api/v1/trades")).json()["data"][0]
        # 09:30 New York == 13:30 UTC.
        assert trade["entry_timestamp"].startswith("2024-05-06T13:30")
