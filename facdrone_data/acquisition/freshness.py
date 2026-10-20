# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Freshness policy components: live vs simulation (task 9; R5.1-R5.4).

The same acquisition contract, symbol model, and PIT read apply in both live
and simulation; ONLY the freshness policy differs, and it is a typed component
selected at a composition root — never a behavioral mode flag inside the
read/acquire code (R5.4):

* :class:`SimOnExisting` — use the existing stored data and acquire EXACTLY ONCE
  only when the required data is absent; never switch to refresh-on-every-read
  (R5.2, R5.3).

Acquisition is reached through an injected ``AcquireFn`` so the policy is
decoupled from concrete adapters and testable with a fake; ``as_of`` is injected
data, never a wall-clock read. ``PresenceFn`` derives "is the required data
present?" from the stored panel (coverage-from-panel, no side index).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from facdrone_data.acquisition.acquisition import AcquireDeps, acquire, derive_coverage
from facdrone_data_types.symbols import CanonicalSymbol

if TYPE_CHECKING:
    import pandas as pd

    from facdrone_data_types.ports import RegimeSource

# The injected acquisition seam: (universe, datasets, as_of) -> None (it merges).
AcquireFn = Callable[[tuple[CanonicalSymbol, ...], tuple[str, ...], date], None]
# The presence check derived from the stored panel.
PresenceFn = Callable[[tuple[CanonicalSymbol, ...], tuple[str, ...], date], bool]


@runtime_checkable
class FreshnessPolicy(Protocol):
    """A typed freshness policy (R5.4): a variant is a class, not a flag."""

    def ensure(
        self,
        universe: tuple[CanonicalSymbol, ...],
        datasets: tuple[str, ...],
        as_of: date,
        acquire: AcquireFn,
    ) -> None: ...


@dataclass(frozen=True)
class SimOnExisting:
    """Simulation: use existing data; acquire ONCE only if absent (R5.2, R5.3)."""

    is_present: PresenceFn

    def ensure(
        self,
        universe: tuple[CanonicalSymbol, ...],
        datasets: tuple[str, ...],
        as_of: date,
        acquire: AcquireFn,
    ) -> None:
        if self.is_present(universe, datasets, as_of):
            return  # required data present: no provider contact (R5.2)
        acquire(universe, datasets, as_of)  # acquire-once, not refresh-on-every-read (R5.3)


# ── Composition-root scaffolding (task 11.3; R5.4) ──────────────────
# The live and sim roots over ONE shared data layer differ ONLY in the typed
# FreshnessPolicy they bind (and, where relevant, the RegimeSource). No mode
# flag: each root constructs its own policy instance and calls compose_root.


@dataclass(frozen=True)
class DataRootInputs:
    """Everything a composition root binds for one run (no module globals, R5.4)."""

    universe: tuple[CanonicalSymbol, ...]
    datasets: tuple[str, ...]
    window: tuple[date, date]  # (start, as_of) acquisition window; as_of is the clock
    panel: pd.DataFrame
    deps: AcquireDeps
    regime_source: RegimeSource


@dataclass(frozen=True)
class DataRoot:
    """The composed data layer: the bound freshness policy + ``RegimeSource``."""

    policy: FreshnessPolicy
    regime_source: RegimeSource


def ensure_fresh(
    policy: FreshnessPolicy,
    universe: tuple[CanonicalSymbol, ...],
    datasets: tuple[str, ...],
    as_of: date,
    acquire_fn: AcquireFn,
) -> None:
    """Run the policy's ensure-before-decide step (shared by both roots)."""
    policy.ensure(universe, datasets, as_of, acquire_fn)


def panel_presence(panel: pd.DataFrame) -> PresenceFn:
    """A presence check derived from the stored panel (coverage-from-panel, no
    side index): every universe symbol has coverage spanning the as-of date."""

    def is_present(
        universe: tuple[CanonicalSymbol, ...], datasets: tuple[str, ...], as_of: date
    ) -> bool:
        del datasets
        return all(
            any(
                start <= as_of <= end
                for start, end in derive_coverage(panel, s.canonical_id).present
            )
            for s in universe
        )

    return is_present


def _acquire_fn(inputs: DataRootInputs) -> AcquireFn:
    start, _ = inputs.window

    def refresh(
        universe: tuple[CanonicalSymbol, ...], datasets: tuple[str, ...], as_of: date
    ) -> None:
        for dataset in datasets:
            acquire(universe, dataset, (start, as_of), inputs.deps, inputs.panel)

    return refresh


def compose_root(inputs: DataRootInputs, policy: FreshnessPolicy) -> DataRoot:
    """Ensure freshness under the bound policy, then return the composed root."""
    _, as_of = inputs.window
    ensure_fresh(policy, inputs.universe, inputs.datasets, as_of, _acquire_fn(inputs))
    return DataRoot(policy=policy, regime_source=inputs.regime_source)
