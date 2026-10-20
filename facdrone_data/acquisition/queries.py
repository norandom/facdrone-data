# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Parameter-bound Core query construction and the schema drift check (task 2.3; R11.5, R4.1).

Time-bounded reads are built with composable, PARAMETER-BOUND SQLAlchemy Core
constructs — never string interpolation of dates. That makes the lookahead read
contract (``<time_column> <= :as_of``, R4.1) enforceable and injection-safe
(R11.5): the as-of value travels as a bound parameter at execution, never inside
the SQL text. ``schema_drift`` compares a declared ``MetaData`` with a live snapshot.

Builds statements over the owned schema; no quant math, no IO (executing the statement is
the caller's job).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import MetaData, Select, Table, bindparam, select

AS_OF_PARAM = "as_of"


def as_of_select(table: Table, *, time_column: str, as_of_param: str = AS_OF_PARAM) -> Select[Any]:
    """A point-in-time read: ``SELECT * FROM table WHERE <time_column> <= :as_of``.

    The bound parameter (default name ``as_of``) is supplied at execution, so no
    date literal is ever inlined into the SQL — the read contract is enforceable
    and injection-safe. ``time_column`` is REQUIRED and chosen per table:
    ``knowledge_date`` for evidence-class data, ``filing_date`` for fundamentals
    (publication time), or ``date`` for plain panels (the owned panels carry no
    ``knowledge_date`` column, so there is no safe default)."""
    column = table.c[time_column]
    return select(table).where(column <= bindparam(as_of_param))


def schema_drift(
    declared: MetaData,
    live_tables: set[str],
    live_columns: Mapping[str, set[str]],
    live_pks: Mapping[str, tuple[str, ...]],
) -> list[str]:
    """Return human-readable drift between ``declared`` and a live snapshot (R11.3).

    Empty list ⇔ the live database matches the MetaData (same tables, same
    columns per table, same primary-key columns in order). Pure: the live
    snapshot is passed in, so the comparison is testable without a database."""
    problems: list[str] = []
    declared_tables = set(declared.tables)
    if live_tables != declared_tables:
        problems.append(
            f"table drift — only-live={sorted(live_tables - declared_tables)} "
            f"only-declared={sorted(declared_tables - live_tables)}"
        )
    for name in sorted(declared_tables & live_tables):
        table = declared.tables[name]
        declared_cols = {c.name for c in table.columns}
        if live_columns.get(name, set()) != declared_cols:
            diff = live_columns.get(name, set()) ^ declared_cols
            problems.append(f"{name}: column drift {sorted(diff)}")
        declared_pk = tuple(c.name for c in table.primary_key.columns)
        if live_pks.get(name, ()) != declared_pk:
            problems.append(
                f"{name}: pk drift live={live_pks.get(name, ())} declared={declared_pk}"
            )
    return problems
