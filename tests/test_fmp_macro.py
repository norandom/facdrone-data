# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""FMP macro + news transport: recorded payload → fetch → REAL normalizer (R2.2).

Proves the payload→fetch→normalize contract end-to-end with NO network for all
six datasets ``FmpMacroClient`` serves. Each test feeds a recorded FMP payload
through an INJECTED transport, calls ``FmpMacroClient.fetch``, then feeds the raw
frame into the matching REAL normalizer and asserts the normalizer returns
exactly the target table's columns — covering the load-bearing raw shapes: the
``date``→(``date``,``time``) split, the stamped indicator ``name``, the per-day
``date`` stamp on the dateless sector snapshot, the stamped ETF snapshot ``date``,
and the verbatim ``publishedDate``/``averageChange`` field names.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from facdrone_data.acquisition.families.normalizers.macro import (
    EconomicCalendarNormalizer,
    EconomicIndicatorNormalizer,
    EtfHoldingsNormalizer,
    SectorPerformanceNormalizer,
    TreasuryRatesNormalizer,
)
from facdrone_data.acquisition.families.normalizers.news import NewsNormalizer
from facdrone_data.acquisition.schema import (
    FMP_ECONOMIC_CALENDAR,
    FMP_ECONOMIC_INDICATORS,
    FMP_ETF_HOLDINGS,
    FMP_NEWS,
    FMP_SECTOR_PERFORMANCE,
    FMP_TREASURY_RATES,
)
from facdrone_data.providers.fmp_macro import FmpMacroClient, live_fmp_macro
from facdrone_data.testing import API_KEY
from facdrone_data_types.enums import AssetClass
from facdrone_data_types.ports import FetchRequest
from facdrone_data_types.symbols import CanonicalSymbol, Provider

AS_OF = datetime(2024, 6, 1, 12, 0)
# An ETF / news-bearing equity symbol — provider_symbol is the FMP symbol; the
# canonical id is what the symbol-keyed normalizers (ETF holdings, news) store.
SYMBOL = CanonicalSymbol("SPY", AssetClass.EQUITIES_US, "USD")
FMP_SYMBOL = "SPY"


def _client(by_endpoint: Mapping[str, Any]) -> FmpMacroClient:
    """A client whose transport routes by endpoint suffix to a recorded payload."""

    def _transport(url: str, params: Mapping[str, str]) -> Any:
        del params
        for endpoint, payload in by_endpoint.items():
            if url.endswith(endpoint):
                return payload
        raise AssertionError(f"unexpected endpoint: {url}")

    return FmpMacroClient(transport=_transport, api_key=API_KEY)


def _req(dataset: str, *, provider_symbol: str = FMP_SYMBOL) -> FetchRequest:
    return FetchRequest(
        provider_symbol=provider_symbol,
        dataset=dataset,
        start=date(2024, 1, 1),
        end=date(2024, 3, 1),
    )


def _table_columns(table: Any) -> list[str]:
    return [c.name for c in table.columns]


# ── economic calendar (90-day chunks; date→date/time split) ──────────────────
def test_economic_calendar_payload_fetches_and_normalizes_to_table_columns() -> None:
    payload = [
        {
            "date": "2024-01-31 19:00:00",
            "country": "US",
            "event": "FOMC Rate Decision",
            "actual": 5.5,
            "estimate": 5.5,
            "previous": 5.5,
            "impact": "High",
        }
    ]
    raw = _client({"/economic-calendar": payload}).fetch(_req("fmp_economic_calendar"))
    # The raw frame carries the verbatim FMP ``date`` the normalizer later splits.
    assert "date" in raw.columns
    assert raw["date"].iloc[0] == "2024-01-31 19:00:00"

    out = EconomicCalendarNormalizer().normalize(raw, symbol=None, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_ECONOMIC_CALENDAR)
    assert out["date"].iloc[0] == date(2024, 1, 31)
    assert out["time"].iloc[0] == "19:00:00"  # the HH:MM:SS tail
    assert out["event"].iloc[0] == "FOMC Rate Decision"


