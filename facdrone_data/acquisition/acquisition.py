# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Incremental acquisition node (task 5.1; R1.2, R1.3, R1.4, R2.1, R2.2).

For each canonical symbol: resolve it to a provider symbol through the injected
``SymbolRegistry``, DERIVE coverage from the stored canonical panel (per-symbol
MIN/MAX + an interior gap scan — never a separately-maintained index that could
drift, and unlike a plain ``MAX(date)`` cursor it sees interior holes), compute
the missing sub-ranges via the pure ``library.coverage`` math, fetch ONLY those gaps through the
injected ``MarketDataClient``, and schema-validate every fetched frame.

A fully-covered request issues zero provider calls (R2.1); a partially-covered
one fetches only the gap (R2.2). This node returns the fetched frames plus an
audit of what was fetched vs skipped — it does NOT persist (the idempotent
merge is task 5.2; catalog persistence is task 11). Time is the injected
``want`` range, never a wall-clock read; callers depend only on the
Protocols, never on a concrete adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from facdrone_data.acquisition.schemas import (
    DATASET_SCHEMAS,
    DatasetSchema,
    SchemaViolationError,
    validate_frame,
)
from facdrone_data.coverage import Coverage, missing_ranges
from facdrone_data_types.ports import FetchRequest, MarketDataClient, SymbolRegistry
from facdrone_data_types.symbols import CanonicalSymbol

_Range = tuple[str, date, date]


@dataclass(frozen=True)
class AcquireDeps:
    """The injected boundary collaborators (bundled to keep ``acquire`` ≤5 args)."""

    client: MarketDataClient
    registry: SymbolRegistry


@dataclass(frozen=True)
class AcquireAudit:
    """Per-run record of already-present, fetched, and skipped ranges (R2.5).

    ``present`` is the symbol's full stored coverage; ``fetched`` are the gap
    sub-ranges actually downloaded; ``skipped`` are the covered sub-ranges of
    the request that were NOT downloaded. ``fetched`` and ``skipped`` tile the
    requested range. Observability only — the panel remains the authority for
    coverage."""

    present: tuple[_Range, ...]
    fetched: tuple[_Range, ...]
    skipped: tuple[_Range, ...]


@dataclass(frozen=True)
class AcquireResult:
    """Schema-validated fetched frames plus the run audit (no persistence here)."""

    frames: tuple[pd.DataFrame, ...]
    audit: AcquireAudit


def derive_coverage(
    panel: pd.DataFrame,
    symbol: str,
    *,
    date_column: str = "date",
    symbol_column: str = "symbol",
) -> Coverage:
    """Derive a symbol's present contiguous ranges FROM the panel (R2.1, R2.2).

    Scans the symbol's stored dates and starts a new contiguous run only when a
    TRADING day is missing between two present dates, so interior holes are
    representable (a plain ``MAX(date)`` cursor would miss them) while weekends are NOT
    treated as gaps — otherwise a fully-covered weekday-only instrument would
    re-fetch the closed weekend on every run (breaking R2.1). Never reads a side
    index. A panel lacking the symbol column yields empty coverage (fail-safe:
    re-acquire rather than mis-attribute the whole panel to one symbol)."""
    if symbol_column not in panel.columns:
        return Coverage(())
    rows = panel[panel[symbol_column] == symbol]
    if rows.empty:
        return Coverage(())
    dates = sorted({d for d in pd.to_datetime(rows[date_column]).dt.date})
    runs: list[tuple[date, date]] = []
    run_start = previous = dates[0]
    for current in dates[1:]:
        if _has_interior_gap(previous, current):
            runs.append((run_start, previous))
            run_start = current
        previous = current
    runs.append((run_start, previous))
    return Coverage(tuple(runs))


def _has_interior_gap(previous: date, current: date) -> bool:
    """True if a business day lies strictly between two present dates.

    A Fri→Mon step is contiguous (no business day in the closed weekend); a
    missing Tue is a real hole. Holiday-aware contiguity needs a trading
    calendar (future); business-day adjacency fixes the dominant weekend case."""
    return bool(np.busday_count(previous + timedelta(days=1), current) > 0)


def acquire(
    symbols: tuple[CanonicalSymbol, ...],
    dataset: str,
    want: tuple[date, date],
    deps: AcquireDeps,
    panel: pd.DataFrame,
) -> AcquireResult:
    """Acquire ``dataset`` for ``symbols`` over ``want``, fetching only gaps."""
    if dataset not in DATASET_SCHEMAS:
        raise SchemaViolationError(f"no declared schema for dataset {dataset!r}")
    schema = DATASET_SCHEMAS[dataset]
    frames: list[pd.DataFrame] = []
    present: list[_Range] = []
    fetched: list[_Range] = []
    skipped: list[_Range] = []
    for sym in symbols:
        sym_frames, sym_present, sym_fetched, sym_skipped = _acquire_symbol(
            sym, want, deps, panel, schema
        )
        frames.extend(sym_frames)
        present.extend(sym_present)
        fetched.extend(sym_fetched)
        skipped.extend(sym_skipped)
    return AcquireResult(
        tuple(frames), AcquireAudit(tuple(present), tuple(fetched), tuple(skipped))
    )


def _acquire_symbol(
    sym: CanonicalSymbol,
    want: tuple[date, date],
    deps: AcquireDeps,
    panel: pd.DataFrame,
    schema: DatasetSchema,
) -> tuple[list[pd.DataFrame], list[_Range], list[_Range], list[_Range]]:
    # Resolution is fail-loud (SymbolConfigError) BEFORE any download.
    provider_symbol = deps.registry.to_provider(sym, deps.client.provider)
    coverage = derive_coverage(panel, sym.canonical_id)
    gaps = missing_ranges(coverage, want)
    # The covered sub-ranges of `want` are its complement of the gaps; together
    # they tile `want`, so the audit accounts for the whole request.
    covered = missing_ranges(Coverage(present=gaps), want)
    present = [(provider_symbol, a, b) for a, b in coverage.present]
    skipped = [(provider_symbol, a, b) for a, b in covered]
    frames: list[pd.DataFrame] = []
    fetched: list[_Range] = []
    for gap_start, gap_end in gaps:
        raw = deps.client.fetch(FetchRequest(provider_symbol, schema.name, gap_start, gap_end))
        validated = validate_frame(raw, schema)
        # Re-key the provider symbol to the canonical id so the stored panel is
        # canonical (R3.2: symbols resolved only through the registry) and matches
        # the canonical lookup in derive_coverage on the next run.
        frames.append(validated.assign(symbol=sym.canonical_id))
        fetched.append((provider_symbol, gap_start, gap_end))
    return frames, present, fetched, skipped
