# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""FMP fundamentals transport: recorded payload → fetch → REAL normalizer (R2.2).

Proves the payload→fetch→normalize contract end-to-end with NO network. Each test
feeds a recorded FMP payload through an INJECTED transport, calls
``FmpFundamentalsClient.fetch``, then feeds the raw frame into the matching REAL
normalizer and asserts the normalizer returns exactly the target table's columns
(and that the load-bearing key-metrics bvps join survives the round-trip)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from facdrone_data.acquisition.families.normalizers.analyst import AnalystEstimatesNormalizer
from facdrone_data.acquisition.families.normalizers.fundamentals import (
    BalanceSheetNormalizer,
    CashFlowNormalizer,
    EarningsCalendarNormalizer,
    IncomeStatementNormalizer,
    KeyMetricsNormalizer,
)
from facdrone_data.acquisition.schema import (
    FMP_ANALYST_ESTIMATES,
    FMP_BALANCE_SHEET,
    FMP_CASH_FLOW,
    FMP_EARNINGS_CALENDAR,
    FMP_INCOME_STATEMENT,
    FMP_KEY_METRICS,
)
from facdrone_data.providers.fmp_fundamentals import (
    FmpFundamentalsClient,
    live_fmp_fundamentals,
)
from facdrone_data.testing import API_KEY
from facdrone_data_types.enums import AssetClass
from facdrone_data_types.ports import FetchRequest
from facdrone_data_types.symbols import CanonicalSymbol, Provider

AS_OF = datetime(2024, 6, 1, 12, 0)
SYMBOL = CanonicalSymbol("AAPL", AssetClass.EQUITIES_US, "USD")


def _client(by_endpoint: Mapping[str, Any]) -> FmpFundamentalsClient:
    """A client whose transport routes by endpoint suffix to a recorded payload."""

    def _transport(url: str, params: Mapping[str, str]) -> Any:
        del params
        for endpoint, payload in by_endpoint.items():
            if url.endswith(endpoint):
                return payload
        raise AssertionError(f"unexpected endpoint: {url}")

    return FmpFundamentalsClient(transport=_transport, api_key=API_KEY)


def _req(dataset: str) -> FetchRequest:
    return FetchRequest(
        provider_symbol="AAPL",
        dataset=dataset,
        start=date(2020, 1, 1),
        end=date(2024, 1, 1),
    )


def _table_columns(table: Any) -> list[str]:
    return [c.name for c in table.columns]


# ── income statement ─────────────────────────────────────────────────────────
def test_income_statement_payload_fetches_and_normalizes_to_table_columns() -> None:
    payload = [
        {
            "date": "2023-09-30",
            "calendarYear": "2023",
            "period": "FY",
            "revenue": 383_285_000_000,
            "grossProfit": 169_148_000_000,
            "ebitda": 125_820_000_000,
            "operatingIncome": 114_301_000_000,
            "netIncome": 96_995_000_000,
            "eps": 6.16,
            "epsdiluted": 6.13,  # legacy lowercase alias
            "grossProfitRatio": 0.441,
            "operatingIncomeRatio": 0.298,
            "netIncomeRatio": 0.253,
            "filingDate": "2023-11-03",
        }
    ]
    raw = _client({"/income-statement": payload}).fetch(_req("fmp_income_statement"))
    # The raw frame carries exactly the FMP field names the normalizer reads.
    assert "calendarYear" in raw.columns
    assert "epsdiluted" in raw.columns

    out = IncomeStatementNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_INCOME_STATEMENT)
    assert out["symbol"].iloc[0] == "AAPL"
    assert out["fiscal_year"].iloc[0] == "2023"  # calendarYear fallback
    assert out["eps_diluted"].iloc[0] == 6.13  # epsdiluted coalesced


# ── balance sheet ────────────────────────────────────────────────────────────
def test_balance_sheet_payload_fetches_and_normalizes_to_table_columns() -> None:
    payload = [
        {
            "date": "2023-09-30",
            "fiscalYear": "2023",
            "period": "FY",
            "totalAssets": 352_583_000_000,
            "totalDebt": 111_088_000_000,
            "totalEquity": 62_146_000_000,  # legacy alias of totalStockholdersEquity
            "cashAndCashEquivalents": 29_965_000_000,
            "totalCurrentAssets": 143_566_000_000,
            "totalCurrentLiabilities": 145_308_000_000,
            "filingDate": "2023-11-03",
        }
    ]
    raw = _client({"/balance-sheet-statement": payload}).fetch(_req("fmp_balance_sheet"))
    out = BalanceSheetNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_BALANCE_SHEET)
    assert out["total_equity"].iloc[0] == 62_146_000_000  # totalEquity coalesced


