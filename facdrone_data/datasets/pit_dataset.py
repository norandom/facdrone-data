# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""As-of point-in-time read gateway — Path A (task 8.1; R4.1, R8.3).

``PITDataset`` is a custom Kedro dataset that wraps an underlying panel and, on
load, returns ONLY the rows knowable at its as-of date by delegating to the
foundation's ``point_in_time_view`` (``knowledge_date <= as_of`` plus the
``matures_on`` forward-window gate). The as-of date is resolved from the run's
``${runtime_params:as_of_date}`` in the catalog; the dataset has no API that
yields a later-dated row, and saving is unsupported — writes go through the
idempotent merge node. This is the only sanctioned read-path view of a
time series (the ``before_dataset_loaded`` tripwire and the import contract keep
raw panels out of the read path).

Infrastructure tier (a Kedro dataset), not library/engine: it performs IO and
composes the foundation PIT filter.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

import pandas as pd
from kedro.io import AbstractDataset, DatasetError

from facdrone_data.pit import point_in_time_view


def _coerce_date(value: str | date) -> date:
    """Accept an ISO string (e.g. a CLI runtime param) or a date/datetime."""
    return pd.Timestamp(value).date()


def _as_of_datetime(as_of_date: date) -> datetime:
    """End-of-day UTC: a value knowable any time on the as-of date is visible,
    and ``as_of.date()`` equals the as-of date for the maturity gate."""
    return datetime.combine(as_of_date, time.max, tzinfo=UTC)


class PITDataset(AbstractDataset[pd.DataFrame, pd.DataFrame]):
    """Wrap a panel dataset; load returns only as-of-knowable rows (R4.1)."""

    def __init__(
        self,
        *,
        dataset: dict[str, Any] | AbstractDataset[Any, Any],
        as_of_date: str | date,
    ) -> None:
        self._as_of_date = _coerce_date(as_of_date)
        self._wrapped: AbstractDataset[Any, Any] = (
            dataset
            if isinstance(dataset, AbstractDataset)
            else AbstractDataset.from_config("pit_wrapped", dataset)
        )

    def _load(self) -> pd.DataFrame:
        frame = self._wrapped.load()
        return point_in_time_view(frame, _as_of_datetime(self._as_of_date))

    def _save(self, _data: pd.DataFrame) -> None:
        raise DatasetError(
            "PITDataset is a read-only as-of gateway; writes go through the merge node"
        )

    def _describe(self) -> dict[str, Any]:
        return {
            "as_of_date": self._as_of_date.isoformat(),
            "wrapped": type(self._wrapped).__name__,
        }
