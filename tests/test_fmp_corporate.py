# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""FmpCorporateClient: recorded payload → fetch → REAL normalizer, no network.

Proves the transport produces the exact raw columns each normalizer reads:

* ``fmp_analyst_grades`` → ``AnalystGradesNormalizer`` → ``FMP_ANALYST_GRADES``
* ``fmp_grade_actions``  → ``GradeActionsNormalizer``  → ``FMP_GRADE_ACTIONS``
* ``fmp_insider_trading``→ ``InsiderTradingNormalizer``→ ``FMP_INSIDER_TRADING``

The transport is an injected fake fed recorded FMP payloads; the insider fake
serves descending-page payloads so the pagination + filing-date short-circuit
is exercised offline.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from facdrone_data.acquisition.families.normalizers.analyst import (
    AnalystGradesNormalizer,
    GradeActionsNormalizer,
)
from facdrone_data.acquisition.families.normalizers.ownership import InsiderTradingNormalizer
from facdrone_data.acquisition.schema import (
    FMP_ANALYST_GRADES,
    FMP_GRADE_ACTIONS,
    FMP_INSIDER_TRADING,
)
from facdrone_data.providers.fmp_corporate import FmpCorporateClient
from facdrone_data.testing import API_KEY
from facdrone_data_types.enums import AssetClass
from facdrone_data_types.ports import FetchRequest
from facdrone_data_types.symbols import CanonicalSymbol, Provider

AS_OF = datetime(2024, 6, 1, 12, 0)
SYMBOL = CanonicalSymbol("AAPL", AssetClass.EQUITIES_US, "USD")


def _static_transport(payload: Any) -> Any:
    def _t(url: str, params: Mapping[str, str]) -> Any:
        del url, params
        return payload

    return _t


def _columns(table: Any) -> list[str]:
    return [c.name for c in table.columns]


# ── analyst grades ────────────────────────────────────────────────────────────
def test_analyst_grades_payload_fetch_normalize() -> None:
    payload = [
        {
            "date": "2024-05-01T00:00:00",
            "analystRatingsStrongBuy": 12,
            "analystRatingsBuy": 8,
            "analystRatingsHold": 3,
            "analystRatingsSell": 1,
            "analystRatingsStrongSell": 0,
        },
        {
            "date": "2024-04-01T00:00:00",
            "analystRatingsStrongBuy": 10,
            "analystRatingsBuy": 9,
            "analystRatingsHold": 4,
            "analystRatingsSell": 1,
            "analystRatingsStrongSell": 1,
        },
    ]
    client = FmpCorporateClient(transport=_static_transport(payload), api_key=API_KEY)
    req = FetchRequest("AAPL", "fmp_analyst_grades", date(2024, 1, 1), date(2024, 6, 1))
    raw = client.fetch(req)

    out = AnalystGradesNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _columns(FMP_ANALYST_GRADES)
    assert len(out) == 2
    assert out["strong_buy"].tolist() == [12, 10]
    assert set(out["symbol"]) == {"AAPL"}


# ── grade actions ─────────────────────────────────────────────────────────────
def test_grade_actions_payload_fetch_normalize() -> None:
    payload = [
        {
            "date": "2024-05-10T00:00:00",
            "gradingCompany": "Morgan Stanley",
            "previousGrade": "Hold",
            "newGrade": "Buy",
            "action": "upgrade",
        },
        {
            "date": "2024-05-09T00:00:00",
            "gradingCompany": "Goldman Sachs",
            "previousGrade": "Buy",
            "newGrade": "Hold",
            "action": "downgrade",
        },
    ]
    client = FmpCorporateClient(transport=_static_transport(payload), api_key=API_KEY)
    req = FetchRequest("AAPL", "fmp_grade_actions", date(2024, 1, 1), date(2024, 6, 1))
    raw = client.fetch(req)

    out = GradeActionsNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _columns(FMP_GRADE_ACTIONS)
    assert len(out) == 2
    assert out["grading_company"].tolist() == ["Morgan Stanley", "Goldman Sachs"]
    assert out["action"].tolist() == ["upgrade", "downgrade"]


