# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""FRED series transport (R2.1, R2.3).

This module is the LIVE fetch layer for the ``fred_series`` family. The family is
*series-keyed*, not symbol-keyed: ``req.provider_symbol`` is a FRED series id
(``VIXCLS``, ``T10Y2Y``, …), and ``fetch`` turns a single series' observations into
the RAW shape ``FredSeriesNormalizer`` reads.

The normalizer consumes a frame carrying ``series_id``, ``date`` and ``value``
columns (``symbol`` is ``None`` for this family). So ``fetch`` returns exactly those
columns: ``series_id`` repeated from ``provider_symbol``, ``date`` lifted from the
series index, and ``value`` from the series values.

The network call is an injected ``FredGetSeries`` transport so the adapter is pure
and offline-testable; the live transport (``live_fred``) lazily imports ``fredapi``
inside the factory, so the module imports cleanly even when the SDK is absent. The
live transport calls ``Fred(api_key).get_series(sid, observation_start=start)``,
windowing on ``observation_start``, and returns the resulting ``pd.Series``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from datetime import date

    from facdrone_data_types.ports import FetchRequest

log = logging.getLogger(__name__)

# (series_id, start, end) -> the series' observations as a pandas Series indexed by
# observation date. May raise; the adapter catches and fails open (an empty frame
# skips the series: a failed series never aborts the others).
FredGetSeries = Callable[[str, "date", "date"], "pd.Series[float]"]

# The raw columns ``FredSeriesNormalizer`` reads.
_RAW_COLUMNS = ("series_id", "date", "value")


def _series_to_raw(series_id: str, series: pd.Series[float]) -> pd.DataFrame:
    """A FRED ``pd.Series`` → the RAW ``series_id/date/value`` frame the normalizer
    reads; the normalizer drops NaN ``value`` rows itself, so this
    stays a faithful raw projection with no filtering."""
    if series.empty:
        return pd.DataFrame(columns=list(_RAW_COLUMNS))
    return pd.DataFrame(
        {
            "series_id": series_id,
            "date": series.index,
            "value": series.to_numpy(),
        }
    )


def live_fred(api_key: str) -> FredGetSeries:
    """The live ``fredapi``-based transport (wired only at the composition root).

    ``fredapi`` is imported lazily so this module imports cleanly even when the SDK
    is not installed. It calls ``Fred(api_key=...)
    .get_series(sid, observation_start=start)``; the ``api_key`` is held inside the
    closure and never logged."""

    def _get_series(series_id: str, start: date, end: date) -> pd.Series[float]:
        from fredapi import Fred  # lazy SDK import

        client = Fred(api_key=api_key)
        series: pd.Series[float] = client.get_series(
            series_id,
            observation_start=start.isoformat(),
            observation_end=end.isoformat(),
        )
        return series

    return _get_series


@dataclass(frozen=True)
class FredSeriesClient:
    """``MarketDataClient`` for the FRED ``fred_series`` family (R2.1).

    ``fetch`` returns the RAW ``series_id/date/value`` frame that
    ``FredSeriesNormalizer`` reads; ``req.provider_symbol`` is the FRED series id.
    Any non-``fred_series`` dataset returns empty → the refresher fails open and
    skips it."""

    transport: FredGetSeries

    @property
    def provider(self) -> Provider:
        return Provider.FRED

    def fetch(self, req: FetchRequest) -> pd.DataFrame:
        if req.dataset != "fred_series":
            return pd.DataFrame()  # FRED serves only the series family
        try:
            series = self.transport(req.provider_symbol, req.start, req.end)
        except Exception as exc:  # noqa: BLE001 — fail-open per series
            log.warning("FRED get_series failed for %s: %s", req.provider_symbol, exc)
            return pd.DataFrame()
        return _series_to_raw(req.provider_symbol, series)

    def datasets(self) -> tuple[str, ...]:
        return ("fred_series",)