# ── key metrics (the load-bearing /key-metrics ⨝ /ratios join) ───────────────
def _key_metrics_payload() -> list[dict[str, Any]]:
    return [
        {
            "date": "2023-09-30",
            "calendarYear": "2023",
            "period": "FY",
            "marketCap": 2_700_000_000_000,
            "enterpriseValue": 2_800_000_000_000,
            "peRatio": 29.0,
            "evToEBITDA": 22.0,
            "evToSales": 7.3,
            "returnOnInvestedCapital": 0.56,
            "returnOnEquity": 1.56,
            "returnOnAssets": 0.27,
            "currentRatio": 0.99,
            "debtToEquity": 1.79,
            "freeCashFlowPerShare": 6.5,
            "freeCashFlowYield": 0.038,
            "dividendYield": 0.005,
            "payoutRatio": 0.15,
            "revenuePerShare": 24.3,
            "bookValuePerShare": 3.85,  # legacy /key-metrics value
        }
    ]


def test_key_metrics_joins_ratios_bvps_and_normalizes_to_table_columns() -> None:
    metrics = _key_metrics_payload()
    ratios = [
        {"date": "2023-09-30", "period": "FY", "bookValuePerShare": 4.00},  # /ratios wins
    ]
    raw = _client({"/key-metrics": metrics, "/ratios": ratios}).fetch(_req("fmp_key_metrics"))
    # fetch() performed the join: the column the normalizer reads is present.
    assert "ratios_bookValuePerShare" in raw.columns
    assert raw["ratios_bookValuePerShare"].iloc[0] == 4.00

    out = KeyMetricsNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_KEY_METRICS)
    # Per-row fallback prefers the joined /ratios value over the legacy one.
    assert out["book_value_per_share"].iloc[0] == 4.00
    assert out["pe_ratio"].iloc[0] == 29.0
    assert out["roic"].iloc[0] == 0.56  # returnOnInvestedCapital coalesced


def test_key_metrics_ratios_outage_falls_back_to_legacy_bvps() -> None:
    """A /ratios outage emits the ratios column ABSENT; the normalizer falls back
    to the legacy /key-metrics bookValuePerShare for every row — never NULL."""
    raw = _client({"/key-metrics": _key_metrics_payload(), "/ratios": []}).fetch(
        _req("fmp_key_metrics")
    )
    assert "ratios_bookValuePerShare" not in raw.columns  # outage → column absent

    out = KeyMetricsNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_KEY_METRICS)
    assert out["book_value_per_share"].iloc[0] == 3.85  # legacy fallback, not NULL


def test_key_metrics_join_aligns_per_date_period() -> None:
    """Two fiscal rows join their own /ratios bvps on (date, period)."""
    metrics = [
        {"date": "2023-09-30", "period": "FY", "bookValuePerShare": 3.85},
        {"date": "2022-09-24", "period": "FY", "bookValuePerShare": 3.18},
    ]
    ratios = [
        {"date": "2022-09-24", "period": "FY", "bookValuePerShare": 3.30},
        {"date": "2023-09-30", "period": "FY", "bookValuePerShare": 4.00},
    ]
    raw = _client({"/key-metrics": metrics, "/ratios": ratios}).fetch(_req("fmp_key_metrics"))
    out = KeyMetricsNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    by_date = dict(zip(out["date"], out["book_value_per_share"], strict=True))
    assert by_date[date(2023, 9, 30)] == 4.00
    assert by_date[date(2022, 9, 24)] == 3.30


# ── analyst estimates ────────────────────────────────────────────────────────
def test_analyst_estimates_payload_fetches_and_normalizes_to_table_columns() -> None:
    payload = [
        {
            "date": "2024-09-30",
            "revenueAvg": 400_000_000_000,
            "revenueHigh": 410_000_000_000,
            "revenueLow": 390_000_000_000,
            "ebitdaAvg": 130_000_000_000,
            "epsAvg": 6.5,
            "epsHigh": 6.9,
            "epsLow": 6.1,
            "numberAnalystsEstimatedRevenue": 25,
            "numberAnalystsEstimatedEps": 27,
        }
    ]
    raw = _client({"/analyst-estimates": payload}).fetch(_req("fmp_analyst_estimates"))
    assert "revenueAvg" in raw.columns

    out = AnalystEstimatesNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_ANALYST_ESTIMATES)
    assert out["estimated_revenue_avg"].iloc[0] == 400_000_000_000
    assert out["estimated_eps_avg"].iloc[0] == 6.5


