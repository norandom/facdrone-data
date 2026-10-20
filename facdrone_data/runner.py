# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The download runner: window, family selection, partition and execution (R3.2, R3.3, R4.2, R6.3).

One recipe for both entry points (the module's ``facdrone-data`` and the trading core's
``facdrone data``): the caller binds a universe, a credential set and the provider clients;
this module resolves the window from the stored panel, selects the families, partitions
them into runnable, refused (a key is missing: the variable is named) and unavailable (a
client library is missing: the extra is named), runs the runnable ones through the family
refresher and reports the rest. No provider request precedes the partition, and a missing
key or extra refuses families, never the run.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Protocol

from sqlalchemy import func, select

from facdrone_data.acquisition.families.refresher import (
    DownloadInputs,
    DownloadReport,
    FamilyRefresher,
    FamilyRefreshReport,
    ProviderOutcome,
    Refusal,
    RefusedFamily,
)
from facdrone_data.acquisition.families.registry import download_families
from facdrone_data.acquisition.schema import DAILY_PRICES
from facdrone_data.acquisition.symbol_registry import DerivedSymbolRegistry
from facdrone_data.acquisition.validation import QuarantineUnmeasurable
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from sqlalchemy import Engine

    from facdrone_data.acquisition.families.credentials import ProviderCredentials
    from facdrone_data.acquisition.families.ports import FamilyPort
    from facdrone_data.acquisition.families.refresher import QualityGate
    from facdrone_data.acquisition.requirement import DataRequirement
    from facdrone_data.quality.ensemble import EnsembleValidator
    from facdrone_data_types.ports import MarketDataClient

    FamilySelector = Callable[[str], tuple[FamilyPort, ...]]


class DownloadRequest(Protocol):
    """What one download asks for; the core's command and the module's both satisfy it."""

    @property
    def source(self) -> str: ...

    @property
    def from_date(self) -> date: ...

    @property
    def to_date(self) -> date: ...

    @property
    def exclude_families(self) -> tuple[str, ...]: ...

    @property
    def families(self) -> tuple[str, ...]: ...

    @property
    def refetch(self) -> bool: ...


@dataclass(frozen=True)
class DownloadCommand:
    """The module's explicit download request: a resolved window and the family selection."""

    source: str
    from_date: date
    to_date: date
    exclude_families: tuple[str, ...] = ()
    families: tuple[str, ...] = ()
    refetch: bool = False

    def __post_init__(self) -> None:
        if self.from_date > self.to_date:
            raise ValueError(f"--from {self.from_date} must not be after --to {self.to_date}")


# CLI --source token to provider set.
_SOURCE_PROVIDERS: dict[str, frozenset[Provider]] = {
    "fmp": frozenset({Provider.FMP}),
    "yfinance": frozenset({Provider.YFINANCE}),
    "fred": frozenset({Provider.FRED}),
    "cftc": frozenset({Provider.CFTC}),
    "ken_french": frozenset({Provider.KEN_FRENCH}),
    "oecd": frozenset({Provider.OECD}),
    "etf": frozenset({Provider.ETF}),
    "dukascopy": frozenset({Provider.DUKASCOPY}),
    "aqr": frozenset({Provider.AQR}),  # QMJ/BAB style factors
}
_KEYED_PROVIDERS: frozenset[Provider] = frozenset({Provider.FMP, Provider.FRED, Provider.ETF})
# The client library each paid provider's adapter needs, and the extra that installs it
# (R6.2, R6.3). A provider absent here needs only the base installation.
_PROVIDER_EXTRAS: dict[Provider, tuple[str, str]] = {
    Provider.FMP: ("requests", "fmp"),
    Provider.ETF: ("requests", "fmp"),
    Provider.DUKASCOPY: ("requests", "dukascopy"),
}
_COMPLETE_PRICE_OUTCOMES: frozenset[ProviderOutcome] = frozenset(
    {ProviderOutcome.PROVIDER_VERIFIED, ProviderOutcome.ALREADY_LANDED}
)


# How far back a resumed download reaches before the panel's last covered day. Resuming
# exactly at the coverage end would only request days AFTER it, which loses a provider's
# late revision to that final day. On an idempotent merge the overlap costs a handful of
# rows and recovers those revisions.
_RESUME_OVERLAP = timedelta(days=7)
# With no coverage there is nothing to resume from. A window of zero days would report
# success having fetched nothing, which reads exactly like a refresh that worked.
_COLD_START = timedelta(days=5 * 365)


