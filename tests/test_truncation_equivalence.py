# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 14.1: lookahead truncation-equivalence across both read paths (Req 4.4).

For randomized as-of T, a read against the full panel filtered to T must equal
the read against a panel physically truncated at T — for Path A (the PITDataset
/ point_in_time_view filter) and Path B (the per-day backtest slice, which uses
the same point_in_time_view). The property fails when a future-row leak is
injected (an unfiltered read).
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest
from kedro.io import MemoryDataset

from facdrone_data.datasets.pit_dataset import PITDataset
from facdrone_data.pit import point_in_time_view

_BASE = pd.Timestamp("2024-01-01", tz="UTC")


def _panel(n: int = 60, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        [
            {
                "event_date": (_BASE + pd.Timedelta(days=i)).date(),
                "knowledge_date": _BASE + pd.Timedelta(days=i),  # 1 row/day, tz-aware
                "value": float(rng.random()),
            }
            for i in range(n)
        ]
    )


def _truncate(panel: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    visible = pd.to_datetime(panel["knowledge_date"], utc=True) <= cutoff
    return panel[visible].reset_index(drop=True)


def test_truncation_equivalence_holds_across_randomized_as_of() -> None:
    panel = _panel()
    rng = np.random.default_rng(11)
    for _ in range(25):  # randomized T spanning before/within/after the panel
        cutoff = _BASE + pd.Timedelta(days=int(rng.integers(-5, 76)))
        filtered = point_in_time_view(panel, cutoff.to_pydatetime())
        truncated = point_in_time_view(_truncate(panel, cutoff), cutoff.to_pydatetime())
        pd.testing.assert_frame_equal(filtered, truncated)


def test_path_a_pit_dataset_equals_physical_truncation() -> None:
    panel = _panel()
    as_of = date(2024, 2, 1)
    cutoff = pd.Timestamp(as_of, tz="UTC") + pd.Timedelta(hours=23, minutes=59, seconds=59)
    full = PITDataset(dataset=MemoryDataset(data=panel), as_of_date=as_of).load()  # type: ignore[abstract]
    truncated = PITDataset(  # type: ignore[abstract]
        dataset=MemoryDataset(data=_truncate(panel, cutoff)), as_of_date=as_of
    ).load()
    pd.testing.assert_frame_equal(full, truncated)


def test_a_future_row_leak_breaks_the_equivalence() -> None:
    panel = _panel()
    cutoff = _BASE + pd.Timedelta(days=30)
    correct = point_in_time_view(panel, cutoff.to_pydatetime())
    truncated = _truncate(panel, cutoff)
    pd.testing.assert_frame_equal(correct, truncated)  # honest read == truncation
    leaky = panel.reset_index(drop=True)  # a read that forgot the knowledge_date filter
    assert len(leaky) > len(truncated)  # the leak shows up as extra future rows
    with pytest.raises(AssertionError):
        pd.testing.assert_frame_equal(leaky, truncated)
