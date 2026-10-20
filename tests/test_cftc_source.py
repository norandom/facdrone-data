# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""CFTC COT transport (R2.1, R2.3).

Proves payload -> fetch -> normalize end-to-end with NO network: a RECORDED
``cot_reports.cot_year`` Legacy-Futures frame is fed through an INJECTED per-year
transport, ``CftcCotClient.fetch`` derives the years covering the request window and
concatenates the per-year frames into the RAW shape ``CftcCotNormalizer`` reads, and
the REAL normalizer is then run over that frame and asserted to yield exactly the
``cftc_cot`` table columns (with the unmapped market dropped and the nets derived).
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from facdrone_data.acquisition.families.normalizers.cftc import CftcCotNormalizer
from facdrone_data.acquisition.schema import CFTC_COT
from facdrone_data.providers.cftc_source import (
    CftcCotClient,
    CotYearFetch,
    live_cftc,
)
from facdrone_data_types.ports import FetchRequest, MarketDataClient
from facdrone_data_types.symbols import Provider

_REQ = FetchRequest(
    provider_symbol="GC=F",  # keyless family: not used for mapping (fold is internal)
    dataset="cftc_cot",
    start=date(2023, 6, 1),
    end=date(2024, 2, 1),
)
_TABLE_COLUMNS = [c.name for c in CFTC_COT.columns]


def _cot_row(
    market: str, as_of: str, *, counts: tuple[int, int, int, int, int]
) -> dict[str, object]:
    """One Legacy-Futures record carrying exactly the columns the normalizer reads.

    ``counts`` is ``(noncomm_long, noncomm_short, comm_long, comm_short, open_interest)``."""
    ncl, ncs, cl, cs, oi = counts
    return {
        "Market and Exchange Names": market,
        "As of Date in Form YYYY-MM-DD": as_of,
        "Open Interest (All)": oi,
        "Noncommercial Positions-Long (All)": ncl,
        "Noncommercial Positions-Short (All)": ncs,
        "Commercial Positions-Long (All)": cl,
        "Commercial Positions-Short (All)": cs,
        "% of OI-Noncommercial-Long (All)": round(100.0 * ncl / oi, 1),
        "% of OI-Noncommercial-Short (All)": round(100.0 * ncs / oi, 1),
        "Change in Noncommercial-Long (All)": 10,
        "Change in Noncommercial-Short (All)": -5,
    }


def _recorded_year(year: int) -> pd.DataFrame:
    """A recorded ``cot_year`` frame for one calendar year: one mapped GOLD row plus
    one UNMAPPED market the normalizer must skip (R5.2)."""
    return pd.DataFrame(
        [
            _cot_row(
                "GOLD - COMMODITY EXCHANGE INC.",
                f"{year}-07-04",
                counts=(200_000, 120_000, 90_000, 160_000, 500_000),
            ),
            _cot_row(
                "LEAN HOGS - CHICAGO MERCANTILE EXCHANGE",  # not in CFTC_MAP → skipped
                f"{year}-07-04",
                counts=(1_000, 2_000, 3_000, 4_000, 10_000),
            ),
        ]
    )


def _fixed_transport() -> CotYearFetch:
    def _t(year: int) -> pd.DataFrame:
        return _recorded_year(year)

    return _t


def test_client_conforms_to_the_market_data_client_port() -> None:
    client = CftcCotClient(transport=_fixed_transport())
    assert isinstance(client, MarketDataClient)
    assert client.provider is Provider.CFTC
    assert client.datasets() == ("cftc_cot",)


def test_fetch_spans_the_request_years_and_concatenates() -> None:
    captured: list[int] = []

    def _t(year: int) -> pd.DataFrame:
        captured.append(year)
        return _recorded_year(year)

    client = CftcCotClient(transport=_t)
    raw = client.fetch(_REQ)

    # The window 2023-06 .. 2024-02 touches two calendar years; both are pulled whole.
    assert captured == [2023, 2024]
    # Raw is the untouched cot_year shape: both markets, both years, no projection.
    assert len(raw) == 4
    assert "Market and Exchange Names" in raw.columns
    assert "As of Date in Form YYYY-MM-DD" in raw.columns


def test_recorded_payload_maps_through_the_real_normalizer_to_cftc_cot() -> None:
    """payload -> fetch -> REAL normalize -> exactly the cftc_cot columns."""
    client = CftcCotClient(transport=_fixed_transport())
    raw = client.fetch(_REQ)

    out = CftcCotNormalizer().normalize(raw, symbol=None, as_of=datetime(2024, 3, 1, 12, 0))

    assert list(out.columns) == _TABLE_COLUMNS
    # Two years, one MAPPED gold row each; the unmapped market is skipped (R5.2).
    assert len(out) == 2
    assert set(out["symbol"]) == {"GC=F"}  # market-name fold, keyless family
    assert sorted(out["date"]) == [date(2023, 7, 4), date(2024, 7, 4)]
    row = out.iloc[0]
    assert row["open_interest"] == 500_000
    assert row["noncomm_net"] == 200_000 - 120_000  # zero-fill-before-subtract
    assert row["comm_net"] == 90_000 - 160_000