def resume_point(
    coverage_ends: Mapping[str, date], required: tuple[str, ...] | Sequence[str]
) -> date | None:
    """The last day the panel covers for EVERY required symbol, or None for a cold start.

    The minimum, deliberately, and not the maximum the first version used. A max is the
    date the panel reaches for its most current symbol, which says nothing about the
    others: one freshly-updated series advances the resume point for all of them, a
    symbol months behind is handed a one-week window that cannot reach its shortfall,
    and because a max only moves forward the hole never closes. Observed on the live
    panel with the stress series at 2026-08-27 and EURUSD at 2026-06-16.

    Widening the window to the laggard is close to free: a symbol that already holds
    those days yields no gaps, so no provider request is made for it.

    A required symbol with no history at all has no point to resume FROM, and resuming
    from the others would leave it holding a week where it needs years — so that is a
    cold start, which is the honest answer rather than a cheaper one.
    """
    if not required:
        return None
    ends = [coverage_ends.get(symbol) for symbol in required]
    return None if any(end is None for end in ends) else min(end for end in ends if end is not None)


def resolve_download_window(
    *,
    explicit_from: date | None,
    explicit_to: date | None,
    coverage_end: date | None,
    today: date,
) -> tuple[date, date]:
    """The window to download: what the operator asked for, or what the panel needs.

    Pure. The coverage end is read elsewhere and passed in, so this rule is testable
    without a database and the read has exactly one home.

    Giving one end and not the other is REFUSED rather than half-guessed: silently
    inventing the missing end is how a partial refresh comes to look like a complete one.
    """
    if (explicit_from is None) != (explicit_to is None):
        raise ValueError("give both --from and --to, or neither to resume from the panel")
    if explicit_from is not None and explicit_to is not None:
        return explicit_from, explicit_to

    start = today - _COLD_START if coverage_end is None else coverage_end - _RESUME_OVERLAP
    return min(start, today), today


def select_families(source: str) -> tuple[FamilyPort, ...]:
    """Return the family ports selected by a CLI ``--source`` value.

    The selector is a comma-separated list. A bare name includes that family set; a
    name prefixed with ``-`` excludes it. Listing only exclusions implies ``all`` first,
    so ``-dukascopy`` reads as "everything except order flow" — which is the shape
    actually wanted, since order flow is the one rate-limited family and nothing reads
    what it produces.

    A selector resolving to no families is REFUSED. It would otherwise report success
    having fetched nothing, which is indistinguishable from a refresh that worked.
    """
    families = download_families().families()
    included, excluded = _parse_selector(source)
    chosen = _chosen_providers(included, excluded, families)
    selected = tuple(port for port in families if port.provider in chosen)
    if not selected:
        raise ValueError(f"--source {source!r} selects no families")
    return selected


def excluded_families(
    families: tuple[FamilyPort, ...], names: tuple[str, ...]
) -> tuple[FamilyPort, ...]:
    """Validate dataset/writer exclusions against the one registered family set."""
    registered = download_families().families()
    known = {name for port in registered for name in (port.writer, port.target_table.name)}
    unknown = set(names) - known
    if unknown:
        raise ValueError(f"unknown --exclude-family {sorted(unknown)}")
    selected = tuple(
        port
        for port in families
        if not set(names).intersection((port.writer, port.target_table.name))
    )
    if not selected:
        raise ValueError("--exclude-family selects no families")
    return selected


def only_families(
    families: tuple[FamilyPort, ...], names: tuple[str, ...]
) -> tuple[FamilyPort, ...]:
    """Validate dataset/writer inclusions against the one registered family set."""
    registered = download_families().families()
    known = {name for port in registered for name in (port.writer, port.target_table.name)}
    wanted = set(names)
    unknown = wanted - known
    if unknown:
        raise ValueError(f"unknown --family {sorted(unknown)}")
    selected = tuple(
        port for port in families if wanted.intersection((port.writer, port.target_table.name))
    )
    if not selected:
        raise ValueError("--family selects no families")
    return selected


def _parse_selector(source: str) -> tuple[set[str], set[str]]:
    """(included, excluded) family-set names from a comma-separated selector."""
    tokens = [token.strip() for token in source.split(",") if token.strip()]
    if not tokens:
        raise ValueError("empty --source; choose all|" + "|".join(sorted(_SOURCE_PROVIDERS)))

    included: set[str] = set()
    excluded: set[str] = set()
    for token in tokens:
        name = token[1:] if token.startswith("-") else token
        if name != "all" and name not in _SOURCE_PROVIDERS:
            raise ValueError(
                f"unknown --source {name!r}; choose all|"
                f"{'|'.join(sorted(_SOURCE_PROVIDERS))}, optionally prefixed with '-' to exclude"
            )
        (excluded if token.startswith("-") else included).add(name)

    return included, excluded


def _chosen_providers(
    included: set[str], excluded: set[str], families: tuple[FamilyPort, ...]
) -> set[Provider]:
    """The providers the selector names: everything when only exclusions (or ``all``) are
    given, then the exclusions removed."""
    # Only exclusions given: start from everything, then remove.
    if not included or "all" in included:
        chosen = {port.provider for port in families}
    else:
        chosen = {provider for name in included for provider in _SOURCE_PROVIDERS[name]}
    for name in excluded:
        chosen -= {port.provider for port in families} if name == "all" else _SOURCE_PROVIDERS[name]

    return chosen


