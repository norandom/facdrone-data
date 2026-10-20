# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 3.2: per-family incremental cursors (R7.1, R7.2; D5, D6).

The pure gap arithmetic is tested exhaustively offline; each cursor also gets one
DB-backed case (seed rows → assert gaps) on the scratch PostgreSQL database.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pandas as pd
import pytest
from sqlalchemy import Engine, delete

from facdrone_data.acquisition.families.cursors import (
    FullWindow,
    MaxDateByName,
    MaxDateBySymbol,
    MaxDateGlobal,
    MinMaxRangeGapFill,
    YfMaxDateLastBar,
    _forward_gaps,
    _last_bar_gaps,
    _minmax_gaps,
)
from facdrone_data.acquisition.families.ports import IncrementalStrategy
from facdrone_data.acquisition.schema import (
    DAILY_PRICES,
    FMP_ECONOMIC_INDICATORS,
    FMP_TREASURY_RATES,
)

WANT = (date(2024, 1, 1), date(2024, 1, 31))


# ── offline: pure gap arithmetic ────────────────────────────────────────────
def test_minmax_empty_table_fetches_the_whole_window() -> None:
    assert _minmax_gaps(WANT, None, None) == (WANT,)


def test_minmax_backfill_and_forward_ranges_exclude_the_boundary_bar() -> None:
    # stored 10th..20th; want 1st..31st → backfill (1,10) + forward (21,31)
    gaps = _minmax_gaps(WANT, date(2024, 1, 10), date(2024, 1, 20))
    assert gaps == ((date(2024, 1, 1), date(2024, 1, 10)), (date(2024, 1, 21), date(2024, 1, 31)))


def test_minmax_fully_covered_returns_no_gap() -> None:
    # stored covers the whole want and beyond → no backfill, forward starts past end
    assert _minmax_gaps(WANT, date(2023, 12, 1), date(2024, 1, 31)) == ()


def test_minmax_only_forward_when_start_not_before_earliest() -> None:
    assert _minmax_gaps(WANT, date(2024, 1, 1), date(2024, 1, 20)) == (
        (date(2024, 1, 21), date(2024, 1, 31)),
    )


def test_minmax_overlap_reaches_back_into_the_held_tail() -> None:
    """2026-09-07: the FMP price cursor never re-fetched a bar it already held, so the
    dollar index's provisional Sunday stub — close exactly zero — could never be
    corrected, even though the upsert overwrites on change. With an overlap the forward
    range starts inside the held tail; with none it keeps the plain min/max gap shape (D5)."""
    stored = (date(2024, 1, 10), date(2024, 1, 20))
    assert _minmax_gaps(WANT, *stored, overlap_days=0) == (
        (date(2024, 1, 1), date(2024, 1, 10)),
        (date(2024, 1, 21), date(2024, 1, 31)),
    )
    assert _minmax_gaps(WANT, *stored, overlap_days=5) == (
        (date(2024, 1, 1), date(2024, 1, 10)),
        (date(2024, 1, 16), date(2024, 1, 31)),
    )


def test_minmax_overlap_never_reaches_before_the_wanted_start() -> None:
    # stored tail ends on the 20th, want starts on the 18th: the overlap is clamped
    assert _minmax_gaps(
        (date(2024, 1, 18), date(2024, 1, 31)),
        date(2024, 1, 10),
        date(2024, 1, 20),
        overlap_days=30,
    ) == ((date(2024, 1, 18), date(2024, 1, 31)),)


def test_forward_skips_within_threshold() -> None:
    # latest 30th, end 31st → 1 day gap, skip_days=1 → skip
    assert _forward_gaps(WANT, date(2024, 1, 30), skip_days=1, overlap_days=0) == ()


def test_forward_fetches_past_threshold_with_overlap() -> None:
    # latest 10th, end 31st → 21 days > skip 7; overlap 1 → start 9th
    assert _forward_gaps(WANT, date(2024, 1, 10), skip_days=7, overlap_days=1) == (
        (date(2024, 1, 9), date(2024, 1, 31)),
    )


def test_forward_empty_fetches_whole_window() -> None:
    assert _forward_gaps(WANT, None, skip_days=7, overlap_days=0) == (WANT,)


def test_last_bar_includes_the_last_stored_bar() -> None:
    assert _last_bar_gaps(WANT, date(2024, 1, 20)) == ((date(2024, 1, 20), date(2024, 1, 31)),)


def test_last_bar_up_to_date_when_latest_at_or_past_end() -> None:
    assert _last_bar_gaps(WANT, date(2024, 1, 31)) == ()