# ── one named economic-indicator series (name stamped by fetch) ──────────────
def test_economic_indicator_payload_fetches_and_normalizes_to_table_columns() -> None:
    # The named-series payload carries NO ``name`` — fetch must stamp the requested
    # indicator name (``req.provider_symbol``) so the normalizer reads ``raw["name"]``.
    payload = [
        {"date": "2024-01-01", "value": 309.7},
        {"date": "2024-02-01", "value": 310.3},
    ]
    raw = _client({"/economic-indicators": payload}).fetch(
        _req("fmp_economic_indicators", provider_symbol="CPI")
    )
    assert "name" in raw.columns
    assert (raw["name"] == "CPI").all()  # stamped by fetch

    out = EconomicIndicatorNormalizer().normalize(raw, symbol=None, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_ECONOMIC_INDICATORS)
    assert out["name"].iloc[0] == "CPI"
    assert out["date"].iloc[0] == date(2024, 1, 1)
    assert out["value"].iloc[0] == 309.7


# ── treasury yield curve ─────────────────────────────────────────────────────
def test_treasury_rates_payload_fetches_and_normalizes_to_table_columns() -> None:
    payload = [
        {
            "date": "2024-01-02",
            "month1": 5.5,
            "month2": 5.5,
            "month3": 5.4,
            "month6": 5.2,
            "year1": 4.8,
            "year2": 4.3,
            "year3": 4.1,
            "year5": 3.9,
            "year7": 3.9,
            "year10": 3.9,
            "year20": 4.2,
            "year30": 4.1,
        }
    ]
    raw = _client({"/treasury-rates": payload}).fetch(_req("fmp_treasury_rates"))
    assert "year10" in raw.columns  # maturity fields reach the normalizer verbatim

    out = TreasuryRatesNormalizer().normalize(raw, symbol=None, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_TREASURY_RATES)
    assert out["date"].iloc[0] == date(2024, 1, 2)
    assert out["year10"].iloc[0] == 3.9


# ── sector performance (per-day snapshot; date stamped by fetch) ─────────────
def test_sector_performance_payload_fetches_and_normalizes_to_table_columns() -> None:
    # The snapshot rows carry ``sector``/``averageChange`` but NO ``date`` — fetch
    # stamps the per-weekday snapshot date so the normalizer reads ``date``.
    payload = [
        {"sector": "Technology", "averageChange": 1.23},
        {"sector": "Energy", "averageChange": -0.45},
    ]
    raw = _client({"/sector-performance-snapshot": payload}).fetch(_req("fmp_sector_performance"))
    assert "date" in raw.columns  # stamped by fetch
    assert "averageChange" in raw.columns  # the field the normalizer renames

    out = SectorPerformanceNormalizer().normalize(raw, symbol=None, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_SECTOR_PERFORMANCE)
    assert set(out["sector"]) == {"Technology", "Energy"}
    tech = out[out["sector"] == "Technology"]
    assert tech["change_percentage"].iloc[0] == 1.23  # averageChange → change_percentage


# ── ETF holdings (current snapshot; symbol-keyed, date stamped) ──────────────
def test_etf_holdings_payload_fetches_and_normalizes_to_table_columns() -> None:
    # The holdings snapshot carries no per-row date — fetch stamps the snapshot date.
    payload = [
        {
            "asset": "AAPL",
            "sharesNumber": 1_000_000,
            "weightPercentage": 7.1,
            "marketValue": 195_000_000,
        }
    ]
    raw = _client({"/etf/holdings": payload}).fetch(_req("fmp_etf_holdings"))
    assert "date" in raw.columns  # stamped by fetch
    assert "sharesNumber" in raw.columns  # the field the normalizer coalesces

    out = EtfHoldingsNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_ETF_HOLDINGS)
    assert out["symbol"].iloc[0] == "SPY"  # stored as the canonical symbol
    assert out["asset"].iloc[0] == "AAPL"
    assert out["shares"].iloc[0] == 1_000_000
    assert out["date"].iloc[0] == date(2024, 3, 1)  # the snapshot date (req.end)


