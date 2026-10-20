# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""In-memory provider translation for a derived acquisition requirement.

The production registry is built from ``DataRequirement.canonical_symbols``.  It owns no symbol
list and reads no configuration file: provider spellings are derived through the same family
mappers the acquisition ports use.  ``InMemorySymbolRegistry`` remains as a small test seam for
isolated acquisition-policy tests that need deliberately artificial mappings.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from facdrone_data_types.symbols import CanonicalSymbol, Provider

if TYPE_CHECKING:
    from facdrone_data.acquisition.families.symbol_mapping import ProviderSymbols


class SymbolConfigError(Exception):
    """Raised at config time for a missing, duplicate, or non-round-trip mapping."""


@dataclass(frozen=True)
class SymbolMapping:
    """One canonical instrument and its symbol in each provider that serves it."""

    canonical: CanonicalSymbol
    provider_symbols: Mapping[Provider, str]


class InMemorySymbolRegistry:
    """A registry backed by explicitly injected mappings.

    Satisfies ``domain.ports.SymbolRegistry`` structurally. The reverse index
    (provider symbol -> canonical) is built once; collisions are recorded and
    surfaced by :meth:`validate`, not raised during construction, so a single
    ``validate()`` call reports every config defect at once.
    """

    def __init__(self, mappings: Sequence[SymbolMapping]) -> None:
        self._mappings: tuple[SymbolMapping, ...] = tuple(mappings)
        self._by_canonical: dict[str, SymbolMapping] = {}
        self._reverse: dict[Provider, dict[str, str]] = {}
        self._dup_canonicals: list[str] = []
        self._dup_provider_symbols: list[tuple[Provider, str]] = []
        for mapping in self._mappings:
            cid = mapping.canonical.canonical_id
            if cid in self._by_canonical:
                self._dup_canonicals.append(cid)
            self._by_canonical[cid] = mapping
            for provider, provider_symbol in mapping.provider_symbols.items():
                rev = self._reverse.setdefault(provider, {})
                if provider_symbol in rev and rev[provider_symbol] != cid:
                    self._dup_provider_symbols.append((provider, provider_symbol))
                rev[provider_symbol] = cid

    def to_provider(self, sym: CanonicalSymbol, provider: Provider) -> str:
        mapping = self._by_canonical.get(sym.canonical_id)
        if mapping is None:
            raise SymbolConfigError(f"no symbol mapping for {sym.canonical_id!r}")
        provider_symbol = mapping.provider_symbols.get(provider)
        if provider_symbol is None:
            raise SymbolConfigError(
                f"symbol {sym.canonical_id!r} has no mapping for "
                f"required provider {provider.value!r}"
            )
        return provider_symbol

    def to_canonical(self, provider_symbol: str, provider: Provider) -> CanonicalSymbol:
        cid = self._reverse.get(provider, {}).get(provider_symbol)
        if cid is None:
            raise SymbolConfigError(
                f"provider {provider.value!r} symbol {provider_symbol!r} "
                "maps to no canonical instrument"
            )
        return self._by_canonical[cid].canonical

    def all(self) -> tuple[CanonicalSymbol, ...]:
        return tuple(mapping.canonical for mapping in self._mappings)

    def providers_for(self, sym: CanonicalSymbol) -> tuple[Provider, ...]:
        """The providers mapped to ``sym``, sorted by token (status overview, R3.2).

        An unmapped symbol yields ``()`` — a status overview reports "no providers"
        rather than raising, unlike :meth:`to_provider` which is fail-loud before a
        download."""
        mapping = self._by_canonical.get(sym.canonical_id)
        if mapping is None:
            return ()
        return tuple(sorted(mapping.provider_symbols, key=lambda p: p.value))

    def validate(self) -> None:
        errors: list[str] = []
        for cid in self._dup_canonicals:
            errors.append(f"duplicate canonical id {cid!r}")
        for provider, provider_symbol in self._dup_provider_symbols:
            errors.append(
                f"duplicate {provider.value!r} symbol {provider_symbol!r} "
                "claimed by multiple instruments"
            )
        for mapping in self._mappings:
            cid = mapping.canonical.canonical_id
            for provider in mapping.provider_symbols:
                back = self.to_canonical(self.to_provider(mapping.canonical, provider), provider)
                if back != mapping.canonical:
                    errors.append(f"non-round-trip for {cid!r} via {provider.value!r}")
        if errors:
            raise SymbolConfigError("; ".join(errors))


class DerivedSymbolRegistry(InMemorySymbolRegistry):
    """Provider translation derived from required canonical symbols and the family mappers."""

    def __init__(self, symbols: Sequence[CanonicalSymbol], mappers: ProviderSymbols) -> None:
        super().__init__(
            tuple(
                SymbolMapping(symbol, _provider_symbols(symbol.canonical_id, mappers))
                for symbol in symbols
            )
        )
        self.validate()


def _provider_symbols(canonical_id: str, mappers: ProviderSymbols) -> Mapping[Provider, str]:
    mappings: dict[Provider, str] = {
        Provider.YFINANCE: canonical_id,
    }
    fmp = mappers.fmp_price(canonical_id)
    if fmp is not None:
        mappings[Provider.FMP] = fmp
    duka = mappers.duka(canonical_id)
    if duka is not None:
        mappings[Provider.DUKASCOPY] = duka
    return mappings
