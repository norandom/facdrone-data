# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 3.4: the shared FMP request wrapper (fail-open) and the per-family
normalizer contract (raw payload → table columns), R2.2, R2.4, R6.1."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import Mock, call, patch

import pandas as pd
import pytest
import yaml

from facdrone_data.acquisition.families.normalizers._base import (
    coerce_date,
    drop_null_and_dedup,
    project_to_table,
    stamp_updated_at,
)
from facdrone_data.acquisition.families.symbol_mapping import ProviderSymbols
from facdrone_data.acquisition.instrument_table import InstrumentTable
from facdrone_data.acquisition.schema import DAILY_PRICES, FAMA_FRENCH_FACTORS
from facdrone_data.providers.fmp_endpoints import (
    FmpPriceClient,
    FmpRequestError,
    chunked_date_ranges,
    fmp_get,
    fmp_records,
    live_http_json,
)
from facdrone_data.providers.policy import TIMEOUT_SECONDS
from facdrone_data.testing import API_KEY
from facdrone_data_types.enums import AssetClass
from facdrone_data_types.ports import FetchRequest, ProviderAnsweredEmpty
from facdrone_data_types.symbols import CanonicalSymbol


def _transport(payload: Any) -> Any:
    def _t(url: str, params: Mapping[str, str]) -> Any:
        del url, params
        return payload

    return _t


# ── FMP wrapper ──────────────────────────────────────────────────────────────
def test_live_transport_creates_one_session_on_first_use() -> None:
    params = {"apikey": API_KEY, "symbol": "AAPL"}
    payload = [{"close": 1.0}]
    with (
        patch("requests.Session") as session,
        patch("requests.get", side_effect=AssertionError("must use Session.get")),
    ):
        transport = live_http_json()
        session.assert_not_called()
        response = session.return_value.get.return_value
        response.json.return_value = payload
        assert transport("https://example.test/first", params) is payload
        assert transport("https://example.test/second", params) is payload
        session.assert_called_once_with()
        assert session.return_value.get.call_args_list == [
            call("https://example.test/first", params=params, timeout=TIMEOUT_SECONDS),
            call("https://example.test/second", params=params, timeout=TIMEOUT_SECONDS),
        ]
        assert response.method_calls == [
            call.raise_for_status(),
            call.json(),
            call.raise_for_status(),
            call.json(),
        ]


def test_live_transport_keeps_sessions_separate_and_propagates_http_errors() -> None:
    import requests

    first_session, second_session = Mock(), Mock()
    error = requests.HTTPError("unavailable")
    first_session.get.return_value.raise_for_status.side_effect = error
    second_session.get.return_value.json.return_value = []
    with (
        patch("requests.Session", side_effect=[first_session, second_session]) as session,
        patch("requests.get", side_effect=AssertionError("must use Session.get")),
    ):
        first, second = live_http_json(), live_http_json()
        session.assert_not_called()
        with pytest.raises(requests.HTTPError, match="unavailable"):
            first("https://example.test/first", {})
        first_session.get.return_value.json.assert_not_called()
        assert second("https://example.test/second", {}) == []
        assert session.call_count == 2


def test_fmp_get_returns_a_list_payload_verbatim() -> None:
    records = [{"date": "2024-01-01", "close": 1.0}]
    assert fmp_get(_transport(records), "/x", api_key=API_KEY) == records


def test_fmp_get_wraps_a_dict_payload_in_a_list() -> None:
    assert fmp_get(_transport({"a": 1}), "/x", api_key=API_KEY) == [{"a": 1}]


def test_fmp_get_fails_open_on_error_dict_string_and_exception() -> None:
    assert fmp_get(_transport({"Error Message": "limit"}), "/x", api_key=API_KEY) == []
    assert fmp_get(_transport("rate limited"), "/x", api_key=API_KEY) == []

    def _boom(url: str, params: Mapping[str, str]) -> Any:
        raise ConnectionError("network down")

    assert fmp_get(_boom, "/x", api_key=API_KEY) == []  # no raise — fail-open (R2.4)


