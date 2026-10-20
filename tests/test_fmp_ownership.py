# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""FMP ownership transport: recorded payload → fetch → REAL normalizer (R2.2).

Proves the payload→fetch→normalize contract end-to-end with NO network. Each test
feeds a recorded FMP payload through an INJECTED transport that routes by the
``(year, quarter)`` query params (reproducing the per-period loop), calls
``FmpOwnershipClient.fetch``, then feeds the raw frame into the matching REAL
normalizer and asserts it returns exactly the target table's columns — and that the
camelCase→snake_case rename + ``year``/``quarter`` stamping survive the round-trip.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from facdrone_data.acquisition.families.normalizers.ownership import (
    InstitutionalOwnershipNormalizer,
    PositionsSummaryNormalizer,
)
from facdrone_data.acquisition.schema import (
    FMP_INSTITUTIONAL_OWNERSHIP,
    FMP_POSITIONS_SUMMARY,
)
from facdrone_data.providers.fmp_ownership import (
    FmpOwnershipClient,
    live_fmp_ownership,
    year_quarters,
)
from facdrone_data.testing import API_KEY
from facdrone_data_types.enums import AssetClass
from facdrone_data_types.ports import FetchRequest
from facdrone_data_types.symbols import CanonicalSymbol, Provider

AS_OF = datetime(2024, 6, 1, 12, 0)
SYMBOL = CanonicalSymbol("AAPL", AssetClass.EQUITIES_US, "USD")


def _client(by_period: Mapping[tuple[str, str], Any]) -> FmpOwnershipClient:
    """A client whose transport routes by the ``(year, quarter)`` query params to a
    recorded payload — reproducing the one-call-per-period loop offline."""

    def _transport(url: str, params: Mapping[str, str]) -> Any:
        del url
        key = (params["year"], params["quarter"])
        return by_period.get(key, [])

    return FmpOwnershipClient(transport=_transport, api_key=API_KEY)


def _req(dataset: str, *, start: date, end: date) -> FetchRequest:
    return FetchRequest(provider_symbol="AAPL", dataset=dataset, start=start, end=end)


def _table_columns(table: Any) -> list[str]:
    return [c.name for c in table.columns]


# ── (year, quarter) derivation ───────────────────────────────────────────────
def test_year_quarters_covers_the_inclusive_window() -> None:
    pairs = year_quarters(date(2023, 2, 15), date(2024, 5, 1))
    assert pairs == [
        (2023, 1),
        (2023, 2),
        (2023, 3),
        (2023, 4),
        (2024, 1),
        (2024, 2),
    ]


def test_year_quarters_empty_when_start_after_end() -> None:
    assert year_quarters(date(2024, 5, 1), date(2024, 1, 1)) == []


