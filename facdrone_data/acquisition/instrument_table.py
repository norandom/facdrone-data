# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Interfacer: YAML series identities become validated, frozen lookup rows.

Kinds declare which universes reuse a SERIES; contract tables own economics.
Task 2.2 will replace the provider dictionaries with this table at their callers.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from facdrone_data_types.enums import AssetClass
from facdrone_data_types.symbols import CanonicalSymbol, Provider

InstrumentKind = Literal["cfd", "futures"]
Intraday = Literal["fmp", "unavailable"]
Token = Annotated[str, StringConstraints(pattern=r"^\S+$")]


class InstrumentTableError(ValueError):
    """An identity file or lookup is invalid; the message names its row."""


@dataclass(frozen=True)
class InstrumentIdentity:
    canonical: CanonicalSymbol
    kinds: frozenset[InstrumentKind]
    providers: Mapping[Provider, str | None]
    intraday: Intraday
    shared_series: bool

    @property
    def asset_class(self) -> AssetClass:
        return self.canonical.asset_class

    @property
    def settlement_ccy(self) -> str:
        return self.canonical.profit_ccy


@dataclass(frozen=True)
class CoverageReport:
    """Sorted canonical ids with/without identity, plus rows outside the universe."""

    with_identity: tuple[str, ...]
    without_identity: tuple[str, ...]
    outside: tuple[str, ...]


class _Row(BaseModel):
    """Validate the file boundary before constructing the immutable identity."""

    model_config = ConfigDict(extra="forbid")

    canonical: Token
    kind: InstrumentKind | None = None
    kinds: tuple[InstrumentKind, ...] = ()
    asset_class: AssetClass
    settlement_ccy: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    providers: dict[Provider, Token | None]
    intraday: Intraday
    shared_series: bool = False

    @model_validator(mode="after")
    def complete(self) -> Self:
        if (self.kind is None) == (not self.kinds):
            raise ValueError("declare exactly one of kind or kinds")
        kinds = self.kinds if self.kind is None else (self.kind,)
        if len(set(kinds)) != len(kinds):
            raise ValueError("duplicate kinds")
        if self.shared_series != (len(kinds) > 1):
            raise ValueError("shared_series requires kinds listing both cfd and futures")
        if not {Provider.FMP, Provider.YFINANCE} <= self.providers.keys():
            raise ValueError("providers must declare fmp (possibly null) and yfinance")
        if self.providers[Provider.YFINANCE] is None:
            raise ValueError("yfinance must name the series")
        if self.intraday == "fmp" and self.providers[Provider.FMP] is None:
            raise ValueError("intraday fmp requires an fmp symbol")
        return self

    def identity(self) -> InstrumentIdentity:
        return InstrumentIdentity(
            canonical=CanonicalSymbol(self.canonical, self.asset_class, self.settlement_ccy),
            kinds=frozenset(self.kinds if self.kind is None else (self.kind,)),
            providers=MappingProxyType(dict(self.providers)),
            intraday=self.intraday,
            shared_series=self.shared_series,
        )


def _read_rows(path: Path) -> Iterable[tuple[str, InstrumentIdentity]]:
    try:
        data: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise InstrumentTableError(f"{path}: {exc}") from exc
    if not isinstance(data, list):
        raise InstrumentTableError(f"{path}: expected a list of identity rows")
    for number, raw in enumerate(data, start=1):
        canonical = raw.get("canonical", "<missing canonical>") if isinstance(raw, dict) else raw
        location = f"{path}: row {number} ({canonical!r})"
        try:
            yield location, _Row.model_validate(raw).identity()
        except ValueError as exc:
            raise InstrumentTableError(f"{location}: {exc}") from exc


def _canonical_id(canonical: CanonicalSymbol | str) -> str:
    return canonical if isinstance(canonical, str) else canonical.canonical_id


class InstrumentTable:
    """One validated series registry across the supplied identity files.

    The files are always given by the caller; the module knows no default location.
    """

    def __init__(self, paths: Iterable[Path]) -> None:
        self._rows: dict[str, InstrumentIdentity] = {}
        self._reverse: dict[tuple[Provider, str], CanonicalSymbol] = {}
        for path in paths:
            for location, row in _read_rows(path):
                self._add(location, row)
        self._validate_round_trips()

    def _add(self, location: str, row: InstrumentIdentity) -> None:
        cid = row.canonical.canonical_id
        if cid in self._rows:
            raise InstrumentTableError(f"{location}: duplicate canonical {cid!r}")
        self._rows[cid] = row
        for provider, symbol in row.providers.items():
            if symbol is None:
                continue
            key = (provider, symbol)
            if key in self._reverse:
                origin = self._reverse[key].canonical_id
                raise InstrumentTableError(
                    f"{location}: duplicate {provider.value} symbol {symbol!r}, also on {origin!r}"
                )
            self._reverse[key] = row.canonical

    def _validate_round_trips(self) -> None:
        for row in self._rows.values():
            for provider in row.providers:
                symbol = self.provider_symbol(row.canonical, provider)
                if symbol is not None and self.canonical_for(provider, symbol) != row.canonical:
                    raise InstrumentTableError(
                        f"row {row.canonical.canonical_id!r}: non-round-trip via {provider.value}"
                    )

    def identity(self, canonical: CanonicalSymbol | str) -> InstrumentIdentity:
        cid = _canonical_id(canonical)
        try:
            return self._rows[cid]
        except KeyError as exc:
            raise InstrumentTableError(f"no identity row for {cid!r}") from exc

    def provider_symbol(self, canonical: CanonicalSymbol | str, provider: Provider) -> str | None:
        return self.identity(canonical).providers.get(provider)

    def canonical_for(self, provider: Provider, symbol: str) -> CanonicalSymbol:
        try:
            return self._reverse[provider, symbol]
        except KeyError as exc:
            raise InstrumentTableError(
                f"no identity row for {provider.value} symbol {symbol!r}"
            ) from exc

    def without_intraday(self) -> frozenset[str]:
        """The canonical ids whose row declares ``intraday: unavailable``: no provider serves
        their intraday bars, so an intraday scope that names them can only miss."""
        return frozenset(cid for cid, row in self._rows.items() if row.intraday == "unavailable")

    def by_kind(self, kind: InstrumentKind) -> tuple[CanonicalSymbol, ...]:
        return tuple(row.canonical for _, row in sorted(self._rows.items()) if kind in row.kinds)

    def coverage(self, universe: Iterable[CanonicalSymbol | str]) -> CoverageReport:
        requested = {_canonical_id(symbol) for symbol in universe}
        present = self._rows.keys()
        return CoverageReport(
            with_identity=tuple(sorted(requested & present)),
            without_identity=tuple(sorted(requested - present)),
            outside=tuple(sorted(present - requested)),
        )
