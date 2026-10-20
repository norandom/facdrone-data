# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Pure status query layer for ``data status`` / ``symbol status`` (R2, R3).

Derives per-family coverage and freshness from the stored panel ONLY — it opens
no provider socket (R2.3). For each registered acquisition family it reads the
``MIN``/``MAX`` of the family's availability column (filtered by symbol when the
family is symbol-keyed) and derives interior gaps with the pure
``library.coverage`` math, exactly as the acquisition node does. Iterating the
registry — not the present rows — makes an absent family show as **absent**
rather than vanish from the report (R2.4).

pipelines tier: imports only domain/library/pipelines (the import-linter
``pipelines ↛ shells`` wall holds); the Rich renderer that turns these value
objects into terminal tables lives in the CLI shell.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Protocol

import pandas as pd
from sqlalchemy import Date, DateTime, Table, func, select

from facdrone_data.acquisition.acquisition import derive_coverage
from facdrone_data.acquisition.families.registry import download_families
from facdrone_data.coverage import Coverage, missing_ranges

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy import Engine

    from facdrone_data.acquisition.families.ports import FamilyPort
    from facdrone_data_types.symbols import CanonicalSymbol, Provider


class StatusSymbolRegistry(Protocol):
    """Read projection the offline status overview needs from a symbol registry."""

    def all(self) -> tuple[CanonicalSymbol, ...]: ...

    def providers_for(self, sym: CanonicalSymbol) -> tuple[Provider, ...]: ...


_DateRange = tuple[date, date]


@dataclass(frozen=True)
class _Coverage:
    """The per-family coverage facts, named (vs a positional 4-tuple)."""

    present: bool
    start: date | None
    end: date | None
    gaps: tuple[_DateRange, ...]


@dataclass(frozen=True)
class DatasetStatus:
    """One dataset family's coverage for a symbol, derived from the panel.

    ``present`` is False for a family the symbol has no rows in (shown, not
    omitted — R2.4). ``start``/``end`` and ``gaps`` are populated only for
    date-keyed families (the listed examples); ``symbol_scoped`` is False for a
    shared/global family (e.g. ``fred_series``) that is not filtered by symbol."""

    family: str
    table: str
    provider: str
    symbol_scoped: bool
    present: bool
    start: date | None
    end: date | None
    gaps: tuple[_DateRange, ...]


@dataclass(frozen=True)
class SymbolOverview:
    """A one-glance cross-symbol summary row (R3.1, R3.2)."""

    symbol: str
    providers: tuple[str, ...]
    datasets_present: int
    datasets_total: int
    coverage_start: date | None
    coverage_end: date | None
    last_available: date | None


def _ordered(families: Sequence[FamilyPort]) -> list[FamilyPort]:
    """A deterministic family order for stable output (R3.3): (table, writer)."""
    return sorted(families, key=lambda p: (p.target_table.name, p.writer))


def _is_date_column(table: Table, column_name: str) -> bool:
    return isinstance(table.c[column_name].type, (Date, DateTime))


def _present_dates(engine: Engine, port: FamilyPort, symbol: str | None) -> list[date]:
    """The distinct availability dates for ``(family, symbol)`` from the panel."""
    table = port.target_table
    column = table.c[port.availability_column]
    stmt = select(column)
    if symbol is not None and "symbol" in table.c:
        stmt = stmt.where(table.c.symbol == symbol)
    frame = pd.read_sql(stmt, engine)
    if frame.empty:
        return []
    return sorted({d for d in pd.to_datetime(frame.iloc[:, 0]).dt.date if not pd.isna(d)})


def _row_count(engine: Engine, port: FamilyPort, symbol: str | None) -> int:
    """Row count for a non-date family (presence without a date span)."""
    table = port.target_table
    stmt = select(func.count()).select_from(table)
    if symbol is not None and "symbol" in table.c:
        stmt = stmt.where(table.c.symbol == symbol)
    with engine.connect() as conn:
        return int(conn.execute(stmt).scalar() or 0)


def _family_coverage(engine: Engine, port: FamilyPort, symbol: str | None) -> _Coverage:
    """The date-span coverage for ``(family, symbol)``, or presence-only for a
    non-date family (the availability column is a ``year``/``symbol``)."""
    table = port.target_table
    if not _is_date_column(table, port.availability_column):
        present = _row_count(engine, port, symbol) > 0
        return _Coverage(present=present, start=None, end=None, gaps=())
    dates = _present_dates(engine, port, symbol)
    if not dates:
        return _Coverage(present=False, start=None, end=None, gaps=())
    span = (dates[0], dates[-1])
    coverage: Coverage = derive_coverage(pd.DataFrame({"symbol": "_", "date": dates}), "_")
    return _Coverage(present=True, start=span[0], end=span[1], gaps=missing_ranges(coverage, span))


def _status_for_family(engine: Engine, port: FamilyPort, symbol: str | None) -> DatasetStatus:
    cov = _family_coverage(engine, port, symbol)
    return DatasetStatus(
        family=port.writer,
        table=port.target_table.name,
        provider=port.provider.value,
        symbol_scoped="symbol" in port.target_table.c,
        present=cov.present,
        start=cov.start,
        end=cov.end,
        gaps=cov.gaps,
    )


def status_for(
    engine: Engine, symbol: str, *, families: Sequence[FamilyPort] | None = None
) -> tuple[DatasetStatus, ...]:
    """Per-family coverage/freshness for ``symbol`` from the panel (R2.1-R2.4).

    Iterates THE registered families (no provider contact), so every family —
    present or absent — appears, in a deterministic ``(table, writer)`` order."""
    ports = _ordered(list(families) if families is not None else download_families().families())
    return tuple(_status_for_family(engine, port, symbol) for port in ports)


def _symbol_overview(
    engine: Engine,
    sym: CanonicalSymbol,
    registry: StatusSymbolRegistry,
    symbol_families: Sequence[FamilyPort],
) -> SymbolOverview:
    statuses = [_status_for_family(engine, port, sym.canonical_id) for port in symbol_families]
    present = [s for s in statuses if s.present]
    starts = [s.start for s in present if s.start is not None]
    ends = [s.end for s in present if s.end is not None]
    return SymbolOverview(
        symbol=sym.canonical_id,
        providers=tuple(p.value for p in registry.providers_for(sym)),
        datasets_present=len(present),
        datasets_total=len(symbol_families),
        coverage_start=min(starts) if starts else None,
        coverage_end=max(ends) if ends else None,
        last_available=max(ends) if ends else None,
    )


def overview(
    engine: Engine,
    registry: StatusSymbolRegistry,
    *,
    families: Sequence[FamilyPort] | None = None,
) -> tuple[SymbolOverview, ...]:
    """One summary row per registry symbol, ordered by canonical id (R3.1-R3.3).

    Spans and staleness are summarized only over the symbol-keyed families (a
    shared series like ``fred_series`` is not attributable to one symbol)."""
    ports = list(families) if families is not None else download_families().families()
    symbol_families = _ordered([p for p in ports if "symbol" in p.target_table.c])
    symbols = sorted(registry.all(), key=lambda s: s.canonical_id)
    return tuple(_symbol_overview(engine, sym, registry, symbol_families) for sym in symbols)