# ── institutional ownership (13F holder rows aggregated per period) ───────────
def test_institutional_ownership_payload_fetches_and_normalizes_to_table_columns() -> None:
    # Two distinct (year, quarter) blocks of holder rows; the normalizer
    # aggregates each block into exactly one stored row.
    q1_holders = [
        {
            "holder": "VANGUARD",
            "sharesNumber": 1_000_000,
            "changeInSharesNumber": 200_000,
            "changeInSharesNumberPercentage": 25.0,  # increased
            "isNew": True,
            "isSoldOut": False,
            "filingDate": "2023-04-15 00:00:00",
        },
        {
            "holder": "BLACKROCK",
            "sharesNumber": 500_000,
            "changeInSharesNumber": -100_000,
            "changeInSharesNumberPercentage": -16.6,  # decreased
            "isNew": False,
            "isSoldOut": False,
            "filingDate": "2023-04-20 00:00:00",
        },
    ]
    q2_holders = [
        {
            "holder": "STATE STREET",
            "sharesNumber": 300_000,
            "changeInSharesNumber": 0,
            "changeInSharesNumberPercentage": 0.0,
            "isNew": False,
            "isSoldOut": True,
            "filingDate": "2023-07-10 00:00:00",
        }
    ]
    client = _client({("2023", "1"): q1_holders, ("2023", "2"): q2_holders})

    raw = client.fetch(
        _req("fmp_institutional_ownership", start=date(2023, 1, 1), end=date(2023, 6, 30))
    )
    # fetch() renamed the camelCase keys to exactly what the normalizer reads and
    # stamped the per-period year/quarter on every row.
    for col in (
        "change_in_shares_number_percentage",
        "is_new",
        "is_sold_out",
        "shares_number",
        "change_in_shares_number",
        "filing_date",
        "year",
        "quarter",
    ):
        assert col in raw.columns
    assert set(zip(raw["year"], raw["quarter"], strict=True)) == {(2023, 1), (2023, 2)}

    out = InstitutionalOwnershipNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_INSTITUTIONAL_OWNERSHIP)
    assert len(out) == 2  # one aggregated row per (year, quarter) block
    q1 = out[(out["year"] == 2023) & (out["quarter"] == 1)]
    assert q1["symbol"].iloc[0] == "AAPL"
    assert q1["n_holders"].iloc[0] == 2
    assert q1["n_new"].iloc[0] == 1
    assert q1["n_increased"].iloc[0] == 1
    assert q1["n_decreased"].iloc[0] == 1
    assert q1["total_shares"].iloc[0] == 1_500_000
    assert q1["net_shares_change"].iloc[0] == 100_000
    q2 = out[(out["year"] == 2023) & (out["quarter"] == 2)]
    assert q2["n_sold_out"].iloc[0] == 1


# ── positions summary (one pre-aggregated row per period) ────────────────────
def test_positions_summary_payload_fetches_and_normalizes_to_table_columns() -> None:
    q1_summary = [
        {
            "investorsHolding": 4200,
            "investorsHoldingChange": 50,
            "newPositions": 120,
            "closedPositions": 70,
            "increasedPositions": 900,
            "reducedPositions": 800,
            "putCallRatio": 0.85,
            "putCallRatioChange": -0.05,
            "totalInvestedChange": 1_250_000.0,
            "ownershipPercentChange": 0.012,
        }
    ]
    client = _client({("2023", "1"): q1_summary})

    raw = client.fetch(_req("fmp_positions_summary", start=date(2023, 1, 1), end=date(2023, 3, 31)))
    for col in (
        "investors_holding",
        "put_call_ratio",
        "ownership_pct_change",
        "year",
        "quarter",
    ):
        assert col in raw.columns

    out = PositionsSummaryNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _table_columns(FMP_POSITIONS_SUMMARY)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["symbol"] == "AAPL"
    assert row["year"] == 2023
    assert row["quarter"] == 1
    assert row["investors_holding"] == 4200
    assert row["new_positions"] == 120
    assert row["put_call_ratio"] == 0.85
    assert row["ownership_pct_change"] == 0.012


# ── fail-open + adapter contract surface ─────────────────────────────────────
def test_empty_payload_yields_empty_frame() -> None:
    client = _client({})  # every period returns []
    raw = client.fetch(
        _req("fmp_institutional_ownership", start=date(2023, 1, 1), end=date(2023, 12, 31))
    )
    assert raw.empty


def test_provider_and_datasets() -> None:
    client = FmpOwnershipClient(transport=lambda url, params: [], api_key=API_KEY)
    assert client.provider is Provider.FMP
    assert client.datasets() == ("fmp_institutional_ownership", "fmp_positions_summary")


def test_unserved_dataset_returns_empty_frame() -> None:
    client = FmpOwnershipClient(transport=lambda url, params: [], api_key=API_KEY)
    out = client.fetch(_req("daily_prices", start=date(2023, 1, 1), end=date(2023, 12, 31)))
    assert out.empty


def test_live_factory_builds_a_client_without_importing_requests() -> None:
    client = live_fmp_ownership("secret")
    assert isinstance(client, FmpOwnershipClient)
    assert client.api_key == "secret"
