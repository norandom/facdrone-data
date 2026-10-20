# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Per-family incremental cursors (R7.1, R7.2; D5, D6).

Each cursor derives the missing gap(s) for a family from its stored cursor and
returns inclusive ``(start, end)`` ranges; ``()`` means fully covered → the
refresher issues zero provider calls. The pure gap arithmetic (``_minmax_gaps`` /
``_forward_gaps`` / ``_last_bar_gaps``) is separated from the DB read so it is
exhaustively testable offline.

Time-injected: a skip threshold compares the stored latest date to the requested
``end`` (the injected as-of upper bound), never a wall-clock read.

* ``MinMaxRangeGapFill`` — price MIN/MAX two-range (backfill-before-earliest +
  forward-after-latest, boundary bar excluded). NO last-bar refetch.
* ``MaxDateBySymbol`` / ``MaxDateGlobal`` / ``MaxDateByName`` — forward-after-max
  with a per-family skip threshold.
* ``YfMaxDateLastBar`` — yfinance fallback ONLY; ``start = last`` re-fetches the
  last stored bar so partial/incomplete bars get corrected via upsert.
* ``FullWindow`` — the "full upsert" families (income/balance/key_metrics/grades/
  cot/fred/fama_french/etf_meta): the endpoint returns full history, the upsert
  dedups; the cursor always returns the requested window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select

if TYPE_CHECKING:
    from sqlalchemy import Engine, Table

_Range = tuple[date, date]


def _as_date(value: object) -> date | None:
    """Normalize a stored bound to a plain ``date`` (intraday stores ``datetime``;
    ``datetime`` is a ``date`` subclass so it must be narrowed, like SQL ``::date``)."""
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


# ── Pure gap arithmetic (offline-testable) ──────────────────────────────────
def _minmax_gaps(
    want: _Range, earliest: date | None, latest: date | None, *, overlap_days: int = 0
) -> tuple[_Range, ...]:
    """Price gap-fill: backfill (start, earliest) if start < earliest, plus
    forward from the stored tail to ``end`` if it fits; empty table → the whole window.

    With ``overlap_days`` at zero the forward range starts the day after the latest
    stored bar, which is the default request shape and what D5 recorded. Above zero it reaches
    back into bars already held, so a vendor's later correction to a bar it served
    provisionally can land: the upsert overwrites on change, but only for a row it is
    asked to write again, and a gap-only cursor never asks."""
    start, end = want
    if earliest is None or latest is None:
        return ((start, end),)
    ranges: list[_Range] = []
    if start < earliest:
        ranges.append((start, earliest))
    fwd_start = max(start, latest + timedelta(days=1 - overlap_days))
    if fwd_start <= end:
        ranges.append((fwd_start, end))
    return tuple(ranges)


def _forward_gaps(
    want: _Range, latest: date | None, *, skip_days: int, overlap_days: int
) -> tuple[_Range, ...]:
    """Forward-after-max with a staleness skip measured against the requested end
    (time-injected: ``(end - latest).days <= N``, never a wall-clock read)."""
    start, end = want
    if latest is None:
        return ((start, end),)
    if (end - latest).days <= skip_days:
        return ()  # recent enough — no provider call (R7.1)
    lo = max(start, latest - timedelta(days=overlap_days))
    if lo > end:
        return ()
    return ((lo, end),)


def _last_bar_gaps(want: _Range, latest: date | None) -> tuple[_Range, ...]:
    """yfinance ``start = last``: include the last stored bar so a partial bar is
    re-fetched and overwritten (R6.3); up-to-date when ``latest >= end``."""
    start, end = want
    if latest is None:
        return ((start, end),)
    if latest >= end:
        return ()
    return ((latest, end),)


# ── DB reads ────────────────────────────────────────────────────────────────
def _stored_max(
    engine: Engine, table: Table, date_column: str, scope_column: str | None, scope: str | None
) -> date | None:
    stmt = select(func.max(table.c[date_column]))
    if scope is not None and scope_column is not None:
        stmt = stmt.where(table.c[scope_column] == scope)
    with engine.connect() as conn:
        value = conn.execute(stmt).scalar()
    return _as_date(value)


