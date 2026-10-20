# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The policy rows' retry schedules, through the adapters (facdrone-data-manager 3.4; R6.4).

The Dukascopy hour fetch waits as it did before the table; FMP retries a throttle answer on
its row's schedule and then fails open with the family's summary line. Every transport is a
fake; the summary-line test runs on the embedded PostgreSQL.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast
from unittest.mock import Mock, patch

import pytest
import requests
from sqlalchemy import Engine

from download_support import KNOWN_AT, PORTS, WINDOW, SyntheticClient, two_instrument_universe
from facdrone_data.acquisition.families.credentials import ProviderCredentials
from facdrone_data.acquisition.families.refresher import passthrough_gate
from facdrone_data.cli.render import download_lines
from facdrone_data.providers.dukascopy_source import live_dukascopy
from facdrone_data.providers.fmp_endpoints import FmpPriceClient, live_http_json
from facdrone_data.quality.ensemble import EnsembleValidator
from facdrone_data.runner import DataRootDeps, DownloadCommand, compose_download_runner
from facdrone_data_types.ports import MarketDataClient
from facdrone_data_types.symbols import Provider

_KEY = "synthetic-fmp-key"  # noqa: S105 — a test fixture, not a real key
_HOUR = datetime(2026, 10, 7, 10)


# ── the Dukascopy schedule, through the adapter ──────────────────────────────
@pytest.mark.parametrize(
    ("side_effect", "attempts", "waits"),
    [
        ([Mock(status_code=429)] * 4, 4, [1, 2, 4]),
        ([Mock(status_code=503)] * 4, 4, [1, 2, 4]),
        (requests.ConnectionError("down"), 3, [1, 2]),
        ([Mock(status_code=500)] * 3, 3, []),
        ([Mock(status_code=404)], 1, []),
        # The attempt counter runs across classes: a fault after two throttles is the third
        # attempt, which the fault schedule no longer retries.
        ([Mock(status_code=429), Mock(status_code=429), requests.ConnectionError("x")], 3, [1, 2]),
    ],
)
def test_the_dukascopy_adapter_waits_as_before(
    side_effect: object, attempts: int, waits: list[float]
) -> None:
    sleeps: list[float] = []
    with patch("requests.Session") as session:
        session.return_value.get.side_effect = side_effect
        assert live_dukascopy(sleep=sleeps.append)("EURUSD", _HOUR) is None
        assert session.return_value.get.call_count == attempts
    assert sleeps == waits


# ── FMP: a throttle answer is retried, then the request fails open ───────────
def _answer(status: int, payload: object = None) -> Mock:
    response = Mock(status_code=status)
    response.json.return_value = payload
    if status >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(
            f"{status} Client Error for url: https://example.test/x?apikey={_KEY}"
        )
    return response


def test_fmp_retries_a_throttle_and_returns_the_answer_that_follows() -> None:
    sleeps: list[float] = []
    with patch("requests.Session") as session:
        session.return_value.get.side_effect = [_answer(429), _answer(503), _answer(200, [{}])]
        assert live_http_json(sleep=sleeps.append)("https://example.test/x", {}) == [{}]
        assert session.return_value.get.call_count == 3
    assert sleeps == [1, 2]


def test_fmp_gives_up_on_a_standing_throttle_after_its_rows_waits() -> None:
    sleeps: list[float] = []
    with patch("requests.Session") as session:
        session.return_value.get.side_effect = [_answer(429)] * 4
        with pytest.raises(requests.HTTPError, match="429"):
            live_http_json(sleep=sleeps.append)("https://example.test/x", {})
        assert session.return_value.get.call_count == 4
    assert sleeps == [1, 2, 4]


@pytest.mark.parametrize("failure", [_answer(500), requests.ConnectionError("down")])
def test_fmp_does_not_retry_a_fault_or_another_status(failure: object) -> None:
    sleeps: list[float] = []
    with patch("requests.Session") as session:
        session.return_value.get.side_effect = [failure]
        with pytest.raises(requests.RequestException):
            live_http_json(sleep=sleeps.append)("https://example.test/x", {})
        assert session.return_value.get.call_count == 1
    assert sleeps == []


def test_a_standing_fmp_throttle_yields_the_empty_frame_and_the_familys_summary_line(
    prices_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    sleeps: list[float] = []
    session = Mock()
    session.get.side_effect = lambda *_a, **_k: _answer(429)
    with patch("requests.Session", return_value=session):
        transport = live_http_json(sleep=sleeps.append)
        fmp = FmpPriceClient(transport=transport, api_key=_KEY)
        universe = two_instrument_universe()
        clients = {Provider.YFINANCE: SyntheticClient(Provider.YFINANCE), Provider.FMP: fmp}
        deps = DataRootDeps(
            engine=prices_engine,
            credentials=ProviderCredentials(fmp_api_key=_KEY, fred_api_key=None),
            requirement=lambda: universe.requirement(prices_engine, KNOWN_AT),
            clients=cast("dict[Provider, MarketDataClient]", clients),
            validator=EnsembleValidator(),
            installed=lambda name: True,
        )
        runner = compose_download_runner(
            deps, family_selector=lambda source: PORTS, gate=passthrough_gate
        )
        report = runner(DownloadCommand("all", *WINDOW))
    requests_made = session.get.call_count
    assert requests_made > 0 and requests_made % 4 == 0  # four attempts per request
    assert sleeps == [1, 2, 4] * (requests_made // 4)
    lines = download_lines(report)
    assert (
        "keyed_prices BBB: fetched=0 skipped=0 inserted=0 updated=0 quarantined=0 "
        "provider_outcome=provider_unavailable cursor=-"
    ) in lines
    assert any(line.startswith("free_prices AAA: fetched=5 ") for line in lines)
    assert _KEY not in caplog.text and "FMP request failed" in caplog.text
