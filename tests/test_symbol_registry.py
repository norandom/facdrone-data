# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Provider translation over injected and requirement-derived canonical symbols."""

from __future__ import annotations

import pytest

from facdrone_data.acquisition.families.symbol_mapping import ProviderSymbols
from facdrone_data.acquisition.instrument_table import InstrumentTable
from facdrone_data.acquisition.symbol_registry import (
    DerivedSymbolRegistry,
    InMemorySymbolRegistry,
    SymbolConfigError,
    SymbolMapping,
)
from facdrone_data.testing import EURUSD
from facdrone_data_types.enums import AssetClass
from facdrone_data_types.ports import SymbolRegistry
from facdrone_data_types.symbols import CanonicalSymbol, Provider

XAUUSD = CanonicalSymbol(
    canonical_id="XAUUSD", asset_class=AssetClass.PRECIOUS_METALS, profit_ccy="USD"
)


def _good_registry() -> InMemorySymbolRegistry:
    return InMemorySymbolRegistry(
        [
            SymbolMapping(EURUSD, {Provider.YFINANCE: "EURUSD=X", Provider.DUKASCOPY: "EURUSD"}),
            SymbolMapping(XAUUSD, {Provider.YFINANCE: "GC=F", Provider.DUKASCOPY: "XAUUSD"}),
        ]
    )


def test_is_a_symbol_registry() -> None:
    assert isinstance(_good_registry(), SymbolRegistry)


def test_translates_to_and_from_provider() -> None:
    reg = _good_registry()
    assert reg.to_provider(EURUSD, Provider.YFINANCE) == "EURUSD=X"
    assert reg.to_provider(EURUSD, Provider.DUKASCOPY) == "EURUSD"
    assert reg.to_canonical("GC=F", Provider.YFINANCE) == XAUUSD


def test_round_trips_every_instrument() -> None:
    reg = _good_registry()
    for sym in reg.all():
        for provider in (Provider.YFINANCE, Provider.DUKASCOPY):
            assert reg.to_canonical(reg.to_provider(sym, provider), provider) == sym


def test_validate_passes_on_a_clean_config() -> None:
    _good_registry().validate()  # no raise


def test_missing_required_provider_fails_loud_naming_symbol_and_provider() -> None:
    reg = _good_registry()
    with pytest.raises(SymbolConfigError) as exc:
        reg.to_provider(EURUSD, Provider.FMP)
    assert "EURUSD" in str(exc.value) and "fmp" in str(exc.value)


def test_unknown_provider_symbol_fails_loud() -> None:
    reg = _good_registry()
    with pytest.raises(SymbolConfigError, match="UNMAPPED"):
        reg.to_canonical("UNMAPPED", Provider.YFINANCE)


def test_validate_rejects_a_duplicate_provider_symbol() -> None:
    reg = InMemorySymbolRegistry(
        [
            SymbolMapping(EURUSD, {Provider.YFINANCE: "EURUSD=X"}),
            # GBPUSD wrongly claims the same yfinance symbol as EURUSD.
            SymbolMapping(
                CanonicalSymbol(
                    canonical_id="GBPUSD", asset_class=AssetClass.FOREX, profit_ccy="USD"
                ),
                {Provider.YFINANCE: "EURUSD=X"},
            ),
        ]
    )
    with pytest.raises(SymbolConfigError, match="duplicate"):
        reg.validate()


def test_validate_rejects_a_duplicate_canonical_id() -> None:
    reg = InMemorySymbolRegistry(
        [
            SymbolMapping(EURUSD, {Provider.YFINANCE: "EURUSD=X"}),
            SymbolMapping(EURUSD, {Provider.DUKASCOPY: "EURUSD"}),
        ]
    )
    with pytest.raises(SymbolConfigError, match="EURUSD"):
        reg.validate()


def test_derived_registry_uses_the_required_canonical_symbols_without_a_config_list() -> None:
    eurhkd = CanonicalSymbol("EURHKD=X", AssetClass.FOREX, "HKD")
    reg = DerivedSymbolRegistry((eurhkd,), ProviderSymbols(InstrumentTable(())))

    assert reg.all() == (eurhkd,)
    assert reg.to_provider(eurhkd, Provider.YFINANCE) == "EURHKD=X"
    assert reg.to_provider(eurhkd, Provider.FMP) == "EURHKD"
