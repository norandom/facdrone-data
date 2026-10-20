# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Futures catalog data must load completely or refuse the offending contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace

import pandas as pd
import pytest

from facdrone_data.acquisition.futures_specs import load_futures_contract_specs
from facdrone_data_types.instruments import FuturesContract
from futures_support import seeded_frame as seeded_frame

_UNIVERSE = {"ES=F", "CL=F", "GC=F"}


def test_seeded_catalog_loads(seeded_frame: pd.DataFrame) -> None:
    table = load_futures_contract_specs(seeded_frame)
    contracts = {row.symbol: row for row in table.rows}
    assert set(contracts) == {"ES", "MES", "CL", "GC"}
    assert contracts["MES"].multiplier == 5.0
    assert contracts["MES"].tick_value == 1.25
    assert contracts["MES"].micro_of == "ES"
    assert not {"lot", "lot_size", "volume_step", "volume_min"} & {
        field.name for field in fields(FuturesContract)
    }
    with pytest.raises(FrozenInstanceError):
        contracts["MES"].multiplier = 50.0  # type: ignore[misc]  # frozen at runtime too
    for row in table.rows:
        micro = row.limit_weight == 0.1
        assert row.commission_per_side == (0.25 if micro else 1.0)
        assert row.fees_per_side == (0.6 if micro else 2.0)
        assert row.currency == "USD"
    assert all({"fees_per_side", "initial_margin"} <= set(v) for v in seeded_frame["verify"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tick_value", 12.5),
        ("multiplier", 0.0),
        ("tick_size", -0.25),
        ("tick_value", float("inf")),
        ("limit_weight", 0.5),
        ("micro_of", "MES"),
        ("commission_per_side", None),
        ("initial_margin", float("nan")),
        ("exchange", ""),
        ("session_open", "25:00"),
        ("sizing_contract", None),
    ],
)
def test_invalid_row_is_refused_by_name(
    seeded_frame: pd.DataFrame, field: str, value: float | str | None
) -> None:
    broken = seeded_frame.astype(object)
    broken.loc[broken["symbol"] == "MES", field] = value
    with pytest.raises(ValueError, match="MES"):
        load_futures_contract_specs(broken)


def test_missing_column_is_refused_by_name(seeded_frame: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="(?s)ES.*initial_margin"):
        load_futures_contract_specs(seeded_frame.drop(columns="initial_margin"))


def test_missing_micro_parent_is_refused_by_name(seeded_frame: pd.DataFrame) -> None:
    with pytest.raises(ValueError, match="MES.*ES"):
        load_futures_contract_specs(seeded_frame.loc[seeded_frame["symbol"] != "ES"])


def test_coverage_uses_sizing_contracts_and_reports_outside_rows(
    seeded_frame: pd.DataFrame,
) -> None:
    table = load_futures_contract_specs(seeded_frame)
    report = table.coverage(iter(_UNIVERSE))
    assert set(report.with_sizing_contract) == _UNIVERSE
    assert report.without_sizing_contract == ()
    assert report.outside_universe == ()
    assert sum(row.sizing_contract for row in table.rows) == len(_UNIVERSE)

    # A standard row without its sizing micro does not count as sizing coverage.
    no_mes = load_futures_contract_specs(seeded_frame.loc[seeded_frame["symbol"] != "MES"])
    assert no_mes.coverage(_UNIVERSE).without_sizing_contract == ("ES=F",)
    restricted = table.coverage({"ES=F", "UNKNOWN=F"})
    assert restricted.with_sizing_contract == ("ES=F",)
    assert restricted.without_sizing_contract == ("UNKNOWN=F",)
    assert {row.symbol for row in restricted.outside_universe} == {
        row.symbol for row in table.rows if row.parent_series != "ES=F"
    }


def test_domain_tick_tolerance(seeded_frame: pd.DataFrame) -> None:
    mes = next(row for row in load_futures_contract_specs(seeded_frame).rows if row.symbol == "MES")
    assert replace(mes, tick_value=1.25 + 1e-10).tick_value > 1.25
    with pytest.raises(ValueError, match="MES.*tick_value"):
        replace(mes, tick_value=1.25 + 1e-8)
