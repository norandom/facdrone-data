# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 8.1: the as-of PIT dataset, Path A (Req 4.1, 8.3).

PITDataset wraps an underlying panel and, on load, returns only
``knowledge_date <= as_of`` rows by delegating to the foundation's
``point_in_time_view``. There is no API that yields a later-dated row, and it is
read-only (writes go through the merge node).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from kedro.io import AbstractDataset, DatasetError, MemoryDataset

from facdrone_data.datasets.pit_dataset import PITDataset


def _pit(dataset: dict[str, Any] | AbstractDataset[Any, Any], as_of_date: str) -> PITDataset:
    # kedro's AbstractDataset declares load/save abstract and synthesizes them
    # from _load/_save via __init_subclass__ at runtime; mypy can't follow that
    # framework magic, so the instantiation needs a localized ignore.
    return PITDataset(dataset=dataset, as_of_date=as_of_date)  # type: ignore[abstract]


def _panel() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["X", "X", "X"],
            "value": [1, 2, 3],
            "knowledge_date": pd.to_datetime(["2024-01-01", "2024-06-01", "2024-12-01"], utc=True),
        }
    )


def test_load_returns_only_rows_knowable_at_the_as_of_date() -> None:
    out = _pit(MemoryDataset(data=_panel()), "2024-06-15").load()
    assert list(out["value"]) == [1, 2]  # the 2024-12-01 row is in the future → excluded


def test_a_later_as_of_reveals_more_rows_an_earlier_one_fewer() -> None:
    panel = _panel()
    early = _pit(MemoryDataset(data=panel), "2024-01-15").load()
    late = _pit(MemoryDataset(data=panel), "2025-01-01").load()
    assert list(early["value"]) == [1]
    assert list(late["value"]) == [1, 2, 3]  # the only access path is the as-of filter


def test_unmatured_forward_window_rows_are_excluded() -> None:
    # point_in_time_view also gates on matures_on (the forward-window close).
    panel = pd.DataFrame(
        {
            "symbol": ["X", "X"],
            "value": [1, 2],
            "knowledge_date": pd.to_datetime(["2024-01-01", "2024-01-01"], utc=True),
            "matures_on": pd.to_datetime(["2024-03-01", "2025-03-01"]),
        }
    )
    out = _pit(MemoryDataset(data=panel), "2024-06-15").load()
    assert list(out["value"]) == [1]  # the window maturing 2025-03-01 has not closed


def test_dataset_is_read_only() -> None:
    ds = _pit(MemoryDataset(data=_panel()), "2024-06-15")
    with pytest.raises(DatasetError, match="read-only"):
        ds.save(_panel())


def test_builds_the_wrapped_dataset_from_a_catalog_config(tmp_path: Path) -> None:
    fp = tmp_path / "panel.parquet"
    _panel().to_parquet(fp)
    ds = _pit({"type": "pandas.ParquetDataset", "filepath": str(fp)}, "2024-06-15")
    assert list(ds.load()["value"]) == [1, 2]