def test_fmp_records_tells_an_empty_answer_from_a_failed_request() -> None:
    """The strict wrapper under ``fmp_get``: FMP answering with no rows is ``[]``; a
    transport fault, an error payload and a bare string are ``FmpRequestError``."""
    assert fmp_records(_transport([]), "/x", api_key=API_KEY) == []
    assert fmp_records(_transport({"a": 1}), "/x", api_key=API_KEY) == [{"a": 1}]
    for payload in ({"Error Message": "limit"}, "rate limited"):
        with pytest.raises(FmpRequestError, match="/x"):
            fmp_records(_transport(payload), "/x", api_key=API_KEY)

    def _boom(url: str, params: Mapping[str, str]) -> Any:
        raise ConnectionError("network down")

    with pytest.raises(FmpRequestError, match="network down"):
        fmp_records(_boom, "/x", api_key=API_KEY)


def _price_client(answers: list[Any]) -> FmpPriceClient:
    """An FMP price client whose transport gives one answer per request, in order; an
    exception in the list is raised as that request's transport fault."""
    queue = list(answers)

    def _answer(url: str, params: Mapping[str, str]) -> Any:
        del url, params
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    return FmpPriceClient(_answer, API_KEY)


DAILY = FetchRequest("GCUSD", "daily_prices", date(2026, 10, 26), date(2026, 10, 27))
INTRADAY = FetchRequest("ESUSD", "intraday_prices", date(2024, 1, 1), date(2024, 3, 31))
BAR = {"date": "2024-01-02 18:00:00", "close": 1.0}


def test_the_price_client_raises_why_no_rows_came_back() -> None:
    """FMP answering every request with nothing is ``ProviderAnsweredEmpty``; a failed
    request with nothing landed is ``FmpRequestError`` (the refresher tells them apart)."""
    with pytest.raises(ProviderAnsweredEmpty):
        _price_client([[]]).fetch(DAILY)
    with pytest.raises(FmpRequestError):
        _price_client([ConnectionError("down")]).fetch(DAILY)
    with pytest.raises(ProviderAnsweredEmpty):
        _price_client([[], [], [], []]).fetch(INTRADAY)
    with pytest.raises(FmpRequestError):
        _price_client([[], ConnectionError("down"), [], []]).fetch(INTRADAY)


def test_a_failed_intraday_window_keeps_the_other_windows_bars() -> None:
    frame = _price_client([[BAR], ConnectionError("down"), {"Error Message": "x"}, []]).fetch(
        INTRADAY
    )
    assert frame["datetime"].tolist() == ["2024-01-02 18:00:00"]
    assert _price_client([[{"date": "2026-10-26", "close": 1.0}]]).fetch(DAILY)[
        "close"
    ].tolist() == [1.0]
    assert _price_client([]).fetch(replace(DAILY, dataset="fred_series")).empty  # not a price set


def test_fmp_get_injects_the_api_key_and_endpoint_params() -> None:
    seen: dict[str, Any] = {}

    def _capture(url: str, params: Mapping[str, str]) -> Any:
        seen["url"] = url
        seen["params"] = params
        return []

    fmp_get(_capture, "/income-statement", {"symbol": "AAPL"}, api_key=API_KEY)
    assert seen["url"].endswith("/income-statement")
    assert seen["params"] == {"apikey": API_KEY, "symbol": "AAPL"}


def test_intraday_request_defaults_to_15min_and_accepts_each_interval() -> None:
    urls: list[str] = []

    def capture(url: str, params: Mapping[str, str]) -> list[dict[str, object]]:
        urls.append(url)
        assert params == {
            "apikey": API_KEY,
            "symbol": "ESUSD",
            "from": "2026-09-24",
            "to": "2026-09-24",
        }
        return [{"date": "2026-09-24 10:00:00", "close": 100.0}]

    client = FmpPriceClient(capture, API_KEY)
    request = FetchRequest("ESUSD", "intraday_prices", date(2026, 9, 24), date(2026, 9, 24))
    assert "datetime" in client.fetch(request)
    assert urls[-1].endswith("/historical-chart/15min")
    from dataclasses import replace

    for interval in ("1min", "5min", "15min", "30min", "1hour"):
        client.fetch(replace(request, interval=interval))
        assert urls[-1].endswith(f"/historical-chart/{interval}")
    with pytest.raises(ValueError, match="interval"):
        replace(request, interval="2min")


