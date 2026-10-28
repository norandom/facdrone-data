# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Typed ports for one provider data family (D1, D6, D7).

Per-family behaviour is a typed component carried on a :class:`FamilyPort`, never
a runtime branch (honours the no-behavioural-mode-flags rule):

* :class:`IncrementalStrategy` — derive the missing gap(s) from the stored cursor.
* :class:`Normalizer` — raw provider payload → table-shaped frame, provider quirks kept verbatim.
* :class:`MergeStrategy` — DO-UPDATE (``UpsertPanel``) or DO-NOTHING (``MergePanel``),
  selected at the family composition root.

These are structural Protocols: a concrete strategy conforms by shape, so the
``merge.py`` implementations need not import this module (avoids a cycle).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from datetime import date, datetime

    import pandas as pd
    from sqlalchemy import Engine, Table

    from facdrone_data.acquisition.families.symbol_mapping import ProviderSymbols
    from facdrone_data.acquisition.merge import MergeReceipt
    from facdrone_data_types.symbols import CanonicalSymbol


@runtime_checkable
class IncrementalStrategy(Protocol):
    """Derive the missing gap(s) for a family from its stored cursor (R7.1, R7.2).

    Returns a tuple of inclusive ``(start, end)`` ranges; ``()`` means fully
    covered — the refresher issues zero provider calls. ``scope`` is the per-row
    axis a cursor keys on (a symbol id, a series name, or ``None`` for global)."""

    def gaps(
        self,
        engine: Engine,
        table: Table,
        want: tuple[date, date],
        *,
        scope: str | None,
    ) -> tuple[tuple[date, date], ...]: ...


@runtime_checkable
class Normalizer(Protocol):
    """Raw provider payload → table-shaped frame, carrying provider quirks verbatim (R2.3).

    ``as_of`` is the injected clock used to stamp ``_updated_at`` on the tables
    that declare it (time-as-injected-data); ``symbol`` is the canonical symbol
    for symbol-keyed families, ``None`` for global/named families."""

    def normalize(
        self,
        raw: pd.DataFrame,
        *,
        symbol: CanonicalSymbol | None,
        as_of: datetime,
    ) -> pd.DataFrame: ...


@runtime_checkable
class MergeStrategy(Protocol):
    """A merge variant is a class, not a flag (mirrors ``freshness.FreshnessPolicy``).

    Chosen at the family composition root; the refresher calls ``merge`` with zero
    runtime branch on a mode."""

    def merge(self, engine: Engine, table: Table, frame: pd.DataFrame) -> MergeReceipt: ...


@dataclass(frozen=True)
class FamilyPort:
    """The explicit port disposition binding one writer to its typed strategies.

    ``writer`` is the back-ticked inventory name (the coverage test asserts it
    is a PORT writer); ``natural_key`` must equal ``target_table.primary_key``.
    Value-bearing fundamentals/prices carry ``UpsertPanel`` (a wrong strategy is
    silent data loss neither mypy nor import-linter can catch — the registry test
    asserts it)."""

    writer: str
    target_table: Table
    natural_key: tuple[str, ...]
    provider: Provider
    incremental: IncrementalStrategy
    merge_strategy: MergeStrategy
    normalizer: Normalizer
    availability_column: str
    quality_policy_default: str  # "keep_all" | "quarantine"
    # Explicit provider scopes for series/named families (e.g. the 20 FRED series
    # ids, the 12 economic-indicator names) — the family's scope *data*, not a
    # behaviour flag. Empty means: iterate the universe (symbol-keyed families) or
    # run once (global families), decided structurally from ``natural_key``.
    scopes: tuple[str, ...] = ()
    # The family's OWN canonical-YF → provider-symbol map (each family has its
    # own mapper: prices via ``ProviderSymbols.fmp_price``, fundamentals/COT via
    # ``ProviderSymbols.fmp``), called with the run's injected ``ProviderSymbols``. Returns
    # ``None`` when the symbol has no mapping for this family → skip + record (R5.3).
    # The yfinance fallback's mapper returns the symbol only when FMP cannot map it,
    # which realises the FMP-primary / yfinance-fallback precedence (D8).
    symbol_mapper: Callable[[ProviderSymbols, str], str | None] | None = None
    # A normalizer that reads the run's identity table (the intraday port judges a bar's
    # completeness in its symbol's stamp zone) is bound from the run's ``ProviderSymbols``,
    # as ``symbol_mapper`` is called with them. ``None``: ``normalizer`` reads no table.
    bind_normalizer: Callable[[ProviderSymbols], Normalizer] | None = None

    def normalizer_for(self, symbols: ProviderSymbols) -> Normalizer:
        """The normalizer this run's scopes go through."""
        return self.normalizer if self.bind_normalizer is None else self.bind_normalizer(symbols)
