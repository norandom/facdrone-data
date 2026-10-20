# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 3.1: the DO-UPDATE panel merge carried as a typed MergeStrategy (R6.3,
R7.3, R7.4).

``UpsertPanel`` overwrites revised rows (fundamentals revisions, the daily
partial-bar fix); ``MergePanel`` preserves first-write-wins. Both are typed
Protocol objects selected at the family composition root — never a mode flag. The
conflict target derives strictly from the table primary key (proved on the
``[date]``-only Fama-French table, which has no symbol axis).

DB-backed tests use the scratch PostgreSQL database; the protocol-conformance
and precondition tests run offline.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import Engine, create_engine, delete, select, text

from facdrone_data.acquisition.families.ports import MergeStrategy
from facdrone_data.acquisition.merge import (
    MergePanel,
    MergeReceipt,
    UpsertPanel,
    upsert_panel,
)
from facdrone_data.acquisition.schema import DAILY_PRICES, FAMA_FRENCH_FACTORS


def _prices(symbol: str, start: date, end: date, close_offset: float = 0.0) -> pd.DataFrame:
    rows = []
    day, i = start, 0
    while day <= end:
        close = 100.0 + i + close_offset
        rows.append(
            {
                "symbol": symbol,
                "date": day,
                "open": close - 1.0,
                "high": close + 1.0,
                "low": close - 2.0,
                "close": close,
                "volume": 1000 + i,
            }
        )
        day += timedelta(days=1)
        i += 1
    return pd.DataFrame(rows)


def _ff(rows: list[tuple[date, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"date": d, "mkt_rf": v, "smb": 0.01, "hml": 0.02, "rf": 0.0001} for d, v in rows]
    )


@pytest.fixture
def ff_engine(pg_engine: Engine) -> Iterator[Engine]:
    FAMA_FRENCH_FACTORS.drop(pg_engine, checkfirst=True)
    FAMA_FRENCH_FACTORS.create(pg_engine)
    try:
        yield pg_engine
    finally:
        with pg_engine.begin() as conn:
            conn.execute(delete(FAMA_FRENCH_FACTORS))
        FAMA_FRENCH_FACTORS.drop(pg_engine, checkfirst=True)


# ── offline ───────────────────────────────────────────────────────────────
def test_upsert_and_merge_panels_satisfy_the_merge_strategy_protocol() -> None:
    assert isinstance(UpsertPanel(), MergeStrategy)
    assert isinstance(MergePanel(), MergeStrategy)


def test_upsert_panel_rejects_a_non_postgresql_dialect() -> None:
    engine = create_engine("sqlite://")
    with pytest.raises(NotImplementedError, match="PostgreSQL-only"):
        upsert_panel(engine, DAILY_PRICES, _prices("EURUSD", date(2024, 1, 1), date(2024, 1, 1)))


def test_upsert_panel_rejects_columns_not_in_the_table() -> None:
    engine = create_engine("sqlite://")
    frame = _prices("EURUSD", date(2024, 1, 1), date(2024, 1, 1))
    frame["knowledge_date"] = date(2024, 1, 1)  # not a daily_prices column
    with pytest.raises(ValueError, match="not in daily_prices"):
        upsert_panel(engine, DAILY_PRICES, frame)


# ── DB-backed ───────────────────────────────────────────────────────────────
def test_upsert_panel_inserts_fresh_rows(prices_engine: Engine) -> None:
    receipt = upsert_panel(
        prices_engine, DAILY_PRICES, _prices("EURUSD", date(2024, 1, 1), date(2024, 1, 5))
    )
    assert receipt == MergeReceipt(written=5, conflicts=0, inserted=5, updated=0)


def test_upsert_panel_writes_more_rows_than_one_statement_can_bind(
    prices_engine: Engine,
) -> None:
    """A backfill exceeding PostgreSQL's 65,535 bind parameters (7 columns x 12,000 rows)
    lands in batches inside one transaction; a re-run is idempotent across batches."""
    frame = _prices("ES=F", date(1990, 1, 1), date(1990, 1, 1) + timedelta(days=11_999))
    assert len(frame) * len(frame.columns) > 65_535
    receipt = upsert_panel(prices_engine, DAILY_PRICES, frame)
    assert receipt == MergeReceipt(written=12_000, conflicts=0, inserted=12_000, updated=0)
    again = upsert_panel(prices_engine, DAILY_PRICES, frame)
    assert again == MergeReceipt(written=0, conflicts=0, inserted=0, updated=0)


