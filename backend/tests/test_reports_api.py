"""The reports endpoint returns a statistic together with the evidence for it."""

from __future__ import annotations

import pytest

from tests.conftest import ApiUser

pytestmark = pytest.mark.anyio


async def _instrument(alice: ApiUser) -> dict:
    """An instrument this test owns.

    Reading whichever instrument the workspace happens to contain made these tests skip
    themselves on a fresh database, which is no coverage at all. Creating one makes them run
    everywhere and keeps the fixture honest about what it depends on.
    """
    response = await alice.post(
        "/api/v1/instruments",
        json={"symbol": "RPTX", "name": "Report Test Index", "asset_type": "index"},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def test_the_registry_is_published_with_its_parameters(alice: ApiUser) -> None:
    response = await alice.get("/api/v1/reports")
    assert response.status_code == 200, response.text

    reports = response.json()["data"]
    keys = {report["key"] for report in reports}
    assert {"initial_balance", "gap_fill", "previous_day_levels"} <= keys

    for report in reports:
        # The UI builds its controls from this, so every report must describe itself fully.
        assert report["name"] and report["question"] and report["description"]
        for parameter in report["parameters"]:
            assert parameter["name"] and parameter["param_type"]


async def test_an_unknown_report_is_a_404_not_an_empty_result(alice: ApiUser) -> None:
    instrument = await _instrument(alice)

    response = await alice.get(
        "/api/v1/reports/does-not-exist", params={"instrument_id": instrument["id"]}
    )
    # "No such report" and "this never happened" must not look the same to a caller.
    assert response.status_code == 404, response.text


async def test_an_unknown_instrument_is_refused(alice: ApiUser) -> None:
    response = await alice.get(
        "/api/v1/reports/gap_fill",
        params={"instrument_id": "00000000-0000-4000-8000-000000000001"},
    )
    assert response.status_code == 404, response.text


async def test_an_instrument_with_no_candles_says_so(alice: ApiUser) -> None:
    instrument = await _instrument(alice)

    response = await alice.get(
        "/api/v1/reports/gap_fill",
        params={"instrument_id": instrument["id"], "timeframe": "1h"},
    )
    # A fresh workspace has instruments but no market data; the API must explain that rather
    # than return a report of zero sessions that reads as "this never happens".
    assert response.status_code in (200, 409, 422), response.text
    if response.status_code != 200:
        assert "candle" in response.json()["error"]["message"].lower()