def require_credentials(families: tuple[FamilyPort, ...], credentials: ProviderCredentials) -> None:
    """Raise before any request if a selected provider needs a missing key."""
    for provider in {port.provider for port in families} & _KEYED_PROVIDERS:
        credentials.require(provider)  # raises MissingCredentialError naming source + key


def _available_required_prices(
    families: tuple[FamilyPort, ...], report: FamilyRefreshReport
) -> tuple[str, ...]:
    """Project complete daily-price outcomes into actual required-read availability.

    The family result, not provider mapping existence, is authoritative. A partial response does
    not cover the requested window; only a verified fetch or a panel-confirmed
    ``already_landed`` outcome establishes coverage.
    """
    daily_price_writers = {port.writer for port in families if port.target_table is DAILY_PRICES}
    return tuple(
        sorted(
            {
                summary.scope
                for summary in report.summaries
                if summary.writer in daily_price_writers
                and summary.provider_outcome in _COMPLETE_PRICE_OUTCOMES
            }
        )
    )


def module_installed(name: str) -> bool:
    """Whether a top-level module can be imported, without importing it."""
    return importlib.util.find_spec(name) is not None


def partition_families(
    families: tuple[FamilyPort, ...],
    credentials: ProviderCredentials,
    installed: Callable[[str], bool],
) -> tuple[tuple[FamilyPort, ...], tuple[RefusedFamily, ...]]:
    """Split the selection into the families that can run and those refused or unavailable.

    Decided before any provider request: a family whose provider needs an unconfigured key is
    refused with the key's variable; one whose client library is absent is unavailable with
    the extra that installs it (R4.2, R6.3).
    """
    runnable: list[FamilyPort] = []
    refused: list[RefusedFamily] = []
    for port in families:
        variable = credentials.missing_variable(port.provider)
        extra = _PROVIDER_EXTRAS.get(port.provider)
        if extra is not None and not installed(extra[0]):
            refused.append(RefusedFamily(port.writer, Refusal.UNAVAILABLE, extra[1]))
        elif variable is not None:
            refused.append(RefusedFamily(port.writer, Refusal.REFUSED, variable))
        else:
            runnable.append(port)
    return tuple(runnable), tuple(refused)


def panel_coverage_ends(engine: Engine, symbols: Sequence[str]) -> dict[str, date]:
    """The last date the price panel covers, PER SYMBOL, for those that have any history.

    Per symbol, because the panel does not reach one date: a global maximum is the coverage
    of the most current symbol alone, and resuming from it starves every symbol behind it.
    Which date to resume from is chosen by :func:`resume_point`; this only reads.
    """
    query = (
        select(DAILY_PRICES.c.symbol, func.max(DAILY_PRICES.c.date))
        .where(DAILY_PRICES.c.symbol.in_(tuple(symbols)))
        .group_by(DAILY_PRICES.c.symbol)
    )
    with engine.connect() as conn:
        rows = conn.execute(query).all()
    return {str(symbol): date.fromisoformat(str(newest)) for symbol, newest in rows if newest}


@dataclass(frozen=True)
class DataRootDeps:
    """Live boundaries for the data-download runner."""

    engine: Engine
    credentials: ProviderCredentials
    requirement: Callable[[], DataRequirement]
    clients: Mapping[Provider, MarketDataClient]
    validator: EnsembleValidator
    installed: Callable[[str], bool] = field(default=module_installed)


def compose_download_runner(
    deps: DataRootDeps,
    *,
    family_selector: FamilySelector,
    gate: QualityGate,
) -> Callable[[DownloadRequest], DownloadReport]:
    """Bind the data-download runner over injected transports."""

    def _run(cmd: DownloadRequest) -> DownloadReport:
        families = excluded_families(family_selector(cmd.source), cmd.exclude_families)
        if cmd.families:
            families = only_families(families, cmd.families)
        runnable, refused = partition_families(families, deps.credentials, deps.installed)
        requirement = deps.requirement()
        registry = DerivedSymbolRegistry(
            requirement.canonical_symbols, requirement.provider_symbols
        )
        inputs = DownloadInputs(
            universe=requirement.canonical_symbols,
            families=runnable,
            want=(cmd.from_date, cmd.to_date),
            engine=deps.engine,
            clients=deps.clients,
            registry=registry,
            symbols=requirement.provider_symbols,
            validator=deps.validator,
            policy=QuarantineUnmeasurable(),  # the door (R6.4): no unmeasurable price lands
            as_of=datetime.combine(cmd.to_date, time(23, 0)),  # injected clock for PIT reads
            refetch=cmd.refetch,
        )
        family_report = FamilyRefresher(gate=gate).run(inputs)
        coverage = requirement.assert_covers(
            requirement.required_reads,
            _available_required_prices(runnable, family_report),
        )
        return DownloadReport(family_report.summaries, coverage=coverage, refused=refused)

    return _run


__all__ = [
    "DataRootDeps",
    "DownloadCommand",
    "DownloadRequest",
    "RefusedFamily",
    "compose_download_runner",
    "partition_families",
    "require_credentials",
    "resolve_download_window",
    "resume_point",
    "select_families",
]