# ── insider trading (paged) ───────────────────────────────────────────────────
def _insider_record(filing: str, name: str, ttype: str, qty: float) -> dict[str, Any]:
    return {
        "filingDate": f"{filing} 00:00:00",
        "transactionDate": f"{filing} 00:00:00",
        "transactionType": ttype,
        "securitiesTransacted": qty,
        "price": 190.5,
        "reportingName": name,
        "typeOfOwner": "officer: CEO",
        "acquisitionOrDisposition": "A" if ttype == "P-Purchase" else "D",
    }


class _PagingTransport:
    """Serves one recorded page per ``page`` param, descending by filing date;
    an out-of-range page returns ``[]`` (FMP's end-of-history signal)."""

    def __init__(self, pages: list[list[dict[str, Any]]]) -> None:
        self._pages = pages
        self.seen_pages: list[str] = []

    def __call__(self, url: str, params: Mapping[str, str]) -> Any:
        del url
        page = str(params["page"])
        self.seen_pages.append(page)
        idx = int(page)
        return self._pages[idx] if idx < len(self._pages) else []


def test_insider_trading_paged_payload_fetch_normalize() -> None:
    pages = [
        [
            _insider_record("2024-05-20", "Cook Tim", "S-Sale", 5000),
            _insider_record("2024-05-18", "Maestri Luca", "P-Purchase", 1200),
        ],
        [
            _insider_record("2024-05-10", "Cook Tim", "S-Sale", 2000),
        ],
    ]
    transport = _PagingTransport(pages)
    client = FmpCorporateClient(transport=transport, api_key=API_KEY)
    req = FetchRequest("AAPL", "fmp_insider_trading", date(2024, 1, 1), date(2024, 6, 1))
    raw = client.fetch(req)

    # The transport produces exactly the snake_case columns the normalizer reads.
    for column in (
        "filing_date",
        "transaction_date",
        "transaction_type",
        "securities_transacted",
        "price",
        "reporting_name",
        "type_of_owner",
        "acquisition_or_disposition",
    ):
        assert column in raw.columns
    assert len(raw) == 3  # both pages concatenated

    out = InsiderTradingNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _columns(FMP_INSIDER_TRADING)
    assert len(out) == 3
    assert set(out["symbol"]) == {"AAPL"}
    assert out["securities_transacted"].tolist() == [5000.0, 1200.0, 2000.0]


def test_insider_pagination_short_circuits_below_cursor_floor() -> None:
    """A page whose newest filing predates ``req.start`` (the stored max filing
    date) stops paging — the ``filing <= latest and page > 0`` short-circuit."""
    pages = [
        [_insider_record("2024-05-20", "Cook Tim", "S-Sale", 5000)],
        [_insider_record("2024-01-05", "Cook Tim", "S-Sale", 100)],  # all below floor
        [_insider_record("2023-12-01", "Cook Tim", "S-Sale", 7)],  # must NOT be fetched
    ]
    transport = _PagingTransport(pages)
    client = FmpCorporateClient(transport=transport, api_key=API_KEY)
    req = FetchRequest("AAPL", "fmp_insider_trading", date(2024, 2, 1), date(2024, 6, 1))
    raw = client.fetch(req)

    assert transport.seen_pages == ["0", "1"]  # page 2 never requested
    assert "2023-12-01" not in set(raw["filing_date"])


def test_insider_empty_payload_yields_empty_normalized_frame() -> None:
    client = FmpCorporateClient(transport=_static_transport([]), api_key=API_KEY)
    req = FetchRequest("AAPL", "fmp_insider_trading", date(2024, 1, 1), date(2024, 6, 1))
    raw = client.fetch(req)
    out = InsiderTradingNormalizer().normalize(raw, symbol=SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _columns(FMP_INSIDER_TRADING)
    assert out.empty


def test_unserved_dataset_returns_empty() -> None:
    client = FmpCorporateClient(transport=_static_transport([{"x": 1}]), api_key=API_KEY)
    req = FetchRequest("AAPL", "daily_prices", date(2024, 1, 1), date(2024, 6, 1))
    assert client.fetch(req).empty
    assert client.datasets() == (
        "fmp_analyst_grades",
        "fmp_grade_actions",
        "fmp_insider_trading",
    )
    assert client.provider is Provider.FMP
