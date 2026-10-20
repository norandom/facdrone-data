# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Pure derivation of the market-data surface a consumer's declared universe requires.

The service consumes authoritative instrument records and acquisition configuration as injected
data.  It owns no database or provider access: composition and live acquisition remain the
caller's responsibilities.  Re-running :meth:`DataRequirementService.derive` against the
registered universe claims is therefore sufficient to follow a universe change without a second
symbol list.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from facdrone_data.acquisition.families.symbol_mapping import ProviderSymbols
from facdrone_data.acquisition.fx_quotes import fx_pair_symbol
from facdrone_data_types.enums import AssetClass
from facdrone_data_types.symbols import CanonicalSymbol

if TYPE_CHECKING:
    from facdrone_data.acquisition.instrument_table import InstrumentTable

# The single-name classes whose instruments need statement fundamentals; index,
# commodity and FX instruments have no issuer to file (equity-factors 1.4).
FUNDAMENTALS_ASSET_CLASSES: frozenset[AssetClass] = frozenset(
    {AssetClass.EQUITIES_US, AssetClass.EQUITIES_I18N}
)


class _Admission(Protocol):
    """One claimed asset class: the instrument groups, optionally narrowed to symbols."""

    @property
    def asset_class(self) -> AssetClass: ...

    @property
    def groups(self) -> frozenset[str]: ...

    @property
    def symbols(self) -> frozenset[str] | None: ...


class _ClaimedUniverse(Protocol):
    """The admissions one universe claim declares."""

    @property
    def admits(self) -> tuple[_Admission, ...]: ...


class UniverseClaim(Protocol):
    """A declaration that claims part of the instrument universe: the acquisition scope."""

    @property
    def universe(self) -> _ClaimedUniverse: ...


@dataclass(frozen=True, slots=True)
class InstrumentRecord:
    """Acquisition-relevant fields from one authoritative tradeable instrument row.

    ``record_id`` is the stable operator-facing/broker identity used when the row carries no
    acquisition symbol.  ``acquisition_symbol`` is the catalog symbol (currently the
    ``yf_symbol`` column) and may be absent; absence is data to report, never a reason to drop the
    record before derivation.
    """

    record_id: str
    group: str
    acquisition_symbol: str | None
    profit_currency: str

    def __post_init__(self) -> None:
        _require_token(self.record_id, field="record_id")
        _require_token(self.group, field="group")
        if self.acquisition_symbol is not None:
            _require_token(self.acquisition_symbol, field="acquisition_symbol")
        _require_currency(self.profit_currency, field="profit_currency")


@dataclass(frozen=True, slots=True)
class InstrumentConversionRequirement:
    """One admitted symbol's dependency on an account-currency conversion series."""

    record_id: str
    instrument_symbol: str
    series: str


@dataclass(frozen=True, slots=True)
class UntradeableInstrument:
    """An admitted instrument made untradeable by unavailable conversion evidence."""

    record_id: str
    instrument_symbol: str
    missing_series: str

    @property
    def detail(self) -> str:
        """Stable explanation; consumers that gate admission on it reuse the text."""
        return (
            f"{self.record_id} ({self.instrument_symbol}) is untradeable: "
            f"missing series {self.missing_series}"
        )


def untradeable_for_missing_conversion(
    *, record_id: str, instrument_symbol: str, profit_currency: str, target_currency: str
) -> UntradeableInstrument:
    """Name the canonical acquisition series behind an unresolved account conversion."""
    return UntradeableInstrument(
        record_id=record_id,
        instrument_symbol=instrument_symbol,
        missing_series=fx_pair_symbol(profit_currency, target_currency=target_currency),
    )


@dataclass(frozen=True, slots=True)
class DataCoverageReport:
    """Required reads compared with an injected, actually available symbol set."""

    required_reads: tuple[str, ...]
    available_symbols: tuple[str, ...]
    uncovered_symbols: tuple[str, ...]
    untradeable_instruments: tuple[UntradeableInstrument, ...]