# ── stock news (90-day chunks; publishedDate verbatim) ───────────────────────
def test_news_payload_fetches_and_normalizes_to_table_columns() -> None:
    payload = [
        {
            "publishedDate": "2024-01-15 09:30:00",
            "title": "SPY hits a new high",
            "publisher": "Reuters",
        },
        # publisher absent → falls back to ``site``.
        {
            "publishedDate": "2024-01-16 14:00:00",
            "title": "Markets steady",
            "site": "marketwatch.com",
        },
    ]
    raw = _client({"/news/stock": payload}).fetch(_req("fmp_news"))
    assert "publishedDate" in raw.columns  # verbatim FMP field the normalizer reads

    out = NewsNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_NEWS)
    assert (out["symbol"] == "SPY").all()  # re-keyed to the canonical symbol
    fallback = out[out["title"] == "Markets steady"]
    assert fallback["publisher"].iloc[0] == "marketwatch.com"  # publisher ← site


def test_news_skips_records_missing_title_or_published_date() -> None:
    payload = [
        {"publishedDate": "2024-01-15 09:30:00", "title": "Kept"},
        {"publishedDate": "", "title": "No date — dropped"},
        {"publishedDate": "2024-01-17 10:00:00", "title": ""},  # no title — dropped
    ]
    raw = _client({"/news/stock": payload}).fetch(_req("fmp_news"))
    out = NewsNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_NEWS)
    assert list(out["title"]) == ["Kept"]  # only the complete record survives


# ── chunking: a >90-day range fans out into multiple windows ─────────────────
def test_calendar_range_is_chunked_into_ninety_day_windows() -> None:
    seen: list[tuple[str, str]] = []

    def _transport(url: str, params: Mapping[str, str]) -> Any:
        if url.endswith("/economic-calendar"):
            seen.append((params["from"], params["to"]))
            return []
        raise AssertionError(f"unexpected endpoint: {url}")

    client = FmpMacroClient(transport=_transport, api_key=API_KEY)
    req = FetchRequest(
        provider_symbol=FMP_SYMBOL,
        dataset="fmp_economic_calendar",
        start=date(2024, 1, 1),
        end=date(2024, 6, 1),  # ~152 days → two 90-day windows
    )
    client.fetch(req)
    assert len(seen) == 2
    assert seen[0][0] == "2024-01-01"
    assert seen[1][1] == "2024-06-01"  # the final window ends at req.end


# ── adapter contract surface ─────────────────────────────────────────────────
def test_provider_and_datasets() -> None:
    client = FmpMacroClient(transport=lambda url, params: [], api_key=API_KEY)
    assert client.provider is Provider.FMP
    assert client.datasets() == (
        "fmp_economic_calendar",
        "fmp_economic_indicators",
        "fmp_treasury_rates",
        "fmp_sector_performance",
        "fmp_etf_holdings",
        "fmp_news",
    )


def test_unserved_dataset_returns_empty_frame() -> None:
    client = FmpMacroClient(transport=lambda url, params: [], api_key=API_KEY)
    assert client.fetch(_req("daily_prices")).empty


def test_empty_payload_normalizes_to_table_columns() -> None:
    raw = _client({"/treasury-rates": []}).fetch(_req("fmp_treasury_rates"))
    assert raw.empty
    out = TreasuryRatesNormalizer().normalize(raw, symbol=None, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_TREASURY_RATES)
    assert out.empty


def test_empty_indicator_payload_stays_empty_and_normalizes() -> None:
    raw = _client({"/economic-indicators": []}).fetch(
        _req("fmp_economic_indicators", provider_symbol="CPI")
    )
    assert raw.empty  # stamping ``name`` on an empty frame keeps it empty
    out = EconomicIndicatorNormalizer().normalize(raw, symbol=None, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_ECONOMIC_INDICATORS)
    assert out.empty


def test_live_factory_builds_a_client_without_importing_requests() -> None:
    client = live_fmp_macro("secret")
    assert isinstance(client, FmpMacroClient)
    assert client.api_key == "secret"
