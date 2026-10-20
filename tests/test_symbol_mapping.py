# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 3.5: provider symbol mapping ported verbatim (R5.1, R5.2, R5.3)."""

from __future__ import annotations

import pytest

from facdrone_data.acquisition.families.symbol_mapping import (
    cftc_market_to_yf,
    has_cot,
    to_duka,
    to_fmp,
    to_fmp_price,
)
from facdrone_data.acquisition.instrument_table import InstrumentTable
from facdrone_data_types.enums import AssetClass
from futures_support import IDENTITY_PATHS

SHIPPED = InstrumentTable(IDENTITY_PATHS)


@pytest.mark.parametrize(
    ("symbol", "expected"),
    [
        ("EURUSD", "EURUSD"),  # facdrone canonical forex IS a Dukascopy name
        ("XAUUSD", "XAUUSD"),  # canonical gold spot maps directly
        ("GC=F", "XAUUSD"),  # gold future → Dukascopy metal (on the tick feed)
        ("ZW=F", None),  # wheat maps to WHTUSD but that is NOT on the tick feed → skip
        ("EURUSD=X", "EURUSD"),  # YF forex suffix form
        ("AAPL", None),  # equity: not on the tick feed → skip
        ("UNKNOWN", None),
    ],
)
def test_to_duka_mapping(symbol: str, expected: str | None) -> None:
    assert to_duka(symbol) == expected


@pytest.mark.parametrize(
    ("yf", "expected"),
    [
        ("GC=F", "GCUSD"),  # gold: explicit table
        ("KC=F", "KCUSX"),  # coffee: USX suffix
        ("EURUSD=X", "EURUSD"),  # forex strip
        ("BTC-USD", "BTCUSD"),  # crypto dash drop
        ("^GDAXI", "^GDAXI"),  # index identity
        ("AAPL", "AAPL"),  # equity identity
        ("DX-Y.NYB", "DX-Y.NYB"),  # dollar index passthrough
    ],
)
def test_to_fmp_price_mapping(yf: str, expected: str) -> None:
    assert to_fmp_price(yf, SHIPPED) == expected


def test_to_fmp_price_returns_none_for_wheat() -> None:
    assert to_fmp_price("ZW=F", SHIPPED) is None  # routes to the yfinance fallback (D8)


@pytest.mark.parametrize(
    ("yf", "expected"),
    [("GC=F", "GC"), ("CL=F", "CL"), ("ZW=F", "ZW"), ("ALV.DE", "ALV.DE"), ("UNKNOWN", "UNKNOWN")],
)
def test_to_fmp_fundamentals_cot_mapping(yf: str, expected: str) -> None:
    assert to_fmp(yf) == expected


def test_has_cot_is_true_only_for_commodities_in_the_table() -> None:
    assert has_cot("GC=F", AssetClass.COMMODITIES) is True
    assert has_cot("GC=F", AssetClass.INDICES) is False  # right symbol, wrong class
    assert has_cot("AAPL", AssetClass.COMMODITIES) is False  # not in the table


def test_cftc_map_folds_multiple_market_names_to_one_symbol() -> None:
    # Two crude names fold to CL=F; two natural-gas names fold to NG=F (R5.2).
    assert cftc_market_to_yf("CRUDE OIL, LIGHT SWEET - NEW YORK MERCANTILE EXCHANGE") == "CL=F"
    assert cftc_market_to_yf("WTI FINANCIAL CRUDE OIL - NEW YORK MERCANTILE EXCHANGE") == "CL=F"
    assert cftc_market_to_yf("NATURAL GAS - NEW YORK MERCANTILE EXCHANGE") == "NG=F"
    assert cftc_market_to_yf("E-MINI NATURAL GAS - NEW YORK MERCANTILE EXCHANGE") == "NG=F"


def test_cftc_unmapped_market_returns_none_for_skip() -> None:
    assert cftc_market_to_yf("LEAN HOGS - CHICAGO MERCANTILE EXCHANGE") is None  # R5.3
