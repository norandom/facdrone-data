# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The orchestration-free family refresher (R2.1, R2.4, R5.3, R6.x, R7.x, R8.2).

Drives one family per scope: derive the gap(s) from the family's incremental
strategy (or use the full window for explicit refetch), fetch through the injected
``MarketDataClient``, route the frame through the family's quality gate, and merge with the family's
``MergeStrategy``. Every per-family behaviour is a typed component carried on the
``FamilyPort`` — the refresher branches on nothing.

Scope resolution is structural: a family with explicit ``scopes`` iterates them
(FRED series ids, indicator names); a symbol-keyed family iterates the universe and
resolves each through the registry (an unmapped symbol is skipped and recorded,
R5.3); a global family runs once. Fail-open: an empty/`[]` provider payload skips
that scope, persists nothing, and the run continues (R2.4).

Depends only on ``domain.ports`` + pipelines primitives — never imports ``shells``
(an import-linter contract pins ``pipelines ↛ shells``). The quality gate is an
injected seam (task 4.1 binds the real validate→stamp→project gate; the default is
a pass-through so task 3.3 can persist via the family's strategies).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, cast

import pandas as pd
from sqlalchemy import Date, DateTime

from facdrone_data.acquisition.families.ports import FamilyPort
from facdrone_data.acquisition.requirement import DataCoverageReport
from facdrone_data_types.ports import FetchRequest

if TYPE_CHECKING:
    from datetime import date, datetime

    from sqlalchemy import Engine

    from facdrone_data.acquisition.families.symbol_mapping import ProviderSymbols
    from facdrone_data.acquisition.validation import QualityPolicy
    from facdrone_data.quality.ensemble import EnsembleValidator
    from facdrone_data_types.ports import MarketDataClient, SymbolRegistry
    from facdrone_data_types.symbols import CanonicalSymbol, Provider


@dataclass(frozen=True)
class DownloadInputs:
    """Everything the data composition root binds for one download run."""

    universe: tuple[CanonicalSymbol, ...]
    families: tuple[FamilyPort, ...]
    want: tuple[date, date]
    engine: Engine
    clients: Mapping[Provider, MarketDataClient]
    registry: SymbolRegistry
    symbols: ProviderSymbols  # the family mappers over the run's injected identity table
    validator: EnsembleValidator
    policy: QualityPolicy
    as_of: datetime  # injected clock for _updated_at (time-as-injected-data)
    refetch: bool = False


class ProviderOutcome(StrEnum):
    """What the real adapter/landing path established for one family scope."""

    PROVIDER_VERIFIED = "provider_verified"
    PARTIAL_PROVIDER_COVERAGE = "partial_provider_coverage"
    ALREADY_LANDED = "already_landed"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    QUALITY_REJECTED = "quality_rejected"
    UNMAPPED = "unmapped"
    CLIENT_UNAVAILABLE = "client_unavailable"


@dataclass(frozen=True)
class FamilySummary:
    """Per-(family, scope) audit row (R8.2)."""

    writer: str
    scope: str
    fetched: int
    skipped: int
    inserted: int
    updated: int
    provider_outcome: ProviderOutcome
    quarantined: int = 0  # rows the gate refused before merge (the door, R6.4)
    cursor: date | None = None  # the newest availability date this run landed for the scope


class Refusal(StrEnum):
    """Why a selected family did not run (facdrone-data-manager R4.2, R6.3)."""

    REFUSED = "refused"  # a key its provider needs is not configured
    UNAVAILABLE = "unavailable"  # the client library its provider needs is not installed


@dataclass(frozen=True)
class RefusedFamily:
    """A selected family the run did not attempt, and the variable or extra it lacks."""

    writer: str
    refusal: Refusal
    needs: str  # the environment variable (refused) or the extra (unavailable); never a value


@dataclass(frozen=True)
class FamilyRefreshReport:
    summaries: tuple[FamilySummary, ...]


@dataclass(frozen=True)
class DownloadReport:
    summaries: tuple[FamilySummary, ...]
    coverage: DataCoverageReport
    refused: tuple[RefusedFamily, ...] = ()


# (frame, port, inputs, symbol) -> gated frame. The real gate is bound at task 4.1.
QualityGate = Callable[
    [pd.DataFrame, FamilyPort, "DownloadInputs", "CanonicalSymbol | None"], pd.DataFrame
]


def passthrough_gate(
    frame: pd.DataFrame,
    port: FamilyPort,
    inputs: DownloadInputs,
    symbol: CanonicalSymbol | None,
) -> pd.DataFrame:
    del port, inputs, symbol
    return frame


# (canonical | None, provider_symbol, cursor_scope | None, mapped)
_Scope = tuple["CanonicalSymbol | None", str, "str | None", bool]


@dataclass(frozen=True)
class FamilyRefresher:
    """Orchestration-free refresher; ``run`` is the bound ``DOWNLOAD`` callable."""

    gate: QualityGate = passthrough_gate

    def run(self, inputs: DownloadInputs) -> FamilyRefreshReport:
        summaries: list[FamilySummary] = []
        for port in inputs.families:
            summaries.extend(self._refresh_family(port, inputs))
        return FamilyRefreshReport(tuple(summaries))

    def _scopes(self, port: FamilyPort, inputs: DownloadInputs) -> list[_Scope]:
        if port.scopes:
            return [(None, s, s, True) for s in port.scopes]
        if "symbol" in port.natural_key:
            if port.symbol_mapper is None:
                raise ValueError(
                    f"{port.writer}: symbol-keyed family must declare scopes or symbol_mapper"
                )
            scopes: list[_Scope] = []
            for sym in inputs.universe:
                provider_symbol = port.symbol_mapper(inputs.symbols, sym.canonical_id)
                if provider_symbol is None:
                    # Unmapped for this family → skip + record, never fail (R5.3, D8).
                    scopes.append((sym, "", sym.canonical_id, False))
                    continue
                scopes.append((sym, provider_symbol, sym.canonical_id, True))
            return scopes
        return [(None, "", None, True)]  # global single scope

    def _refresh_family(self, port: FamilyPort, inputs: DownloadInputs) -> list[FamilySummary]:
        client = inputs.clients.get(port.provider)
        out: list[FamilySummary] = []
        for canonical, provider_symbol, cursor_scope, mapped in self._scopes(port, inputs):
            label = cursor_scope or provider_symbol or "global"
            unavailable = _unavailable_summary(port.writer, label, mapped, client)
            if unavailable is not None:
                out.append(unavailable)
                continue
            available_client = cast("MarketDataClient", client)
            gaps = _gaps(port, inputs, cursor_scope)
            if not gaps:
                out.append(_landed_summary(port.writer, label))  # covered (R7.1)
                continue
            fetched = inserted = updated = quarantined = 0
            raw_provider_responses = usable_gap_responses = 0
            cursor: date | None = None
            for gap_start, gap_end in gaps:
                request = FetchRequest(provider_symbol, port.target_table.name, gap_start, gap_end)
                try:
                    raw = available_client.fetch(request)
                except Exception:  # noqa: BLE001 — provider/transport fault (HTTP error,
                    # missing optional SDK): fail open so one source never aborts the run (R2.4)
                    raw = pd.DataFrame()
                if raw.empty:  # fail-open: provider error/empty payload (R2.4)
                    continue
                raw_provider_responses += 1
                normalized = port.normalizer.normalize(raw, symbol=canonical, as_of=inputs.as_of)
                gated = self.gate(normalized, port, inputs, canonical)
                quarantined += len(normalized) - len(gated)  # the gate only ever drops rows
                if gated.empty:
                    continue
                usable_gap_responses += 1
                receipt = port.merge_strategy.merge(inputs.engine, port.target_table, gated)
                fetched += len(gated)
                cursor = _latest(cursor, port, gated)
                inserted, updated = inserted + receipt.inserted, updated + receipt.updated
            outcome = _provider_outcome(
                usable_gap_responses,
                raw_provider_responses,
                len(gaps),
            )
            out.append(
                FamilySummary(
                    port.writer, label, fetched, 0, inserted, updated, outcome, quarantined, cursor
                )
            )
        return out


def _latest(cursor: date | None, port: FamilyPort, frame: pd.DataFrame) -> date | None:
    """The later of the running cursor and the newest availability date in a landed frame;
    only a date axis has one (``etf_meta`` keys on ``symbol``: parsing "SPY" aborted the run)."""
    if not isinstance(port.target_table.c[port.availability_column].type, (Date, DateTime)):
        return cursor
    newest = pd.to_datetime(frame[port.availability_column]).max()
    if pd.isna(newest):
        return cursor
    landed = newest.date()
    return landed if cursor is None or landed > cursor else cursor


def _gaps(
    port: FamilyPort, inputs: DownloadInputs, scope: str | None
) -> tuple[tuple[date, date], ...]:
    if inputs.refetch:
        return (inputs.want,)
    return port.incremental.gaps(inputs.engine, port.target_table, inputs.want, scope=scope)


def _landed_summary(writer: str, label: str) -> FamilySummary:
    """The scope's window is already in the panel: nothing fetched, one skip."""
    return FamilySummary(writer, label, 0, 1, 0, 0, ProviderOutcome.ALREADY_LANDED)


def _unavailable_summary(
    writer: str,
    label: str,
    mapped: bool,
    client: MarketDataClient | None,
) -> FamilySummary | None:
    if not mapped:
        outcome = ProviderOutcome.UNMAPPED
    elif client is None:
        outcome = ProviderOutcome.CLIENT_UNAVAILABLE
    else:
        return None
    return FamilySummary(writer, label, 0, 1, 0, 0, outcome)


def _provider_outcome(
    usable_gap_responses: int, raw_provider_responses: int, requested_gaps: int
) -> ProviderOutcome:
    """Classify complete coverage by usable gaps while retaining raw-response failures."""
    if not requested_gaps:
        # Nothing was missing, so nothing was asked for. Checked FIRST because the fetch
        # loop never runs here and all three counts stay zero, which used to fall through
        # to PROVIDER_UNAVAILABLE — reporting an up-to-date symbol as a provider outage.
        # A refresh of the full universe called 581 of 945 scopes unavailable that way,
        # and left ^GSPC and ^VIX flagged uncovered while both held data through the
        # provider's own latest date.
        return ProviderOutcome.ALREADY_LANDED
    if usable_gap_responses == requested_gaps:
        return ProviderOutcome.PROVIDER_VERIFIED
    if usable_gap_responses:
        return ProviderOutcome.PARTIAL_PROVIDER_COVERAGE
    if raw_provider_responses:
        return ProviderOutcome.QUALITY_REJECTED
    return ProviderOutcome.PROVIDER_UNAVAILABLE
