# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The shared vocabulary below the kernel (facdrone-data-manager 1.1; R1.3, R1.5).

The move is a relocation: values, field order and equality are today's. The package
imports only the standard library and itself, so the data layer and the kernel can both
stand on it without either importing the other.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import fields
from datetime import date
from pathlib import Path

import pytest

import facdrone_data_types
from facdrone_data_types.enums import AssetClass
from facdrone_data_types.freshness import FreshnessState
from facdrone_data_types.fx import FxUnavailable, FxUnavailableReason
from facdrone_data_types.instruments import FuturesContract
from facdrone_data_types.ports import FetchRequest
from facdrone_data_types.series_declarations import (
    Adjustment,
    Construction,
    RollGaps,
    SeriesDeclaration,
)
from facdrone_data_types.symbols import CanonicalSymbol, Provider
from facdrone_data_types.trading_calendar import BoundaryKind

PACKAGE = Path(facdrone_data_types.__file__).parent


def test_provider_values_are_today_without_the_unreferenced_venue_member() -> None:
    assert [member.value for member in Provider] == [
        "yfinance",
        "fmp",
        "dukascopy",
        "fred",
        "cftc",
        "ken_french",
        "oecd",
        "etf",
        "aqr",
    ]


def test_enumerations_keep_their_values() -> None:
    assert [member.value for member in AssetClass] == [
        "forex",
        "indices",
        "commodities",
        "agriculture",
        "precious metals",
        "exotics",
        "equities us",
        "equities i18n",
        "crypto",
    ]
    assert [state.severity for state in FreshnessState] == [0, 1, 2, 3]
    assert FreshnessState.WARNING.permits_derived_metrics
    assert not FreshnessState.STALE.permits_derived_metrics
    assert [reason.value for reason in FxUnavailableReason] == [
        "fx.source_currency_invalid",
        "fx.quote_unusable",
        "fx.no_conversion_path",
    ]
    assert [kind.value for kind in BoundaryKind] == ["open", "flat_deadline", "close", "assessment"]
    assert (Construction.PROVIDER_FRONT_MONTH, Adjustment.NONE, RollGaps.PRESENT) == (
        "provider_front_month",
        "none",
        "present",
    )


def test_records_keep_their_field_order() -> None:
    assert [f.name for f in fields(CanonicalSymbol)] == [
        "canonical_id",
        "asset_class",
        "profit_ccy",
    ]
    assert [f.name for f in fields(FetchRequest)] == [
        "provider_symbol",
        "dataset",
        "start",
        "end",
        "interval",
    ]
    assert [f.name for f in fields(SeriesDeclaration)] == [
        "series",
        "construction",
        "adjustment",
        "roll_gaps",
        "known_at",
        "score_on",
    ]
    assert [f.name for f in fields(FxUnavailable)] == ["source_ccy", "reason"]
    assert [f.name for f in fields(FuturesContract)][:4] == [
        "symbol",
        "exchange",
        "parent_series",
        "multiplier",
    ]


def test_records_keep_their_validation() -> None:
    with pytest.raises(ValueError, match="profit_ccy"):
        CanonicalSymbol(canonical_id="EURUSD", asset_class=AssetClass.FOREX, profit_ccy="usd")
    with pytest.raises(ValueError, match="interval"):
        FetchRequest("EURUSD", "daily", date(2025, 1, 1), date(2025, 1, 2), interval="2min")


def _imported_roots(path: Path) -> set[str]:
    """Top-level modules a source file imports outside ``if TYPE_CHECKING:`` blocks."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    guarded = {
        id(node)
        for block in ast.walk(tree)
        if isinstance(block, ast.If) and ast.unparse(block.test) == "TYPE_CHECKING"
        for node in ast.walk(block)
    }
    roots: set[str] = set()
    for node in ast.walk(tree):
        if id(node) in guarded:
            continue
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def test_the_package_imports_only_the_standard_library() -> None:
    allowed = set(sys.stdlib_module_names) | {"__future__", "facdrone_data_types"}
    for path in sorted(PACKAGE.glob("*.py")):
        assert _imported_roots(path) <= allowed, path.name