def test_a_long_intraday_range_is_split_below_the_provider_cap() -> None:
    """FMP serves at most ~3,000 intraday bars per request (the newest); a multi-year gap for
    a new series must be fetched in windows, and overlapping bars are kept once."""
    windows: list[tuple[str, str]] = []

    def capture(url: str, params: Mapping[str, str]) -> list[dict[str, object]]:
        del url
        windows.append((params["from"], params["to"]))
        return [
            {"date": f"{params['from']} 18:00:00", "close": 1.0},
            {"date": "2024-01-26 18:00:00", "close": 2.0},  # the same bar in every window
        ]

    client = FmpPriceClient(capture, API_KEY)
    frame = client.fetch(
        FetchRequest("ESUSD", "intraday_prices", date(2024, 1, 1), date(2024, 3, 31))
    )
    assert windows[0] == ("2024-01-01", "2024-01-26") and windows[-1][1] == "2024-03-31"
    assert len(windows) == 4  # 91 days in 26-day windows (25 days + the inclusive end)
    assert frame["datetime"].tolist().count("2024-01-26 18:00:00") == 1
    one_minute = FetchRequest(
        "ESUSD", "intraday_prices", date(2024, 1, 1), date(2024, 1, 3), interval="1min"
    )
    windows.clear()
    client.fetch(one_minute)
    assert len(windows) == 2  # one-minute bars: two-day windows


def test_chunked_date_ranges_tiles_inclusively() -> None:
    from datetime import date

    windows = chunked_date_ranges(date(2024, 1, 1), date(2024, 1, 10), days=4)
    assert windows == [
        (date(2024, 1, 1), date(2024, 1, 5)),
        (date(2024, 1, 6), date(2024, 1, 10)),
    ]


# ── Normalizer contract ──────────────────────────────────────────────────────
def test_project_to_table_yields_exactly_the_table_columns() -> None:
    raw = pd.DataFrame(
        [{"symbol": "EURUSD", "date": "2024-01-01", "close": 1.1, "junk": "drop-me"}]
    )
    out = project_to_table(raw, DAILY_PRICES)
    assert list(out.columns) == [c.name for c in DAILY_PRICES.columns]
    assert "junk" not in out.columns  # extras dropped
    assert out["open"].isna().all()  # absent non-key columns become NULL


def test_project_to_table_fails_loud_on_a_missing_pk_column() -> None:
    raw = pd.DataFrame([{"date": "2024-01-01", "close": 1.1}])  # no symbol (PK)
    import pytest

    with pytest.raises(ValueError, match="primary-key column 'symbol'"):
        project_to_table(raw, DAILY_PRICES)


def test_recorded_payload_maps_to_its_table_schema() -> None:
    """A recorded FMP daily payload → exactly the daily_prices columns (the
    normalizer contract end-to-end with the shared helpers)."""
    payload = [
        {"date": "2024-01-02", "open": 1.10, "high": 1.12, "low": 1.09, "close": 1.11, "volume": 5},
        {"date": "2024-01-01", "open": 1.08, "high": 1.11, "low": 1.07, "close": 1.10, "volume": 4},
        {"date": "2024-01-01", "open": 9.9, "high": 9.9, "low": 9.9, "close": None, "volume": 0},
    ]
    raw = pd.DataFrame(payload)
    raw["symbol"] = "EURUSD"
    raw["date"] = coerce_date(raw["date"])
    cleaned = drop_null_and_dedup(raw, null_subset=["close"], key=["symbol", "date"])
    out = project_to_table(cleaned, DAILY_PRICES)
    assert list(out.columns) == [c.name for c in DAILY_PRICES.columns]
    assert len(out) == 2  # the null-close row dropped
    assert set(out["date"]) == {raw["date"].iloc[0], raw["date"].iloc[1]}