def test_upsert_panel_overwrites_a_revised_row(prices_engine: Engine) -> None:
    upsert_panel(prices_engine, DAILY_PRICES, _prices("EURUSD", date(2024, 1, 1), date(2024, 1, 1)))
    revised = _prices("EURUSD", date(2024, 1, 1), date(2024, 1, 1), close_offset=900.0)
    receipt = upsert_panel(prices_engine, DAILY_PRICES, revised)
    assert receipt.updated == 1 and receipt.inserted == 0  # revised, not preserved (R6.3)
    stored = pd.read_sql(select(DAILY_PRICES.c.close), prices_engine)
    assert float(stored["close"].iloc[0]) == 1000.0  # the NEW value won (unlike MergePanel)


def test_upsert_panel_idempotent_remerge_makes_zero_updates(prices_engine: Engine) -> None:
    frame = _prices("EURUSD", date(2024, 1, 1), date(2024, 1, 5))
    upsert_panel(prices_engine, DAILY_PRICES, frame)
    again = upsert_panel(prices_engine, DAILY_PRICES, frame)  # byte-identical
    assert again == MergeReceipt(written=0, conflicts=0, inserted=0, updated=0)  # R7.4


def test_upsert_panel_null_overwrites_a_populated_cell(prices_engine: Engine) -> None:
    """A later refresh that NULLs a previously-populated cell must overwrite it
    (the SQL-layer contract is no COALESCE)."""
    upsert_panel(prices_engine, DAILY_PRICES, _prices("EURUSD", date(2024, 1, 1), date(2024, 1, 1)))
    nulled = _prices("EURUSD", date(2024, 1, 1), date(2024, 1, 1))
    nulled.loc[0, "high"] = None
    receipt = upsert_panel(prices_engine, DAILY_PRICES, nulled)
    assert receipt.updated == 1
    stored = pd.read_sql(
        text("SELECT count(*) AS n FROM daily_prices WHERE high IS NULL"), prices_engine
    )
    assert int(stored["n"].iloc[0]) == 1


def test_upsert_panel_conflict_target_is_the_pk_on_a_date_only_table(ff_engine: Engine) -> None:
    """Fama-French has a [date]-only PK (no symbol axis); the conflict target
    must derive from the PK, not assume a symbol column."""
    upsert_panel(ff_engine, FAMA_FRENCH_FACTORS, _ff([(date(2024, 1, 1), 0.5)]))
    receipt = upsert_panel(ff_engine, FAMA_FRENCH_FACTORS, _ff([(date(2024, 1, 1), 0.9)]))
    assert receipt.updated == 1
    stored = pd.read_sql(select(FAMA_FRENCH_FACTORS.c.mkt_rf), ff_engine)
    assert float(stored["mkt_rf"].iloc[0]) == 0.9


def test_merge_panel_strategy_preserves_a_conflicting_row(prices_engine: Engine) -> None:
    strategy: MergeStrategy = MergePanel()
    strategy.merge(
        prices_engine, DAILY_PRICES, _prices("EURUSD", date(2024, 1, 1), date(2024, 1, 1))
    )
    conflicting = _prices("EURUSD", date(2024, 1, 1), date(2024, 1, 1), close_offset=900.0)
    receipt = strategy.merge(prices_engine, DAILY_PRICES, conflicting)
    assert receipt.inserted == 0  # DO NOTHING: nothing newly written
    stored = pd.read_sql(select(DAILY_PRICES.c.close), prices_engine)
    assert float(stored["close"].iloc[0]) == 100.0  # original preserved (first-write-wins)


def test_upsert_panel_strategy_object_overwrites(prices_engine: Engine) -> None:
    strategy: MergeStrategy = UpsertPanel()
    strategy.merge(
        prices_engine, DAILY_PRICES, _prices("EURUSD", date(2024, 1, 1), date(2024, 1, 1))
    )
    conflicting = _prices("EURUSD", date(2024, 1, 1), date(2024, 1, 1), close_offset=900.0)
    receipt = strategy.merge(prices_engine, DAILY_PRICES, conflicting)
    assert receipt.updated == 1
    stored = pd.read_sql(select(DAILY_PRICES.c.close), prices_engine)
    assert float(stored["close"].iloc[0]) == 1000.0  # overwritten