@dataclass(frozen=True, slots=True)
class DataRequirement:
    """Deterministic immutable acquisition projection for a set of universe claims.

    ``instruments`` contains only claimed acquisition symbols. Global price inputs
    remain a separate provenance category; :attr:`acquisition_symbols` provides their deterministic
    union for consumers that need the complete non-conversion daily-price request. Currency
    conversion series and named non-price feeds are separate acquisition axes.
    ``canonical_symbols`` types the full acquisition union, including conversion
    and panel-only series; ``required_reads`` excludes unclaimed panel-only rows.
    """

    instruments: tuple[str, ...]
    canonical_symbols: tuple[CanonicalSymbol, ...]
    profit_currencies: tuple[str, ...]
    conversion_series: tuple[str, ...]
    conversion_requirements: tuple[InstrumentConversionRequirement, ...]
    global_inputs: tuple[str, ...]
    # Panel-completeness symbols: authoritative instrument records whose group no claim
    # covers, kept acquired so a consumer's aligned panel spans every declared instrument.
    # Data-only series: never claimed instruments, never required reads, never
    # conversion-bearing.
    panel_symbols: tuple[str, ...]
    feeds: tuple[str, ...]
    unmapped_records: tuple[str, ...]
    # Fundamentals coverage: the claimed symbols whose asset class is a single-name
    # equities class — the set the fundamentals families must cover. Derived, never
    # hand-kept; empty while no claim covers an equities class. Coverage gaps are the
    # consumer's to record, never this projection's to fill.
    fundamentals_symbols: tuple[str, ...] = ()
    # Recorded series: declared series acquired through the daily-price port for later use,
    # never required reads. A missing one is a recording gap, not an uncovered input, so
    # they stay out of ``required_reads`` and join only the acquisition union.
    recorded_series: tuple[str, ...] = ()
    # The family symbol mappers over the identity table the consumer injected; carried
    # with the universe so every runner maps it the same way. Not part of equality.
    provider_symbols: ProviderSymbols = field(kw_only=True, compare=False)

    def __post_init__(self) -> None:
        canonical_ids = tuple(symbol.canonical_id for symbol in self.canonical_symbols)
        acquisition_reads = tuple(
            sorted(set(self.acquisition_symbols) | set(self.conversion_series))
        )
        if canonical_ids != acquisition_reads:
            raise ValueError(
                "canonical_symbols must be the ordered typed projection of every acquisition read"
            )

    @property
    def acquisition_symbols(self) -> tuple[str, ...]:
        """All required non-conversion daily-price symbols, without category loss."""
        return tuple(
            sorted(
                set(self.instruments)
                | set(self.global_inputs)
                | set(self.panel_symbols)
                | set(self.recorded_series)
            )
        )

    @property
    def required_reads(self) -> tuple[str, ...]:
        """Every daily-price symbol the consumer requires, including conversions."""
        return tuple(
            sorted(set(self.instruments) | set(self.global_inputs) | set(self.conversion_series))
        )

    def assert_covers(
        self,
        reads: Collection[str],
        available_symbols: Collection[str],
    ) -> DataCoverageReport:
        """Compare declared required reads with actual panel/acquisition availability.

        Availability is deliberately injected rather than inferred from this requirement.  Using
        :attr:`required_reads` as both sides would make the coverage check tautological and could
        never expose a requested-but-absent series such as ``EURHKD=X``.
        """
        required_reads = tuple(sorted(set(reads)))
        available = tuple(sorted(set(available_symbols)))
        available_set = set(available)
        untradeable = tuple(
            UntradeableInstrument(
                record_id=item.record_id,
                instrument_symbol=item.instrument_symbol,
                missing_series=item.series,
            )
            for item in self.conversion_requirements
            if item.series not in available_set
        )
        return DataCoverageReport(
            required_reads=required_reads,
            available_symbols=available,
            uncovered_symbols=tuple(sorted(set(required_reads) - available_set)),
            untradeable_instruments=untradeable,
        )


@dataclass(frozen=True, slots=True)
class DataRequirementService:
    """Derive one requirement from injected source records and universe claims.

    ``global_inputs`` and ``recorded_series`` are the consumer's own lists (price inputs it
    reads beside its instruments, and series it records); neither has a default, because
    only the consumer knows them (facdrone-data-manager 1.3).
    """

    instrument_records: tuple[InstrumentRecord, ...]
    global_inputs: tuple[str, ...] = field(kw_only=True)
    configured_feeds: tuple[str, ...] = ()
    recorded_series: tuple[str, ...] = field(kw_only=True)
    identities: InstrumentTable = field(kw_only=True)  # the consumer's identity files
    conversion_target_currency: str = field(kw_only=True)

    def __post_init__(self) -> None:
        _require_currency(self.conversion_target_currency, field="conversion_target_currency")
        for symbol in (*self.global_inputs, *self.recorded_series):
            _require_token(symbol, field="global input")
        for feed in self.configured_feeds:
            _require_token(feed, field="configured feed")

    def derive(self, claims: Sequence[UniverseClaim]) -> DataRequirement:
        """Project all registered universe claims over the current authoritative records.

        Positive universe declaration is the sole admission authority.  A missing acquisition
        symbol can match only a group-wide claim; a symbol-narrowed claim cannot silently infer an
        identity the source did not provide.
        """
        claimed: set[str] = set()
        panel_symbols: set[str] = set()
        fundamentals: set[str] = set()
        currencies: set[str] = set()
        unmapped: set[str] = set()
        conversion_requirements: set[InstrumentConversionRequirement] = set()
        canonical_by_id: dict[str, CanonicalSymbol] = {}
        for record in self.instrument_records:
            asset_class = _claimed_asset_class(claims, record)
            if asset_class is None:
                if record.acquisition_symbol is not None:
                    panel_symbols.add(record.acquisition_symbol)
                continue
            currencies.add(record.profit_currency)
            if record.acquisition_symbol is None:
                unmapped.add(record.record_id)
            else:
                claimed.add(record.acquisition_symbol)
                if asset_class in FUNDAMENTALS_ASSET_CLASSES:
                    fundamentals.add(record.acquisition_symbol)
                _add_canonical(
                    canonical_by_id,
                    CanonicalSymbol(
                        canonical_id=record.acquisition_symbol,
                        asset_class=asset_class,
                        profit_ccy=record.profit_currency,
                    ),
                )
                if record.profit_currency != self.conversion_target_currency:
                    conversion_requirements.add(
                        _conversion_requirement(
                            record, record.acquisition_symbol, self.conversion_target_currency
                        )
                    )

        global_inputs = tuple(sorted(set(self.global_inputs)))
        profit_currencies = tuple(sorted(currencies))
        conversion_series = self._conversion_series(profit_currencies)
        panel = tuple(sorted(panel_symbols - claimed))
        recorded = tuple(sorted(set(self.recorded_series)))
        _add_support(canonical_by_id, (*global_inputs, *conversion_series, *panel, *recorded))
        return DataRequirement(
            instruments=tuple(sorted(claimed)),
            canonical_symbols=tuple(canonical_by_id[key] for key in sorted(canonical_by_id)),
            profit_currencies=profit_currencies,
            conversion_series=conversion_series,
            conversion_requirements=_sorted_requirements(conversion_requirements),
            global_inputs=global_inputs,
            panel_symbols=panel,
            feeds=tuple(sorted(set(self.configured_feeds))),
            unmapped_records=tuple(sorted(unmapped)),
            fundamentals_symbols=tuple(sorted(fundamentals)),
            recorded_series=recorded,
            provider_symbols=ProviderSymbols(self.identities),
        )

    def _conversion_series(self, profit_currencies: tuple[str, ...]) -> tuple[str, ...]:
        """The FX series converting each foreign profit currency to the target."""
        return tuple(
            fx_pair_symbol(currency, target_currency=self.conversion_target_currency)
            for currency in profit_currencies
            if currency != self.conversion_target_currency
        )


