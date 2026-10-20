# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 2.1: the pure status query layer (R2.1-R2.4, R3.1, R3.2).

Coverage/freshness are derived from the stored panel ONLY — no provider contact
(R2.3). The DB-backed checks use the scratch PostgreSQL database (skip where no
scratch DSN is configured); the registry/providers checks run fully offline.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pandas as pd
import pytest
from sqlalchemy import Engine

from facdrone_data.acquisition.schema import DAILY_PRICES, metadata
from facdrone_data.acquisition.status import DatasetStatus, overview, status_for
from facdrone_data.acquisition.symbol_registry import InMemorySymbolRegistry, SymbolMapping
from facdrone_data.testing.plugin import empty_tables
from facdrone_data_types.enums import AssetClass
from facdrone_data_types.symbols import CanonicalSymbol, Provider

SYMBOL = "EURUSD"
# Three present weekdays, an interior hole, then two more weekdays.
PRESENT = [
    date(2025, 1, 6),
    date(2025, 1, 7),
    date(2025, 1, 8),
    date(2025, 1, 13),
    date(2025, 1, 14),
]


def _daily_rows(symbol: str, days: list[date]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": symbol,
                "date": d,
                "open": 1.0,
                "high": 1.1,
                "low": 0.9,
                "close": 1.0,
                "volume": 100,
            }
            for d in days
        ]
    )


@pytest.fixture
def status_engine(pg_engine: Engine) -> Iterator[Engine]:
    """Full owned schema on the scratch database; daily_prices seeded for SYMBOL.

    The migration owns the tables, so they are truncated before and after, never dropped: a
    drop would leave later tests on the shared embedded server without them."""
    empty_tables(pg_engine, *metadata.sorted_tables)
    try:
        _daily_rows(SYMBOL, PRESENT).to_sql(
            DAILY_PRICES.name, pg_engine, if_exists="append", index=False
        )
        yield pg_engine
    finally:
        empty_tables(pg_engine, *metadata.sorted_tables)


def _by_family(statuses: tuple[DatasetStatus, ...]) -> dict[str, DatasetStatus]:
    return {s.family: s for s in statuses}


def test_status_reports_present_range_last_date_and_interior_gaps(status_engine: Engine) -> None:
    statuses = status_for(status_engine, SYMBOL)
    daily = _by_family(statuses)["refresh_daily_prices"]
    assert daily.present is True
    assert daily.start == date(2025, 1, 6)
    assert daily.end == date(2025, 1, 14)  # last availability date
    # The interior hole (the missing weekdays + the enclosed weekend) is reported.
    assert (date(2025, 1, 9), date(2025, 1, 12)) in daily.gaps


def test_absent_family_is_shown_as_absent_not_omitted(status_engine: Engine) -> None:
    statuses = status_for(status_engine, SYMBOL)
    by_family = _by_family(statuses)
    # duka_order_flow was never seeded → present False, but still in the result.
    assert "refresh_tick_data" in by_family
    duka = by_family["refresh_tick_data"]
    assert duka.present is False
    assert duka.start is None and duka.end is None and duka.gaps == ()


def test_status_order_is_deterministic(status_engine: Engine) -> None:
    first = [s.family for s in status_for(status_engine, SYMBOL)]
    second = [s.family for s in status_for(status_engine, SYMBOL)]
    assert first == second
    # Explicit stable key: (table, family).
    keyed = [(s.table, s.family) for s in status_for(status_engine, SYMBOL)]
    assert keyed == sorted(keyed)


def test_overview_one_row_per_symbol_with_providers_and_span(status_engine: Engine) -> None:
    registry = InMemorySymbolRegistry(
        [
            SymbolMapping(
                CanonicalSymbol(SYMBOL, AssetClass.FOREX, "USD"),
                {Provider.YFINANCE: "EURUSD=X", Provider.FMP: "EURUSD"},
            ),
            SymbolMapping(
                CanonicalSymbol("GBPUSD", AssetClass.FOREX, "USD"),
                {Provider.YFINANCE: "GBPUSD=X"},
            ),
        ]
    )
    rows = overview(status_engine, registry)
    assert [r.symbol for r in rows] == ["EURUSD", "GBPUSD"]  # deterministic order
    eur = rows[0]
    assert eur.providers == ("fmp", "yfinance")  # sorted provider tokens
    assert eur.datasets_present >= 1
    assert eur.coverage_start == date(2025, 1, 6)
    assert eur.coverage_end == date(2025, 1, 14)
    gbp = rows[1]
    assert gbp.providers == ("yfinance",)
    assert gbp.datasets_present == 0  # nothing seeded for GBPUSD


def test_providers_for_returns_sorted_mapped_providers() -> None:
    registry = InMemorySymbolRegistry(
        [
            SymbolMapping(
                CanonicalSymbol(SYMBOL, AssetClass.FOREX, "USD"),
                {Provider.YFINANCE: "EURUSD=X", Provider.FMP: "EURUSD"},
            )
        ]
    )
    assert registry.providers_for(CanonicalSymbol(SYMBOL, AssetClass.FOREX, "USD")) == (
        Provider.FMP,
        Provider.YFINANCE,
    )
    # An unknown symbol maps to no providers (not an error).
    assert (
        registry.providers_for(CanonicalSymbol("XAUUSD", AssetClass.PRECIOUS_METALS, "USD")) == ()
    )
