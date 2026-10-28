# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Cross-tier boundary ports as typed Protocols (task 1.2).

These are the ONLY live-boundary seams: every external data provider, the
broker transport, the symbol registry, and the ``RegimeSource`` scale provider
are reached through Protocols declared here in the typed core. Consumer code
depends on these contracts, never on a concrete shell adapter (enforced by
import-linter); tests substitute deterministic fakes (R1.1, R3.2, R6.1).

``RegimeSource`` is the seam that lets a daily-derived scale source and a
tick-derived one be selected at a composition root rather than via a mode flag
(R13.5).

pandas is imported under ``TYPE_CHECKING`` only: the contracts reference
``DataFrame`` shapes for mypy without making the core eagerly import the
quant stack at runtime.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from facdrone_data_types.symbols import CanonicalSymbol, Provider

if TYPE_CHECKING:
    import pandas as pd


@dataclass(frozen=True)
class FetchRequest:
    """One bounded provider fetch: which provider symbol, which dataset family,
    over which inclusive date range."""

    provider_symbol: str
    dataset: str
    start: date
    end: date
    interval: str = "15min"

    def __post_init__(self) -> None:
        if self.interval not in {"1min", "5min", "15min", "30min", "1hour"}:
            raise ValueError(f"unsupported bar interval: {self.interval!r}")


class ProviderAnsweredEmpty(Exception):
    """The provider answered the request and had no rows for its window.

    A ``MarketDataClient`` that can tell an answer from a failure raises it instead of
    returning an empty frame; an empty frame stays "no usable answer" (fail-open, R2.4)."""


@runtime_checkable
class MarketDataClient(Protocol):
    """Injected port for one external data provider (R1.1, R6.1).

    The caller schema-validates the returned frame; adapters declare which
    dataset families they serve so a family maps to exactly one adapter. A fetch
    may raise ``ProviderAnsweredEmpty`` when the provider answered with no rows.
    """

    @property
    def provider(self) -> Provider: ...

    def fetch(self, req: FetchRequest) -> pd.DataFrame: ...

    def datasets(self) -> tuple[str, ...]: ...


@runtime_checkable
class SymbolRegistry(Protocol):
    """Single source of truth for provider<->canonical translation (R3.2).

    The contract lives in the core; the config-loading implementation lives
    in ``pipelines/data`` (IO). Resolution failures and config-integrity
    failures are fail-loud config-time errors, never silent.
    """

    def to_provider(self, sym: CanonicalSymbol, provider: Provider) -> str: ...

    def to_canonical(self, provider_symbol: str, provider: Provider) -> CanonicalSymbol: ...

    def all(self) -> tuple[CanonicalSymbol, ...]: ...

    def validate(self) -> None: ...


@runtime_checkable
class RegimeSource(Protocol):
    """Named scales as of a date — daily-derived or tick-derived (R13.5).

    The implementation is chosen at a composition root, never via a flag, so
    the additive tick path never disturbs the existing daily path.
    """

    def regime_scales(self, as_of: date) -> Mapping[str, float]: ...
