# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Shared, quirk-free normalizer mechanics (R6.1, R6.2; D11).

The per-family normalizer contract is: raw provider payload (a DataFrame) → a
frame whose columns are exactly the target table's columns, with write-time
correction applied **before** persistence (never at read time). These helpers are
the reusable, quirk-free parts; every provider quirk stays in the per-family normalizer.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import Table


def table_columns(table: Table) -> list[str]:
    return [c.name for c in table.columns]


def project_to_table(frame: pd.DataFrame, table: Table) -> pd.DataFrame:
    """Return ``frame`` with exactly ``table``'s columns, in table order.

    Missing non-key columns are created as NULL (the family fetched a subset);
    extra columns are dropped. Fail loud only on a missing primary-key column —
    a NULL key would violate the merge mid-transaction."""
    columns = table_columns(table)
    pk = {c.name for c in table.primary_key.columns}
    out = pd.DataFrame(index=frame.index)
    for column in columns:
        if column in frame.columns:
            out[column] = frame[column]
        elif column in pk:
            raise ValueError(f"normalizer output missing primary-key column {column!r}")
        else:
            out[column] = None
    return out


def coerce_date(series: pd.Series) -> pd.Series:
    """Normalize a date-like column to plain ``datetime.date`` (``.dt.date``)."""
    return pd.to_datetime(series).dt.date


def stamp_updated_at(
    frame: pd.DataFrame, table: Table, as_of: datetime, *, column: str = "_updated_at"
) -> pd.DataFrame:
    """Inject the injected-clock ``_updated_at`` only for tables that declare it
    (time-as-injected-data; never ``pd.Timestamp.now()``). A no-op otherwise."""
    if column not in table_columns(table):
        return frame
    out = frame.copy()
    out[column] = as_of
    return out


def dedup_on_primary_key(frame: pd.DataFrame, table: Table) -> pd.DataFrame:
    """Dedup on the table's declared primary key, keeping the last occurrence.

    ``upsert_panel`` states this as a precondition: a duplicate primary key inside one
    VALUES batch is a Postgres ``CardinalityViolation``, deliberately not coalesced at the
    merge layer. Providers that return several records per key on one day — an analyst
    revising a grade, a timestamp truncated to its date — therefore have to be reduced
    here. The key is read from the table declaration so it cannot drift from the schema.
    """
    key = [c.name for c in table.primary_key.columns]
    if not key or frame.empty:
        return frame
    return frame.drop_duplicates(subset=key, keep="last").reset_index(drop=True)


def drop_null_and_dedup(
    frame: pd.DataFrame, *, null_subset: Sequence[str], key: Sequence[str]
) -> pd.DataFrame:
    """Drop rows null in ``null_subset`` (e.g. null close) and dedup on the natural
    key, keeping the last occurrence (R6.2). Returns a fresh 0-indexed frame."""
    cleaned = frame.dropna(subset=list(null_subset))
    cleaned = cleaned.drop_duplicates(subset=list(key), keep="last")
    return cleaned.reset_index(drop=True)