def _stored_minmax(
    engine: Engine, table: Table, date_column: str, scope_column: str | None, scope: str | None
) -> tuple[date | None, date | None]:
    stmt = select(func.min(table.c[date_column]), func.max(table.c[date_column]))
    if scope is not None and scope_column is not None:
        stmt = stmt.where(table.c[scope_column] == scope)
    with engine.connect() as conn:
        lo, hi = conn.execute(stmt).one()
    return (_as_date(lo), _as_date(hi))


# ── Typed cursors (IncrementalStrategy implementations) ─────────────────────
@dataclass(frozen=True)
class MinMaxRangeGapFill:
    """FMP daily/intraday/ETF prices — MIN/MAX two-range gap-fill.

    ``overlap_days`` re-fetches that many calendar days of already-held tail so a bar the
    vendor served provisionally is corrected once it is final. Zero keeps the default shape."""

    date_column: str = "date"
    scope_column: str | None = "symbol"
    overlap_days: int = 0

    def gaps(
        self, engine: Engine, table: Table, want: _Range, *, scope: str | None
    ) -> tuple[_Range, ...]:
        earliest, latest = _stored_minmax(engine, table, self.date_column, self.scope_column, scope)
        return _minmax_gaps(want, earliest, latest, overlap_days=self.overlap_days)


@dataclass(frozen=True)
class MaxDateBySymbol:
    """Symbol-incremental forward-after-max with a per-family skip (news ≤1d,
    cot_analysis ≤7d, etf_holdings ≤1d). ``overlap_days`` re-fetches a small tail
    (news uses 1)."""

    skip_days: int = 0
    overlap_days: int = 0
    date_column: str = "date"
    scope_column: str = "symbol"

    def gaps(
        self, engine: Engine, table: Table, want: _Range, *, scope: str | None
    ) -> tuple[_Range, ...]:
        latest = _stored_max(engine, table, self.date_column, self.scope_column, scope)
        return _forward_gaps(want, latest, skip_days=self.skip_days, overlap_days=self.overlap_days)


@dataclass(frozen=True)
class MaxDateGlobal:
    """Global forward-after-max (no symbol axis) — treasury_rates, economic_calendar,
    sector_performance. Skip ≤1d."""

    skip_days: int = 1
    date_column: str = "date"

    def gaps(
        self, engine: Engine, table: Table, want: _Range, *, scope: str | None
    ) -> tuple[_Range, ...]:
        del scope  # global: no filter
        latest = _stored_max(engine, table, self.date_column, None, None)
        return _forward_gaps(want, latest, skip_days=self.skip_days, overlap_days=0)


@dataclass(frozen=True)
class MaxDateByName:
    """Per-named-series forward-after-max — economic_indicators (keyed on ``name``).
    Skip ≤30d."""

    skip_days: int = 30
    date_column: str = "date"
    scope_column: str = "name"

    def gaps(
        self, engine: Engine, table: Table, want: _Range, *, scope: str | None
    ) -> tuple[_Range, ...]:
        latest = _stored_max(engine, table, self.date_column, self.scope_column, scope)
        return _forward_gaps(want, latest, skip_days=self.skip_days, overlap_days=0)


@dataclass(frozen=True)
class YfMaxDateLastBar:
    """yfinance fallback ONLY — ``start = last`` re-fetches the last stored bar so
    partial/incomplete bars get corrected (Finding 5). NOT used on the FMP path."""

    date_column: str = "date"
    scope_column: str = "symbol"

    def gaps(
        self, engine: Engine, table: Table, want: _Range, *, scope: str | None
    ) -> tuple[_Range, ...]:
        latest = _stored_max(engine, table, self.date_column, self.scope_column, scope)
        return _last_bar_gaps(want, latest)


@dataclass(frozen=True)
class FullWindow:
    """The "full upsert" families — always fetch the requested window; the endpoint
    returns full history and the upsert dedups on the natural key."""

    def gaps(
        self, engine: Engine, table: Table, want: _Range, *, scope: str | None
    ) -> tuple[_Range, ...]:
        del engine, table, scope
        return (want,)
