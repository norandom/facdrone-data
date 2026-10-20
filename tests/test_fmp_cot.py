# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""FMP COT transport: recorded payload → fetch → REAL normalizer (R2.2).

Proves the payload→fetch→normalize contract end-to-end with NO network. Each
test feeds a recorded FMP COT payload through an INJECTED transport, calls
``FmpCotClient.fetch``, then feeds the raw frame into the matching REAL
normalizer and asserts the normalizer returns exactly the target table's
columns — including the misspelled ``netPostion`` (sic) round-trip and the
operand zero-fill / missing-key semantics."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from facdrone_data.acquisition.families.normalizers.cot import (
    CotAnalysisNormalizer,
    CotNormalizer,
)
from facdrone_data.acquisition.schema import FMP_COT, FMP_COT_ANALYSIS
from facdrone_data.providers.fmp_cot import FmpCotClient, live_fmp_cot
from facdrone_data.testing import API_KEY
from facdrone_data_types.enums import AssetClass
from facdrone_data_types.ports import FetchRequest
from facdrone_data_types.symbols import CanonicalSymbol, Provider

AS_OF = datetime(2024, 6, 1, 12, 0)
# Gold COT — provider_symbol is the FMP COT symbol; canonical is the YF symbol.
SYMBOL = CanonicalSymbol("GC=F", AssetClass.COMMODITIES, "USD")
FMP_SYMBOL = "GCUSD"


def _client(by_endpoint: Mapping[str, Any]) -> FmpCotClient:
    """A client whose transport routes by endpoint suffix to a recorded payload."""

    def _transport(url: str, params: Mapping[str, str]) -> Any:
        del params
        for endpoint, payload in by_endpoint.items():
            if url.endswith(endpoint):
                return payload
        raise AssertionError(f"unexpected endpoint: {url}")

    return FmpCotClient(transport=_transport, api_key=API_KEY)


def _req(dataset: str) -> FetchRequest:
    return FetchRequest(
        provider_symbol=FMP_SYMBOL,
        dataset=dataset,
        start=date(2023, 1, 1),
        end=date(2024, 1, 1),
    )


def _table_columns(table: Any) -> list[str]:
    return [c.name for c in table.columns]


# ── COT report ───────────────────────────────────────────────────────────────
def test_cot_report_payload_fetches_and_normalizes_to_table_columns() -> None:
    payload = [
        {
            "date": "2023-12-26",
            "short_name": "GOLD",
            "sector": "METALS",
            "current_long_all": 250_000,
            "current_short_all": 90_000,
            "change_long_all": 1_500,
            "change_short_all": -800,
            "pct_long_all": 0.61,
            "pct_short_all": 0.22,
        }
    ]
    raw = _client({"/commitment-of-traders-report": payload}).fetch(_req("fmp_cot"))
    # The raw frame carries exactly the FMP field names the normalizer reads.
    assert "current_long_all" in raw.columns
    assert "current_short_all" in raw.columns

    out = CotNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_COT)
    assert out["symbol"].iloc[0] == "GC=F"  # stored as the canonical YF symbol
    # net_position is the subtraction of the (zero-filled) operands, not (l-s) or 0.
    assert out["net_position"].iloc[0] == 250_000 - 90_000
    assert out["current_long_all"].iloc[0] == 250_000


def test_cot_report_missing_operands_zero_fill() -> None:
    """An absent operand surfaces as NaN per row; the normalizer restores the
    missing-key semantics (``r.get(key, 0) or 0``) and stores it zero-filled."""
    payload = [
        # current_short_all absent → zero-filled; short_name/sector absent → "".
        {"date": "2023-12-19", "current_long_all": 100_000},
    ]
    raw = _client({"/commitment-of-traders-report": payload}).fetch(_req("fmp_cot"))
    out = CotNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_COT)
    assert out["current_short_all"].iloc[0] == 0  # zero-filled missing operand
    assert out["net_position"].iloc[0] == 100_000  # 100_000 - 0
    assert out["short_name"].iloc[0] == ""  # missing-key default
    assert out["sector"].iloc[0] == ""


# ── COT analysis ─────────────────────────────────────────────────────────────
def test_cot_analysis_payload_fetches_and_normalizes_to_table_columns() -> None:
    payload = [
        {
            "date": "2023-12-26T00:00:00",
            "currentLongMarketSituation": 64.2,
            "currentShortMarketSituation": 35.8,
            "netPostion": 160_000,  # FMP typo carried verbatim (sic)
            "changeInNetPosition": 2_300,
            "reversalTrend": False,
            "marketSentiment": "Bullish",
            "marketSituation": "Increasing",
        }
    ]
    raw = _client({"/commitment-of-traders-analysis": payload}).fetch(_req("fmp_cot_analysis"))
    # The misspelled FMP key must reach the normalizer verbatim.
    assert "netPostion" in raw.columns

    out = CotAnalysisNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_COT_ANALYSIS)
    assert out["symbol"].iloc[0] == "GC=F"
    assert out["net_position"].iloc[0] == 160_000  # read from netPostion (sic)
    assert out["change_short"].iloc[0] is None  # always None
    assert out["net_position_trend"].iloc[0] == "Bullish"  # cross-wired marketSentiment
    assert out["date"].iloc[0] == date(2023, 12, 26)  # date[:10] coerced


# ── adapter contract surface ─────────────────────────────────────────────────
def test_provider_and_datasets() -> None:
    client = FmpCotClient(transport=lambda url, params: [], api_key=API_KEY)
    assert client.provider is Provider.FMP
    assert client.datasets() == ("fmp_cot", "fmp_cot_analysis")


def test_unserved_dataset_returns_empty_frame() -> None:
    client = FmpCotClient(transport=lambda url, params: [], api_key=API_KEY)
    assert client.fetch(_req("daily_prices")).empty


def test_empty_payload_returns_empty_frame() -> None:
    raw = _client({"/commitment-of-traders-report": []}).fetch(_req("fmp_cot"))
    assert raw.empty
    # The normalizer is fail-safe on an empty fetch — still the table's columns.
    out = CotNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_COT)
    assert out.empty


def test_live_factory_builds_a_client_without_importing_requests() -> None:
    client = live_fmp_cot("secret")
    assert isinstance(client, FmpCotClient)
    assert client.api_key == "secret"
