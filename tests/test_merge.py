# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 5.2: idempotent merge node + knowledge-date stamping (Req 2.3, 2.4, 4.3).

The DB-backed tests use the scratch PostgreSQL database (skips where no scratch
DSN is configured); the stamping and precondition tests run fully offline.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

import pandas as pd
import pytest
from sqlalchemy import Column, Engine, MetaData, String, Table, create_engine, delete, select

from facdrone_data.acquisition.merge import MergeReceipt, merge_panel, stamp_knowledge_date
from facdrone_data.acquisition.schema import DAILY_PRICES


def _panel_frame(symbol: str, start: date, end: date, close_offset: float = 0.0) -> pd.DataFrame:
    rows = []
    day = start
    i = 0
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
    return pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume"])


def _read(engine: Engine) -> pd.DataFrame:
    stmt = select(DAILY_PRICES).order_by(DAILY_PRICES.c.symbol, DAILY_PRICES.c.date)
    return pd.read_sql(stmt, engine)


@pytest.fixture
def panel_engine(pg_engine: Engine) -> Iterator[Engine]:
    """A fresh, empty daily_prices table on the scratch database per test."""
    DAILY_PRICES.drop(pg_engine, checkfirst=True)
    DAILY_PRICES.create(pg_engine)
    try:
        yield pg_engine
    finally:
        with pg_engine.begin() as conn:
            conn.execute(delete(DAILY_PRICES))
        DAILY_PRICES.drop(pg_engine, checkfirst=True)


# ── offline (no DB) ───────────────────────────────────────────────────────


def test_stamp_knowledge_date_uses_publication_time_not_event_or_wallclock() -> None:
    frame = pd.DataFrame(
        {"event_date": [date(2024, 1, 1)], "filing_date": [date(2024, 3, 15)], "value": [1.0]}
    )
    out = stamp_knowledge_date(frame, availability_column="filing_date")
    assert list(out["knowledge_date"]) == [date(2024, 3, 15)]  # filing time, not event
    assert "knowledge_date" not in frame.columns  # original frame untouched (copy)


def test_stamp_knowledge_date_missing_column_raises() -> None:
    with pytest.raises(KeyError, match="filing_date"):
        stamp_knowledge_date(pd.DataFrame({"x": [1]}), availability_column="filing_date")


def test_merge_rejects_a_non_postgresql_dialect() -> None:
    engine = create_engine("sqlite://")
    with pytest.raises(NotImplementedError, match="PostgreSQL-only"):
        merge_panel(
            engine, DAILY_PRICES, _panel_frame("EURUSD", date(2024, 1, 1), date(2024, 1, 1))
        )


def test_merge_requires_a_primary_key() -> None:
    engine = create_engine("sqlite://")
    keyless = Table("keyless_log", MetaData(), Column("run_id", String))  # a log with no PK
    with pytest.raises(ValueError, match="primary key"):
        merge_panel(engine, keyless, pd.DataFrame([{"run_id": "x"}]))


def test_merge_rejects_columns_not_in_the_table() -> None:
    engine = create_engine("sqlite://")
    frame = _panel_frame("EURUSD", date(2024, 1, 1), date(2024, 1, 1))
    frame["knowledge_date"] = date(2024, 1, 1)  # not a daily_prices column → must not silently drop
    with pytest.raises(ValueError, match="not in daily_prices"):
        merge_panel(engine, DAILY_PRICES, frame)


def test_merge_rejects_a_frame_missing_a_required_key_column() -> None:
    engine = create_engine("sqlite://")
    frame = _panel_frame("EURUSD", date(2024, 1, 1), date(2024, 1, 1)).drop(columns=["symbol"])
    with pytest.raises(ValueError, match="missing required columns"):
        merge_panel(engine, DAILY_PRICES, frame)


def test_stamp_knowledge_date_rejects_knowledge_before_event() -> None:
    # A mis-mapped availability column that pre-dates the event is a lookahead leak.
    frame = pd.DataFrame(
        {"event_date": [date(2024, 1, 2)], "filing_date": [date(2023, 12, 1)], "value": [1.0]}
    )
    with pytest.raises(ValueError, match="precedes event_date"):
        stamp_knowledge_date(frame, availability_column="filing_date", event_column="event_date")


