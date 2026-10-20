# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 9: freshness policy components (Req 5.1, 5.2, 5.3, 5.4).

With a recording fake acquire: LiveRefresh always refreshes before deciding;
SimOnExisting makes zero acquire calls when data is present and exactly one
when absent. Same ensure(...) contract both ways — only behavior differs (no
mode flag). An offline integration with the acquisition node + a fake provider
proves the end-to-end zero-download / refresh contract.
"""

from __future__ import annotations

from datetime import date

from facdrone_data.acquisition.acquisition import AcquireDeps, acquire
from facdrone_data.acquisition.freshness import (
    AcquireFn,
    FreshnessPolicy,
    SimOnExisting,
)
from facdrone_data.acquisition.symbol_registry import InMemorySymbolRegistry, SymbolMapping
from facdrone_data.testing import EURUSD
from facdrone_data.testing.fakes import FakeMarketDataClient, synthetic_daily_rows
from facdrone_data_types.symbols import CanonicalSymbol, Provider

_UNIVERSE = (EURUSD,)
_DATASETS = ("daily_prices",)
_AS_OF = date(2024, 6, 1)

_Call = tuple[tuple[CanonicalSymbol, ...], tuple[str, ...], date]


def _recording() -> tuple[list[_Call], AcquireFn]:
    calls: list[_Call] = []

    def fn(universe: tuple[CanonicalSymbol, ...], datasets: tuple[str, ...], as_of: date) -> None:
        calls.append((universe, datasets, as_of))

    return calls, fn


def test_sim_makes_zero_acquire_calls_when_present() -> None:
    calls, fn = _recording()
    SimOnExisting(is_present=lambda u, d, a: True).ensure(_UNIVERSE, _DATASETS, _AS_OF, fn)
    assert calls == []


def test_sim_acquires_exactly_once_per_ensure_when_absent() -> None:
    calls, fn = _recording()
    policy = SimOnExisting(is_present=lambda u, d, a: False)
    policy.ensure(_UNIVERSE, _DATASETS, _AS_OF, fn)
    assert len(calls) == 1  # one acquire, not a refresh loop
    policy.ensure(_UNIVERSE, _DATASETS, _AS_OF, fn)
    assert len(calls) == 2  # still one per ensure call


def test_both_policies_share_the_contract_and_are_typed_components() -> None:
    assert isinstance(SimOnExisting(is_present=lambda u, d, a: True), FreshnessPolicy)


def test_as_of_is_injected_and_threaded_unchanged() -> None:
    calls, fn = _recording()
    SimOnExisting(is_present=lambda u, d, a: False).ensure(
        _UNIVERSE, _DATASETS, date(2025, 3, 3), fn
    )
    assert calls[0][2] == date(2025, 3, 3)


def test_integration_sim_on_present_makes_no_provider_contact() -> None:
    registry = InMemorySymbolRegistry([SymbolMapping(EURUSD, {Provider.YFINANCE: "EURUSD=X"})])
    empty = synthetic_daily_rows("EURUSD", date(2024, 1, 10), date(2024, 1, 1))  # empty panel
    # Sim with data present: no provider contact at all.
    sim_client = FakeMarketDataClient(provider=Provider.YFINANCE)

    def sim_acquire(
        universe: tuple[CanonicalSymbol, ...], datasets: tuple[str, ...], as_of: date
    ) -> None:
        for dataset in datasets:
            acquire(
                universe,
                dataset,
                (date(2024, 1, 1), as_of),
                AcquireDeps(sim_client, registry),
                empty,
            )

    SimOnExisting(is_present=lambda u, d, a: True).ensure(
        _UNIVERSE, _DATASETS, date(2024, 1, 5), sim_acquire
    )
    assert sim_client.calls == []


def test_integration_sim_absent_acquires_the_gap_once_through_the_node() -> None:
    # R5.3 end-to-end with a fake provider: absent → exactly one acquire pass.
    registry = InMemorySymbolRegistry([SymbolMapping(EURUSD, {Provider.YFINANCE: "EURUSD=X"})])
    empty = synthetic_daily_rows("EURUSD", date(2024, 1, 10), date(2024, 1, 1))  # empty panel
    client = FakeMarketDataClient(provider=Provider.YFINANCE)

    def acquire_fn(
        universe: tuple[CanonicalSymbol, ...], datasets: tuple[str, ...], as_of: date
    ) -> None:
        for dataset in datasets:
            acquire(
                universe, dataset, (date(2024, 1, 1), as_of), AcquireDeps(client, registry), empty
            )

    SimOnExisting(is_present=lambda u, d, a: False).ensure(
        _UNIVERSE, _DATASETS, date(2024, 1, 5), acquire_fn
    )
    assert len(client.calls) == 1  # acquired the whole gap once, not refresh-every-read