def test_stamp_updated_at_only_for_tables_that_declare_it() -> None:
    as_of = datetime(2024, 6, 1, 12, 0)
    ff = stamp_updated_at(pd.DataFrame({"date": [1], "mkt_rf": [0.1]}), FAMA_FRENCH_FACTORS, as_of)
    assert list(ff["_updated_at"]) == [as_of]  # fama_french declares _updated_at
    prices = stamp_updated_at(pd.DataFrame({"symbol": ["X"]}), DAILY_PRICES, as_of)
    assert "_updated_at" not in prices.columns  # daily_prices does NOT


def _commodity_symbol(canonical_id: str) -> CanonicalSymbol:
    return CanonicalSymbol(
        canonical_id=canonical_id, asset_class=AssetClass.COMMODITIES, profit_ccy="USD"
    )


# ── Analyst normalizers satisfy upsert_panel's dedup precondition ──────────


def test_grade_actions_dedup_on_the_declared_primary_key() -> None:
    """``upsert_panel`` states that a duplicate primary key inside one VALUES batch is a
    Postgres CardinalityViolation, deliberately not coalesced at the merge layer — so the
    normalizer must reduce it. The provider returns one record per analyst ACTION, and the
    10-character date truncation collapses two same-day actions by one company onto the
    same key. Verified live: this aborted a real acquisition run over the derived universe
    with 'ON CONFLICT DO UPDATE command cannot affect row a second time'."""
    from facdrone_data.acquisition.families.normalizers.analyst import GradeActionsNormalizer
    from facdrone_data.acquisition.schema import FMP_GRADE_ACTIONS

    raw = pd.DataFrame(
        [
            {
                "date": "2015-07-06T09:00:00Z",
                "gradingCompany": "UBS",
                "previousGrade": "Neutral",
                "newGrade": "Neutral",
                "action": "maintain",
            },
            {  # same company, same DAY, later timestamp — collapses onto one key
                "date": "2015-07-06T17:30:00Z",
                "gradingCompany": "UBS",
                "previousGrade": "Neutral",
                "newGrade": "Buy",
                "action": "upgrade",
            },
            {
                "date": "2015-07-06T10:00:00Z",
                "gradingCompany": "Barclays",
                "previousGrade": "Overweight",
                "newGrade": "Overweight",
                "action": "maintain",
            },
        ]
    )
    out = GradeActionsNormalizer().normalize(
        raw, symbol=_commodity_symbol("CC=F"), as_of=datetime(2025, 1, 31, 23, 0)
    )

    key = [c.name for c in FMP_GRADE_ACTIONS.primary_key.columns]
    assert not out.duplicated(subset=key).any(), "a duplicate key would abort the batch upsert"
    assert len(out) == 2  # UBS collapsed to one row, Barclays kept
    ubs = out[out["grading_company"] == "UBS"].iloc[0]
    assert ubs["new_grade"] == "Buy"  # keep="last": the later action wins


def test_analyst_grades_dedup_on_the_declared_primary_key() -> None:
    """The consensus endpoint carries the same hazard on its (symbol, date) key."""
    from facdrone_data.acquisition.families.normalizers.analyst import AnalystGradesNormalizer
    from facdrone_data.acquisition.schema import FMP_ANALYST_GRADES

    raw = pd.DataFrame(
        [
            {"date": "2025-01-15T00:00:00Z", "analystRatingsbuy": 3},
            {"date": "2025-01-15T12:00:00Z", "analystRatingsbuy": 5},
        ]
    )

    out = AnalystGradesNormalizer().normalize(
        raw, symbol=_commodity_symbol("CC=F"), as_of=datetime(2025, 1, 31, 23, 0)
    )

    key = [c.name for c in FMP_ANALYST_GRADES.primary_key.columns]
    assert not out.duplicated(subset=key).any()
    assert len(out) == 1