def _sorted_requirements(
    items: Collection[InstrumentConversionRequirement],
) -> tuple[InstrumentConversionRequirement, ...]:
    return tuple(
        sorted(items, key=lambda item: (item.record_id, item.instrument_symbol, item.series))
    )


def _conversion_requirement(
    record: InstrumentRecord, instrument_symbol: str, target_currency: str
) -> InstrumentConversionRequirement:
    return InstrumentConversionRequirement(
        record_id=record.record_id,
        instrument_symbol=instrument_symbol,
        series=fx_pair_symbol(record.profit_currency, target_currency=target_currency),
    )


def _claimed_asset_class(
    claims: Sequence[UniverseClaim], record: InstrumentRecord
) -> AssetClass | None:
    classes: set[AssetClass] = set()
    for claim in claims:
        for admission in claim.universe.admits:
            if record.group not in admission.groups:
                continue
            if admission.symbols is None or (
                record.acquisition_symbol is not None
                and record.acquisition_symbol in admission.symbols
            ):
                classes.add(admission.asset_class)
    if len(classes) > 1:
        names = ", ".join(sorted(item.value for item in classes))
        raise ValueError(
            f"instrument {record.record_id!r} has conflicting claimed asset classes: {names}"
        )
    return next(iter(classes), None)


def _support_symbol(symbol: str) -> CanonicalSymbol:
    if symbol.endswith("=X") and len(symbol) == 8:
        return CanonicalSymbol(
            canonical_id=symbol,
            asset_class=AssetClass.FOREX,
            profit_ccy=symbol[3:6],
        )
    return CanonicalSymbol(
        canonical_id=symbol,
        asset_class=AssetClass.INDICES,
        profit_ccy="USD",
    )


def _add_support(by_id: dict[str, CanonicalSymbol], symbols: tuple[str, ...]) -> None:
    """Type every support read no admitted record already typed."""
    for symbol in symbols:
        if symbol not in by_id:
            _add_canonical(by_id, _support_symbol(symbol))


def _add_canonical(by_id: dict[str, CanonicalSymbol], symbol: CanonicalSymbol) -> None:
    existing = by_id.get(symbol.canonical_id)
    if existing is not None and existing != symbol:
        raise ValueError(
            f"acquisition symbol {symbol.canonical_id!r} has conflicting canonical declarations"
        )
    by_id[symbol.canonical_id] = symbol


def _require_token(value: str, *, field: str) -> None:
    if not value or value != value.strip():
        raise ValueError(f"{field} must be non-empty and trimmed")


def _require_currency(value: str, *, field: str) -> None:
    if value != value.strip() or value != value.upper() or len(value) != 3 or not value.isalpha():
        raise ValueError(f"{field} must be a trimmed three-letter uppercase code, got {value!r}")


__all__ = [
    "FUNDAMENTALS_ASSET_CLASSES",
    "DataCoverageReport",
    "DataRequirement",
    "DataRequirementService",
    "InstrumentConversionRequirement",
    "InstrumentRecord",
    "UniverseClaim",
    "UntradeableInstrument",
    "untradeable_for_missing_conversion",
]
