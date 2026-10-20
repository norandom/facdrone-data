# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The future profile's series declarations refuse unstated constructions (R3.4)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType

import pytest

from facdrone_data.acquisition.futures_series import (
    Adjustment,
    Construction,
    RollGaps,
    SeriesDeclaration,
    SeriesDeclarationError,
    UndeclaredSeriesError,
    load_series_declarations,
    require_declared,
)


def _entry() -> dict[str, object]:
    return {
        "construction": "provider_front_month",
        "adjustment": "none",
        "roll_gaps": "present",
        "known_at": "2026-09-24",
    }


@pytest.mark.parametrize("known_at", ["2026-09-24", date(2026, 9, 24)])
def test_loads_profile_section_as_frozen_typed_declarations(known_at: object) -> None:
    entry = _entry()
    entry["known_at"] = known_at
    declarations = load_series_declarations(
        MappingProxyType({"CL=F": MappingProxyType(entry), "GC=F": _entry()})
    )
    assert set(declarations) == {"CL=F", "GC=F"}
    assert declarations["CL=F"] == SeriesDeclaration(
        series="CL=F",
        construction=Construction.PROVIDER_FRONT_MONTH,
        adjustment=Adjustment.NONE,
        roll_gaps=RollGaps.PRESENT,
        known_at=date(2026, 9, 24),
    )
    assert declarations["GC=F"].series == "GC=F"
    assert declarations["CL=F"].construction is Construction.PROVIDER_FRONT_MONTH
    assert declarations["CL=F"].adjustment is Adjustment.NONE
    assert declarations["CL=F"].roll_gaps is RollGaps.PRESENT
    with pytest.raises(FrozenInstanceError):
        declarations["CL=F"].series = "GC=F"  # type: ignore[misc]
    entry["construction"] = "back_adjusted"
    assert declarations["CL=F"].construction is Construction.PROVIDER_FRONT_MONTH


@pytest.mark.parametrize("key", ["construction", "adjustment", "roll_gaps", "known_at"])
def test_refuses_each_missing_key_naming_the_series(key: str) -> None:
    entry = _entry()
    del entry[key]
    with pytest.raises(SeriesDeclarationError, match=rf"CL=F.*{key}"):
        load_series_declarations({"CL=F": entry})


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("construction", "continuous"),
        ("adjustment", "back_adjusted"),
        ("roll_gaps", "absent"),
        ("construction", None),
        ("adjustment", []),
        ("roll_gaps", True),
        ("known_at", "2024-02-30"),
        ("known_at", ""),
        ("known_at", None),
        ("known_at", 20260924),
        ("known_at", datetime(2026, 9, 24, 12)),
        ("unexpected", "ignored?"),
    ],
)
def test_refuses_invalid_values_naming_the_series(key: str, value: object) -> None:
    entry = _entry()
    entry[key] = value
    with pytest.raises(SeriesDeclarationError, match="CL=F"):
        load_series_declarations({"CL=F": entry})


@pytest.mark.parametrize("entry", [None, [], "provider_front_month"])
def test_refuses_a_non_mapping_entry(entry: object) -> None:
    with pytest.raises(SeriesDeclarationError, match="CL=F.*mapping"):
        load_series_declarations({"CL=F": entry})


@pytest.mark.parametrize("series", ["", "   "])
def test_refuses_an_empty_series_name(series: str) -> None:
    with pytest.raises(SeriesDeclarationError, match="series"):
        load_series_declarations({series: _entry()})


def test_admission_lists_every_undeclared_series_once() -> None:
    declarations = load_series_declarations({"CL=F": _entry()})
    require_declared(iter(["CL=F", "CL=F"]), declarations)
    require_declared([], declarations)
    with pytest.raises(UndeclaredSeriesError) as error:
        require_declared(iter(["SI=F", "CL=F", "GC=F", "SI=F"]), declarations)
    assert str(error.value) == "undeclared futures series: GC=F, SI=F"
    with pytest.raises(UndeclaredSeriesError, match="CL=F"):
        require_declared(["CL=F"], load_series_declarations({}))


def test_a_declared_roll_gap_survives_in_an_unadjusted_series() -> None:
    """A synthetic provider front-month series across one roll: the declaration says the
    series is unadjusted with roll gaps present, so the roll day keeps the whole jump (the
    contract spread plus the day's move) and nothing smooths it away."""
    rows = [
        {"symbol": "CL=F", "date": day, "close": close}
        for day, close in (
            ("2024-07-19", "100.00"),
            ("2024-07-22", "99.50"),
            ("2024-07-23", "96.50"),  # the roll: a 1.50 spread plus a 1.50 move
            ("2024-07-24", "97.10"),
            ("2024-07-25", "97.80"),
        )
    ]
    declarations = load_series_declarations({"CL=F": _entry()})
    require_declared((row["symbol"] for row in rows), declarations)
    assert declarations["CL=F"].construction is Construction.PROVIDER_FRONT_MONTH
    assert declarations["CL=F"].adjustment is Adjustment.NONE
    assert declarations["CL=F"].roll_gaps is RollGaps.PRESENT

    jump = Decimal(rows[2]["close"]) - Decimal(rows[1]["close"])
    assert jump == Decimal("-3.00")
    # The threshold separates the raw jump from the day's move alone: smoothing away the
    # contract spread would fail it.
    minimum_unadjusted_jump = Decimal("2.00")
    assert abs(jump) > minimum_unadjusted_jump