# ── adapter contract surface ─────────────────────────────────────────────────
def test_provider_and_datasets() -> None:
    client = FmpFundamentalsClient(transport=lambda url, params: [], api_key=API_KEY)
    assert client.provider is Provider.FMP
    assert client.datasets() == (
        "fmp_income_statement",
        "fmp_balance_sheet",
        "fmp_key_metrics",
        "fmp_analyst_estimates",
        "fmp_cash_flow",
        "fmp_earnings_calendar",
    )


def test_unserved_dataset_returns_empty_frame() -> None:
    client = FmpFundamentalsClient(transport=lambda url, params: [], api_key=API_KEY)
    assert client.fetch(_req("daily_prices")).empty


def test_empty_key_metrics_returns_empty_frame() -> None:
    raw = _client({"/key-metrics": []}).fetch(_req("fmp_key_metrics"))
    assert raw.empty


def test_live_factory_builds_a_client_without_importing_requests() -> None:
    client = live_fmp_fundamentals("secret")
    assert isinstance(client, FmpFundamentalsClient)
    assert client.api_key == "secret"


# ── cash flow (the load-bearing income-shares join; equity-factors 1.1) ──────
def _cash_flow_payload() -> list[dict[str, Any]]:
    return [
        {
            "date": "2023-09-30",
            "fiscalYear": "2023",
            "period": "FY",
            "operatingCashFlow": 110_543_000_000,
            "capitalExpenditure": -10_959_000_000,
            "freeCashFlow": 99_584_000_000,
            "stockBasedCompensation": 0.0,  # a vendor ZERO — a value, not absence
            "filingDate": "2023-11-03",
        }
    ]


def test_cash_flow_payload_joins_income_shares_and_normalizes() -> None:
    income = [
        {"date": "2023-09-30", "period": "FY", "weightedAverageShsOut": 15_744_231_000},
        {"date": "2022-09-24", "period": "FY", "weightedAverageShsOut": 16_215_963_000},
    ]
    raw = _client(
        {"/cash-flow-statement": _cash_flow_payload(), "/income-statement": income}
    ).fetch(_req("fmp_cash_flow"))
    assert raw["income_weightedAverageShsOut"].iloc[0] == 15_744_231_000

    out = CashFlowNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_CASH_FLOW)
    assert out["free_cash_flow"].iloc[0] == 99_584_000_000
    assert out["weighted_average_shares"].iloc[0] == 15_744_231_000
    assert out["stock_based_compensation"].iloc[0] == 0.0  # present zero survives
    assert out["filing_date"].iloc[0] == date(2023, 11, 3)


def test_cash_flow_income_outage_leaves_shares_missing_never_zero() -> None:
    raw = _client({"/cash-flow-statement": _cash_flow_payload(), "/income-statement": []}).fetch(
        _req("fmp_cash_flow")
    )
    assert "income_weightedAverageShsOut" not in raw.columns
    out = CashFlowNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert out["weighted_average_shares"].isna().all()


def test_cash_flow_missing_sbc_column_normalizes_to_missing() -> None:
    payload = [dict(_cash_flow_payload()[0])]
    del payload[0]["stockBasedCompensation"]
    raw = _client({"/cash-flow-statement": payload, "/income-statement": []}).fetch(
        _req("fmp_cash_flow")
    )
    out = CashFlowNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert out["stock_based_compensation"].isna().all()  # missing ≠ 0 (R1.3)


# ── earnings calendar (equity-factors 1.1) ───────────────────────────────────
def test_earnings_payload_fetches_and_normalizes_to_table_columns() -> None:
    payload = [
        {
            "date": "2024-05-02",
            "epsActual": 1.53,
            "epsEstimated": 1.50,  # FMP spells the estimate 'epsEstimated'
            "revenueActual": 90_753_000_000,
            "revenueEstimated": 90_010_000_000,
            "lastUpdated": "2024-05-03",
        }
    ]
    raw = _client({"/earnings": payload}).fetch(_req("fmp_earnings_calendar"))
    out = EarningsCalendarNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_EARNINGS_CALENDAR)
    assert out["eps_estimate"].iloc[0] == 1.50
    assert out["date"].iloc[0] == date(2024, 5, 2)
