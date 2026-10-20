# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Idempotent merge node with knowledge-date stamping (task 5.2; R2.3, R2.4,
R2.5, R4.3).

Two write paths, neither re-implementing the foundation:

* **Evidence-class series** are merged through the foundation ``EvidenceLedger``
  (first-write-wins, counted conflicts, bitemporal) — reused as-is at the
  composition root; this module does not duplicate it.
* **Market panels** are merged here by :func:`merge_panel` — an idempotent
  PostgreSQL upsert (``INSERT ... ON CONFLICT DO NOTHING``) over the table's
  primary key, mirroring the ledger's conflict semantics. Re-running a merge
  leaves the panel byte-identical (no duplicate rows, no reordering) and writes
  nothing (R2.3); a row that conflicts on the key preserves the existing row and
  is counted, never overwriting history (R2.4).

``stamp_knowledge_date`` sets a row's ``knowledge_date`` from its publication /
availability time (e.g. a fundamental's filing date), never the event date and
never the wall clock (R4.3), so the point-in-time view reflects what was
actually knowable.

PostgreSQL-only: ``ON CONFLICT`` is part of the idempotency contract, so a
stand-in dialect with different conflict semantics is rejected (matching the
ledger).
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from sqlalchemy import Engine, Table
    from sqlalchemy.dialects.postgresql.dml import Insert as PGInsert


@dataclass(frozen=True)
class MergeReceipt:
    """Row accounting for a panel merge.

    ``written``/``conflicts`` are the back-compat fields. ``inserted``/``updated``
    split the touched rows (data-acquisition D12): under ``UpsertPanel`` they are
    derived from the Postgres ``xmax = 0`` insert/update discriminator and are
    observability-only (excluded from the golden parity comparison).

    * ``MergePanel`` (DO NOTHING): ``written == inserted`` (rows newly written),
      ``updated == 0``, ``conflicts`` = rows that collided and were preserved.
    * ``UpsertPanel`` (DO UPDATE): ``written == inserted + updated``, ``conflicts
      == updated`` (rows that collided on the PK and were revised). A byte-identical
      re-run yields ``inserted == 0, updated == 0`` (R7.4)."""

    written: int
    conflicts: int
    inserted: int = 0
    updated: int = 0


def stamp_knowledge_date(
    frame: pd.DataFrame,
    *,
    availability_column: str,
    knowledge_column: str = "knowledge_date",
    event_column: str | None = None,
) -> pd.DataFrame:
    """Stamp ``knowledge_column`` from the publication/availability time (R4.3).

    The knowledge date is the moment the value became knowable (e.g. a
    fundamental's filing date) — NOT its event date and NOT the wall clock — so
    the PIT filter reflects what was actually knowable. When ``event_column`` is
    given, fail loud if any knowledge date precedes its event date: a value
    cannot be knowable before it occurs (guards a mis-mapped availability column
    from leaking a future row into an earlier as-of read). Returns a new frame."""
    if availability_column not in frame.columns:
        raise KeyError(f"availability column {availability_column!r} not in frame")
    stamped = frame.copy()
    stamped[knowledge_column] = frame[availability_column]
    if event_column is not None:
        if event_column not in frame.columns:
            raise KeyError(f"event column {event_column!r} not in frame")
        # Compare CALENDAR DATES (each in its own frame): a same-day publication
        # is knowable that day, and this sidesteps tz-aware-vs-naive comparison
        # errors and the +UTC-offset same-day false positive.
        knowledge = pd.to_datetime(stamped[knowledge_column]).dt.date
        event = pd.to_datetime(stamped[event_column]).dt.date
        before = knowledge < event
        if bool(before.any()):
            raise ValueError(
                f"knowledge_date precedes event_date for {int(before.sum())} row(s): "
                "a value cannot be knowable before it occurs"
            )
    return stamped


# PostgreSQL's bind-parameter ceiling per statement, less a margin for the statement's own.
_MAX_BIND_PARAMETERS = 60_000


def merge_panel(engine: Engine, table: Table, frame: pd.DataFrame) -> MergeReceipt:
    """Idempotently upsert ``frame`` into ``table`` (first-write-wins, R2.3, R2.4).

    Rows conflicting on the primary key are ignored and counted; existing
    history is never overwritten. Re-running with the same rows writes nothing
    and leaves the panel byte-identical."""
    pk_columns = list(table.primary_key.columns)
    if not pk_columns:
        raise ValueError(f"merge_panel needs a primary key for idempotency; {table.name} has none")
    _validate_columns(frame, table)
    if engine.dialect.name != "postgresql":
        raise NotImplementedError(
            f"merge_panel is PostgreSQL-only (ON CONFLICT semantics are part of the "
            f"idempotency contract), got dialect {engine.dialect.name!r}"
        )
    # Normalize pandas NaN/NaT to None so missing nullable values become SQL NULL,
    # not a stored 'NaN' float (which would corrupt the column and break
    # byte-identity). Legacy numpy dtypes promote missing ints/floats to NaN.
    normalized = frame.astype(object).where(pd.notna(frame), None)
    records = normalized.to_dict(orient="records")
    if not records:
        return MergeReceipt(written=0, conflicts=0)

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    # RETURNING a key column yields exactly the inserted rows, so conflict counts
    # stay precise even when the driver reports rowcount=-1 for this shape.
    statement = pg_insert(table).on_conflict_do_nothing().returning(pk_columns[0])
    written = 0
    with engine.begin() as conn:
        for record in records:
            params = {str(key): value for key, value in record.items()}
            written += len(conn.execute(statement, params).fetchall())
    # DO NOTHING touches only the inserted rows; the rest collided and were
    # preserved (first-write-wins) — counted but never overwritten.
    return MergeReceipt(
        written=written,
        conflicts=len(records) - written,
        inserted=written,
        updated=0,
    )


def upsert_panel(
    engine: Engine,
    table: Table,
    frame: pd.DataFrame,
    *,
    update_columns: tuple[str, ...] | None = None,
) -> MergeReceipt:
    """DO-UPDATE panel upsert (R6.3, R7.3, R7.4).

    A row conflicting on the primary key is **revised** — every non-key column is
    overwritten from ``EXCLUDED`` (no SQL ``COALESCE``: a later refresh that NULLs a
    populated cell must overwrite it). To stay idempotent (R7.4) the DO-UPDATE
    carries a ``WHERE`` that fires only when a value actually changed, so a
    byte-identical re-run performs no update and ``RETURNING`` omits the row —
    ``updated`` stays 0. Insert vs update is split via the Postgres ``xmax = 0``
    discriminator (reliable for the serialized acquisition batches this runs;
    observability-only, D12). The conflict target is strictly the table primary
    key — handles the ``[date]``-only and ``[name,date]`` families with no symbol
    axis. Reuses ``merge_panel``'s column validation and NaN→None normalization.

    Precondition: ``frame`` is deduplicated on the natural key (the normalizer
    does this) — a duplicate PK within one VALUES batch is a Postgres error, by
    design, never silently coalesced."""
    pk_columns = [c.name for c in table.primary_key.columns]
    if not pk_columns:
        raise ValueError(
            f"upsert_panel needs a primary key for the conflict target; {table.name} has none"
        )
    _validate_columns(frame, table)
    if engine.dialect.name != "postgresql":
        raise NotImplementedError(
            f"upsert_panel is PostgreSQL-only (ON CONFLICT DO UPDATE semantics are part "
            f"of the contract), got dialect {engine.dialect.name!r}"
        )
    present = [str(c) for c in frame.columns]
    update_cols = (
        list(update_columns)
        if update_columns is not None
        else [c for c in present if c not in pk_columns]
    )
    normalized = frame.astype(object).where(pd.notna(frame), None)
    records = normalized.to_dict(orient="records")
    if not records:
        return MergeReceipt(written=0, conflicts=0, inserted=0, updated=0)

    from sqlalchemy import Boolean, literal_column

    # PostgreSQL binds at most 65,535 parameters per statement; one VALUES row costs one
    # per column. A backfill of a new series (70,000 15-minute bars on 2026-09-24) exceeds
    # that in one statement, so the rows go in batches inside the ONE transaction below:
    # the write stays atomic, exactly as before, whatever its size.
    batch_rows = max(1, _MAX_BIND_PARAMETERS // max(1, len(present)))
    inserted_flag = literal_column("(xmax = 0)", type_=Boolean())
    inserted = updated = 0
    with engine.begin() as conn:
        for start in range(0, len(records), batch_rows):
            conflict_stmt = _conflict_statement(
                table, records[start : start + batch_rows], pk_columns, update_cols
            )
            batch_inserted, batch_updated = _count_returning(
                conn.execute(conflict_stmt.returning(inserted_flag)).fetchall()
            )
            inserted += batch_inserted
            updated += batch_updated
    return MergeReceipt(
        written=inserted + updated,
        conflicts=updated,
        inserted=inserted,
        updated=updated,
    )


def _conflict_statement(
    table: Table,
    rows: Sequence[Mapping[Hashable, object]],
    pk_columns: list[str],
    update_cols: list[str],
) -> PGInsert:
    from sqlalchemy import or_
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    insert_stmt = pg_insert(table).values(rows)
    excluded = insert_stmt.excluded
    if update_cols:
        changed = or_(*(table.c[c].is_distinct_from(excluded[c]) for c in update_cols))
        return insert_stmt.on_conflict_do_update(
            index_elements=pk_columns,
            set_={c: excluded[c] for c in update_cols},
            where=changed,
        )
    # A PK-only table has nothing to revise; fall back to first-write-wins.
    return insert_stmt.on_conflict_do_nothing(index_elements=pk_columns)


def _count_returning(rows: Iterable[Iterable[object]]) -> tuple[int, int]:
    """Count xmax = 0 as inserted and nonzero as updated."""
    inserted = updated = 0
    for (is_insert,) in rows:
        if is_insert:
            inserted += 1
        else:
            updated += 1
    return inserted, updated


@dataclass(frozen=True)
class UpsertPanel:
    """MergeStrategy: revise-on-conflict (DO UPDATE) for revisable market/fundamental
    panels. A variant is a class, not a flag (mirrors ``freshness.FreshnessPolicy``)."""

    def merge(self, engine: Engine, table: Table, frame: pd.DataFrame) -> MergeReceipt:
        return upsert_panel(engine, table, frame)


@dataclass(frozen=True)
class MergePanel:
    """MergeStrategy: first-write-wins (DO NOTHING) for any immutable family."""

    def merge(self, engine: Engine, table: Table, frame: pd.DataFrame) -> MergeReceipt:
        return merge_panel(engine, table, frame)


def _validate_columns(frame: pd.DataFrame, table: Table) -> None:
    """Fail loud if the frame does not project onto the table (R1.3 spirit).

    Unknown columns would be silently dropped (losing data such as a stamped
    knowledge_date); a missing required column would NULL-violate mid-transaction.
    Both become a clear precondition error instead."""
    table_columns = {c.name for c in table.columns}
    unknown = set(map(str, frame.columns)) - table_columns
    if unknown:
        raise ValueError(f"frame has columns not in {table.name}: {sorted(unknown)}")
    # The full primary key must be present: it is how the upsert detects a
    # conflict, and a missing key column would NULL-violate mid-transaction.
    required = {c.name for c in table.primary_key.columns}
    missing = required - set(map(str, frame.columns))
    if missing:
        raise ValueError(
            f"frame missing required columns of {table.name} (primary key): {sorted(missing)}"
        )
