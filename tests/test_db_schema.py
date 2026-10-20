# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 2.1: the market-data schema as one Core MetaData (Req 11.1, 11.2).

The MetaData imports cleanly (no DB connection) and enumerates the module's 37
``public`` tables with their composite primary keys. Core ``Table`` objects only — no ORM.
"""

from __future__ import annotations

from sqlalchemy import Date, Float, MetaData, Table

from facdrone_data.acquisition.schema import TABLES, metadata

EXPECTED_TABLES = frozenset(
    {
        "cftc_cot",
        "contract_specs",
        "daily_prices",
        "duka_order_flow",
        "etf_meta",
        "etf_prices",
        "factor_definitions",
        "factor_returns",
        "fama_french_factors",
        "fmp_analyst_estimates",
        "fmp_analyst_grades",
        "fmp_balance_sheet",
        "fmp_cash_flow",
        "fmp_cot",
        "fmp_cot_analysis",
        "fmp_earnings_calendar",
        "fmp_economic_calendar",
        "fmp_economic_indicators",
        "fmp_etf_holdings",
        "fmp_grade_actions",
        "fmp_income_statement",
        "fmp_insider_trading",
        "fmp_institutional_ownership",
        "fmp_key_metrics",
        "fmp_news",
        "fmp_positions_summary",
        "fmp_sector_performance",
        "fmp_treasury_rates",
        "fred_series",
        "futures_contract_specs",
        "fx_observation",
        "hourly_prices",
        "instrument_metadata_version",
        "intraday_prices",
        "order_flow_daily",
        "price_observation",
        "symbols",
    }
)


def _pk(table: str) -> tuple[str, ...]:
    return tuple(c.name for c in metadata.tables[table].primary_key.columns)


def test_enumerates_the_37_market_data_tables() -> None:
    assert len(metadata.tables) == 37
    assert set(metadata.tables) == EXPECTED_TABLES


def test_tables_alias_is_the_metadata_registry() -> None:
    assert TABLES is metadata.tables


def test_is_core_metadata_not_orm() -> None:
    assert isinstance(metadata, MetaData)
    assert all(isinstance(t, Table) for t in metadata.tables.values())


def test_composite_primary_keys_match_the_live_schema() -> None:
    assert _pk("daily_prices") == ("symbol", "date")
    assert _pk("fmp_balance_sheet") == ("symbol", "date", "period")
    assert _pk("intraday_prices") == ("symbol", "datetime", "interval")
    assert _pk("fred_series") == ("series_id", "date")


def test_column_types_are_mapped() -> None:
    daily = metadata.tables["daily_prices"]
    assert isinstance(daily.c.close.type, Float)
    assert isinstance(daily.c.date.type, Date)
    assert daily.c.symbol.type.python_type is str