def test_all_cursors_satisfy_the_incremental_strategy_protocol() -> None:
    for cursor in (
        MinMaxRangeGapFill(),
        MaxDateBySymbol(),
        MaxDateGlobal(),
        MaxDateByName(),
        YfMaxDateLastBar(),
        FullWindow(),
    ):
        assert isinstance(cursor, IncrementalStrategy)


# ── DB-backed ───────────────────────────────────────────────────────────────
@pytest.fixture
def seeded_prices(pg_engine: Engine) -> Iterator[Engine]:
    DAILY_PRICES.drop(pg_engine, checkfirst=True)
    DAILY_PRICES.create(pg_engine)
    rows = pd.DataFrame(
        [
            {
                "symbol": "EURUSD",
                "date": d,
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1,
            }
            for d in (date(2024, 1, 10), date(2024, 1, 11), date(2024, 1, 12))
        ]
    )
    rows.to_sql("daily_prices", pg_engine, if_exists="append", index=False)
    try:
        yield pg_engine
    finally:
        with pg_engine.begin() as conn:
            conn.execute(delete(DAILY_PRICES))
        DAILY_PRICES.drop(pg_engine, checkfirst=True)


def test_minmax_cursor_reads_bounds_per_symbol(seeded_prices: Engine) -> None:
    cursor = MinMaxRangeGapFill()
    gaps = cursor.gaps(seeded_prices, DAILY_PRICES, WANT, scope="EURUSD")
    assert gaps == ((date(2024, 1, 1), date(2024, 1, 10)), (date(2024, 1, 13), date(2024, 1, 31)))
    # an unseen symbol fetches the whole window
    assert cursor.gaps(seeded_prices, DAILY_PRICES, WANT, scope="GBPUSD") == (WANT,)


def test_yf_last_bar_cursor_includes_last_stored_bar(seeded_prices: Engine) -> None:
    gaps = YfMaxDateLastBar().gaps(seeded_prices, DAILY_PRICES, WANT, scope="EURUSD")
    assert gaps == ((date(2024, 1, 12), date(2024, 1, 31)),)


@pytest.fixture
def seeded_treasury(pg_engine: Engine) -> Iterator[Engine]:
    FMP_TREASURY_RATES.drop(pg_engine, checkfirst=True)
    FMP_TREASURY_RATES.create(pg_engine)
    pd.DataFrame([{"date": date(2024, 1, 5)}]).to_sql(
        "fmp_treasury_rates", pg_engine, if_exists="append", index=False
    )
    try:
        yield pg_engine
    finally:
        with pg_engine.begin() as conn:
            conn.execute(delete(FMP_TREASURY_RATES))
        FMP_TREASURY_RATES.drop(pg_engine, checkfirst=True)


def test_global_cursor_ignores_symbol_axis(seeded_treasury: Engine) -> None:
    gaps = MaxDateGlobal(skip_days=1).gaps(seeded_treasury, FMP_TREASURY_RATES, WANT, scope=None)
    assert gaps == ((date(2024, 1, 5), date(2024, 1, 31)),)  # forward from global max, no overlap


@pytest.fixture
def seeded_indicators(pg_engine: Engine) -> Iterator[Engine]:
    FMP_ECONOMIC_INDICATORS.drop(pg_engine, checkfirst=True)
    FMP_ECONOMIC_INDICATORS.create(pg_engine)
    pd.DataFrame(
        [
            {"name": "CPI", "date": date(2024, 1, 2), "value": 1.0},
            {"name": "GDP", "date": date(2024, 1, 28), "value": 2.0},
        ]
    ).to_sql("fmp_economic_indicators", pg_engine, if_exists="append", index=False)
    try:
        yield pg_engine
    finally:
        with pg_engine.begin() as conn:
            conn.execute(delete(FMP_ECONOMIC_INDICATORS))
        FMP_ECONOMIC_INDICATORS.drop(pg_engine, checkfirst=True)


def test_by_name_cursor_keys_on_name_with_skip(seeded_indicators: Engine) -> None:
    cursor = MaxDateByName(skip_days=30)
    # CPI latest 2024-01-02; end 31st → 29 days <= 30 → skip
    assert cursor.gaps(seeded_indicators, FMP_ECONOMIC_INDICATORS, WANT, scope="CPI") == ()
    # GDP latest 2024-01-28; a later want is well past the skip window
    later = (date(2024, 1, 1), date(2024, 3, 31))
    assert cursor.gaps(seeded_indicators, FMP_ECONOMIC_INDICATORS, later, scope="GDP") == (
        (date(2024, 1, 28), date(2024, 3, 31)),
    )