def test_unmapped_and_bad_numerics_zero_fill_through_the_normalizer() -> None:
    """A row whose numeric fields are garbage zero-fills, and an unmapped market is
    dropped — proving the recorded payload threads the normalizer's quirks."""

    def _t(year: int) -> pd.DataFrame:
        del year
        return pd.DataFrame(
            [
                {
                    "Market and Exchange Names": "SILVER - COMMODITY EXCHANGE INC.",
                    "As of Date in Form YYYY-MM-DD": "2024-01-09",
                    "Open Interest (All)": "n/a",  # bad → 0
                    "Noncommercial Positions-Long (All)": "",  # bad → 0
                    "Noncommercial Positions-Short (All)": 5,
                    "Commercial Positions-Long (All)": 7,
                    "Commercial Positions-Short (All)": 3,
                    "% of OI-Noncommercial-Long (All)": "bad",  # bad → 0.0
                    "% of OI-Noncommercial-Short (All)": 1.5,
                    "Change in Noncommercial-Long (All)": 2,
                    "Change in Noncommercial-Short (All)": 1,
                },
            ]
        )

    client = CftcCotClient(transport=_t)
    req = FetchRequest("SI=F", "cftc_cot", date(2024, 1, 1), date(2024, 1, 31))
    out = CftcCotNormalizer().normalize(
        client.fetch(req), symbol=None, as_of=datetime(2024, 2, 1, 12, 0)
    )

    assert list(out.columns) == _TABLE_COLUMNS
    assert len(out) == 1
    row = out.iloc[0]
    assert row["symbol"] == "SI=F"
    assert row["open_interest"] == 0  # zero-filled
    assert row["noncomm_long"] == 0  # zero-filled
    assert row["noncomm_net"] == 0 - 5  # zero-fill-before-subtract
    assert row["pct_noncomm_long"] == 0.0  # zero-filled float


def test_folded_duplicate_markets_dedup_to_one_natural_key_keep_last() -> None:
    """Two market names that fold to the SAME YF symbol on the same date must not survive as
    duplicate (symbol, date) rows — that would crash upsert_panel's single VALUES batch with a
    Postgres cardinality error. Dedup keep="last" (row-by-row last-write-wins)."""

    def _t(year: int) -> pd.DataFrame:
        del year
        return pd.DataFrame(
            [
                _cot_row(
                    "CRUDE OIL, LIGHT SWEET - NEW YORK MERCANTILE EXCHANGE",  # → CL=F
                    "2024-01-09",
                    counts=(200_000, 100_000, 80_000, 150_000, 400_000),
                ),
                _cot_row(
                    "WTI FINANCIAL CRUDE OIL - NEW YORK MERCANTILE EXCHANGE",  # → CL=F (same key)
                    "2024-01-09",
                    counts=(11_000, 22_000, 33_000, 44_000, 99_000),
                ),
            ]
        )

    client = CftcCotClient(transport=_t)
    req = FetchRequest("CL=F", "cftc_cot", date(2024, 1, 1), date(2024, 1, 31))
    out = CftcCotNormalizer().normalize(
        client.fetch(req), symbol=None, as_of=datetime(2024, 2, 1, 12, 0)
    )

    assert list(out.columns) == _TABLE_COLUMNS
    assert len(out) == 1  # collapsed to a single (CL=F, 2024-01-09) row
    row = out.iloc[0]
    assert row["symbol"] == "CL=F"
    assert row["date"] == date(2024, 1, 9)
    assert row["open_interest"] == 99_000  # keep="last": the WTI-financial row wins
    assert row["noncomm_net"] == 11_000 - 22_000


def test_non_cftc_dataset_returns_empty_fail_open() -> None:
    client = CftcCotClient(transport=_fixed_transport())
    other = FetchRequest("GC=F", "daily_prices", date(2024, 1, 1), date(2024, 1, 4))
    assert client.fetch(other).empty


def test_fetch_fails_open_on_transport_error_per_year() -> None:
    """A year whose fetch raises is skipped; surviving years still flow through."""

    def _t(year: int) -> pd.DataFrame:
        if year == 2023:
            raise ConnectionError("network down")
        return _recorded_year(year)

    client = CftcCotClient(transport=_t)
    raw = client.fetch(_REQ)  # 2023 raises, 2024 survives
    assert len(raw) == 2  # only the 2024 frame's two rows
    assert raw["As of Date in Form YYYY-MM-DD"].str.startswith("2024").all()


def test_all_years_failing_yields_an_empty_fetch_that_still_normalizes() -> None:
    def _boom(year: int) -> pd.DataFrame:
        del year
        raise ConnectionError("network down")

    client = CftcCotClient(transport=_boom)
    raw = client.fetch(_REQ)
    assert raw.empty
    # The empty raw still threads through the normalizer to the table columns.
    out = CftcCotNormalizer().normalize(raw, symbol=None, as_of=datetime(2024, 3, 1, 12, 0))
    assert list(out.columns) == _TABLE_COLUMNS
    assert out.empty


def test_live_cftc_factory_imports_without_the_sdk_installed() -> None:
    """The factory must BUILD without cot_reports installed (the import is deferred
    into the returned transport); only CALLING the transport imports the SDK and
    then fails with ImportError if it is absent."""
    import importlib.util

    transport = live_cftc()  # must NOT raise even when cot_reports is absent
    assert callable(transport)
    if importlib.util.find_spec("cot_reports") is None:
        import pytest

        with pytest.raises(ImportError):
            transport(2025)
