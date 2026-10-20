# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 2.3: parameter-bound Core queries and the drift check (Req 11.5, 4.1).

A time-bounded read renders as a BOUND statement (``<col> <= :as_of``) with no
inlined date literal — the lookahead read contract is enforceable and
injection-safe. The drift check reports each kind of divergence between a
declared ``MetaData`` and a live snapshot.
"""

from __future__ import annotations

from facdrone_data.acquisition.queries import as_of_select, schema_drift
from facdrone_data.acquisition.schema import metadata


def test_as_of_select_binds_the_date_and_inlines_no_literal() -> None:
    stmt = as_of_select(metadata.tables["daily_prices"], time_column="date")
    compiled = stmt.compile()
    sql = str(compiled)
    assert "<= :as_of" in sql  # bound parameter, not an inlined date
    assert "as_of" in compiled.params and compiled.params["as_of"] is None
    assert "2024" not in sql and "'20" not in sql  # no date literal anywhere


def test_as_of_select_works_on_a_fundamentals_filing_date() -> None:
    # Fundamentals are point-in-time on filing_date (publication time), not event date.
    stmt = as_of_select(metadata.tables["fmp_income_statement"], time_column="filing_date")
    assert "fmp_income_statement.filing_date <= :as_of" in str(stmt.compile())


def test_as_of_param_name_is_configurable() -> None:
    stmt = as_of_select(metadata.tables["daily_prices"], time_column="date", as_of_param="cutoff")
    assert "<= :cutoff" in str(stmt.compile())


def test_as_of_select_supports_a_revisioned_knowledge_time() -> None:
    # The revisioned observations carry when each fact became known — the canonical PIT
    # read contract (R4.1) bounds on that column.
    stmt = as_of_select(metadata.tables["price_observation"], time_column="known_at")
    assert "price_observation.known_at <= :as_of" in str(stmt.compile())


def test_schema_drift_detects_injected_divergence() -> None:
    # A snapshot taken from the MetaData itself reports no drift...
    live = set(metadata.tables)
    columns = {n: {c.name for c in metadata.tables[n].columns} for n in live}
    pks = {n: tuple(c.name for c in metadata.tables[n].primary_key.columns) for n in live}
    assert schema_drift(metadata, live, columns, pks) == []
    # ...and each kind of divergence is caught.
    assert any(
        "table drift" in p for p in schema_drift(metadata, live - {"daily_prices"}, columns, pks)
    )
    renamed = dict(columns)
    renamed["daily_prices"] = (columns["daily_prices"] - {"close"}) | {"closing"}
    assert any(
        "daily_prices: column drift" in p for p in schema_drift(metadata, live, renamed, pks)
    )
    reordered = dict(pks)
    reordered["daily_prices"] = ("date", "symbol")  # composite PK out of order
    assert any(
        "daily_prices: pk drift" in p for p in schema_drift(metadata, live, columns, reordered)
    )