def test_an_intraday_bar_with_a_zero_close_is_dropped_too() -> None:
    """The daily fix alone would leave the fifteen-minute path able to write a zero, and
    the simulation's intrabar replay reads those closes to fire stops and risk limits. A
    close of zero is not a price on either bar."""
    import pandas as pd

    from facdrone_data.acquisition.families.normalizers.prices import IntradayPricesNormalizer
    from facdrone_data_types.enums import AssetClass
    from facdrone_data_types.symbols import CanonicalSymbol

    raw = pd.DataFrame(
        {
            "datetime": ["2026-09-07 21:00:00", "2026-09-07 21:15:00", "2026-09-07 21:30:00"],
            "open": [99.1, 99.2, 99.2],
            "high": [99.3, 99.2, 99.3],
            "low": [99.0, 0.0, 99.1],
            "close": [99.2, 0.0, 99.25],
            "volume": [10, 0, 12],
        }
    )
    dollar_index = CanonicalSymbol(
        canonical_id="DX-Y.NYB", asset_class=AssetClass.INDICES, profit_ccy="USD"
    )
    out = IntradayPricesNormalizer().normalize(
        raw, symbol=dollar_index, as_of=datetime(2026, 9, 8, 2, 0)
    )
    assert len(out) == 2
    assert (out["close"] > 0).all()


_FTSE = CanonicalSymbol("^FTSE", AssetClass.INDICES, "GBP")
_N225 = CanonicalSymbol("^N225", AssetClass.INDICES, "JPY")
_GSPC = CanonicalSymbol("^GSPC", AssetClass.INDICES, "USD")


def _index_table(path: Path) -> InstrumentTable:
    rows = [
        {
            "canonical": symbol,
            "kind": "cfd",
            "asset_class": "indices",
            "settlement_ccy": ccy,
            "providers": {"fmp": symbol, "yfinance": symbol},
            "intraday": "fmp",
        }
        | ({"intraday_zone": zone} if zone else {})
        for symbol, ccy, zone in (
            ("^FTSE", "GBP", "Europe/London"),
            ("^N225", "JPY", "Asia/Tokyo"),
            ("^GSPC", "USD", None),
        )
    ]
    path.write_text(yaml.safe_dump(rows, sort_keys=False), encoding="utf-8")
    return InstrumentTable((path,))


def _bars(day: str, *openings: str) -> pd.DataFrame:
    n = len(openings)
    return pd.DataFrame(
        {
            "datetime": [f"{day} {at}:00" for at in openings],
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.5] * n,
            "volume": [10] * n,
        }
    )


@pytest.mark.parametrize(
    ("canonical", "openings", "as_of", "landed"),
    [
        # 08:00-08:45 London (GMT on 2026-10-27) completes by 09:00 UTC; read in New York the
        # same stamps open at 13:00 UTC, so the NY-only port dropped all four as incomplete
        (_FTSE, ("08:00", "08:15", "08:30", "08:45"), datetime(2026, 10, 27, 9, 0), 4),
        # 09:00 Tokyo is 00:00 UTC: complete at 00:15 UTC, fourteen hours before New York's
        (_N225, ("09:00", "09:15"), datetime(2026, 10, 27, 0, 15, tzinfo=UTC), 1),
        # an undeclared row keeps FMP's declared zone: 09:30 New York (EDT) is 13:30 UTC
        (_GSPC, ("09:30", "09:45"), datetime(2026, 10, 27, 13, 50, tzinfo=UTC), 1),
    ],
)
def test_an_intraday_bar_is_complete_in_its_symbols_stamp_zone(
    tmp_path: Path,
    canonical: CanonicalSymbol,
    openings: tuple[str, ...],
    as_of: datetime,
    landed: int,
) -> None:
    from facdrone_data.acquisition.families.normalizers.prices import (
        IntradayPricesNormalizer,
        price_families,
    )

    table = _index_table(tmp_path / "indices.yml")
    port = next(p for p in price_families() if p.writer == "refresh_fmp_intraday")
    normalizer = port.normalizer_for(ProviderSymbols(table))
    assert normalizer == IntradayPricesNormalizer(zones=table.intraday_zones())
    out = normalizer.normalize(_bars("2026-10-27", *openings), symbol=canonical, as_of=as_of)
    assert len(out) == landed
    assert list(out["datetime"]) == [pd.Timestamp(f"2026-10-27 {at}") for at in openings[:landed]]
    # without the table every stamp reads in New York
    unbound = IntradayPricesNormalizer().normalize(
        _bars("2026-10-27", *openings), symbol=canonical, as_of=as_of
    )
    assert len(unbound) == (landed if canonical is _GSPC else 0)