def test_stamp_knowledge_date_accepts_publication_after_event() -> None:
    frame = pd.DataFrame(
        {"event_date": [date(2024, 1, 1)], "filing_date": [date(2024, 3, 15)], "value": [1.0]}
    )
    out = stamp_knowledge_date(frame, availability_column="filing_date", event_column="event_date")
    assert list(out["knowledge_date"]) == [date(2024, 3, 15)]


# ── DB-backed (scratch PostgreSQL) ──────────────────────────────────────


def test_second_identical_merge_is_byte_identical_with_zero_writes(panel_engine: Engine) -> None:
    frame = _panel_frame("EURUSD", date(2024, 1, 1), date(2024, 1, 5))  # 5 rows
    first = merge_panel(panel_engine, DAILY_PRICES, frame)
    assert first == MergeReceipt(written=5, conflicts=0, inserted=5, updated=0)
    before = _read(panel_engine)

    second = merge_panel(panel_engine, DAILY_PRICES, frame)
    assert second == MergeReceipt(
        written=0, conflicts=5, inserted=0, updated=0
    )  # zero new writes (R2.3)
    after = _read(panel_engine)
    pd.testing.assert_frame_equal(before, after)  # panel byte-identical


def test_key_conflict_preserves_existing_row_and_counts(panel_engine: Engine) -> None:
    merge_panel(
        panel_engine, DAILY_PRICES, _panel_frame("EURUSD", date(2024, 1, 1), date(2024, 1, 1))
    )
    conflicting = _panel_frame("EURUSD", date(2024, 1, 1), date(2024, 1, 1), close_offset=900.0)

    receipt = merge_panel(panel_engine, DAILY_PRICES, conflicting)
    assert receipt == MergeReceipt(
        written=0, conflicts=1, inserted=0, updated=0
    )  # history not overwritten (R2.4)
    stored = _read(panel_engine)
    assert len(stored) == 1
    assert float(stored["close"].iloc[0]) == 100.0  # original value preserved, not 1000.0


def test_partial_overlap_inserts_only_new_rows(panel_engine: Engine) -> None:
    merge_panel(
        panel_engine, DAILY_PRICES, _panel_frame("EURUSD", date(2024, 1, 1), date(2024, 1, 3))
    )
    # days 1-3 already present; merging 1-5 inserts only days 4-5.
    receipt = merge_panel(
        panel_engine, DAILY_PRICES, _panel_frame("EURUSD", date(2024, 1, 1), date(2024, 1, 5))
    )
    assert receipt == MergeReceipt(written=2, conflicts=3, inserted=2, updated=0)
    assert len(_read(panel_engine)) == 5


def test_missing_nullable_value_is_stored_as_sql_null_not_nan(panel_engine: Engine) -> None:
    from sqlalchemy import text

    frame = _panel_frame("EURUSD", date(2024, 1, 1), date(2024, 1, 2))  # 2 rows
    frame.loc[1, "close"] = None  # numpy promotion makes this a float NaN
    merge_panel(panel_engine, DAILY_PRICES, frame)
    with panel_engine.connect() as conn:
        n_null = conn.execute(
            text("SELECT count(*) FROM daily_prices WHERE close IS NULL")
        ).scalar()
        n_nan = conn.execute(
            text("SELECT count(*) FROM daily_prices WHERE close = 'NaN'::float8")
        ).scalar()
    assert n_null == 1  # stored as SQL NULL...
    assert n_nan == 0  # ...not a NaN float


def test_nat_in_a_pk_column_fails_loud(panel_engine: Engine) -> None:
    from sqlalchemy.exc import IntegrityError

    frame = _panel_frame("EURUSD", date(2024, 1, 1), date(2024, 1, 1))
    frame.loc[0, "date"] = pd.NaT  # NaT in the PK → NULL → must be rejected, not silent
    with pytest.raises(IntegrityError):
        merge_panel(panel_engine, DAILY_PRICES, frame)


def test_empty_frame_is_a_noop(panel_engine: Engine) -> None:
    assert merge_panel(
        panel_engine, DAILY_PRICES, _panel_frame("EURUSD", date(2024, 1, 5), date(2024, 1, 1))
    ) == MergeReceipt(0, 0)
