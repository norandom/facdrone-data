# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""CFTC Commitment-of-Traders transport (R2.1, R2.3).

This module is the LIVE fetch layer for the ``cftc_cot`` family. CFTC is keyless
and *not* per-symbol: the source pulls the Legacy Futures report a full year at a
time directly from the CFTC (via ``cot_reports.cot_year``), and the market-name → YF
symbol fold happens INSIDE ``CftcCotNormalizer`` (``CFTC_MAP``), not here. So
``fetch`` ignores ``req.provider_symbol`` for symbol mapping and instead derives the
calendar years spanning ``req.start..req.end``, calls the injected transport once per
year, and concatenates the results into one raw frame.

The returned frame is the RAW shape ``CftcCotNormalizer`` reads: exactly the
``cot_reports.cot_year(..., cot_report_type="legacy_fut")`` columns the normalizer
consumes (``Market and Exchange Names``, ``As of Date in Form YYYY-MM-DD``, the
``Noncommercial``/``Commercial Positions-Long/Short (All)`` counts, ``Open Interest
(All)``, the ``% of OI`` percentages and the ``Change in Noncommercial`` deltas). The
adapter does no projection — ``cot_year`` already returns those columns — so a raw
frame is passed through untouched and the normalizer does the row-by-row fold.

The network call is an injected ``CotYearFetch`` transport so the adapter is pure and
offline-testable; the live transport (``live_cftc``) lazily imports ``cot_reports``
inside the factory, so this module imports cleanly even when the SDK is absent. The
live transport calls ``cot.cot_year(year, cot_report_type="legacy_fut", store_txt=False,
verbose=False)``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from facdrone_data_types.ports import FetchRequest

log = logging.getLogger(__name__)

# (year) -> the CFTC Legacy-Futures report for that calendar year, as the frame
# ``cot_reports.cot_year`` returns. May raise; the adapter catches per year and fails
# open (a bad year is skipped and the remaining years still load).
CotYearFetch = Callable[[int], pd.DataFrame]


def _years_in_range(start_year: int, end_year: int) -> list[int]:
    """The inclusive list of calendar years spanning a request window. ``cot_year``
    is a whole-year endpoint, so any window touching a year pulls that full year (the
    upsert dedups on the natural key, as for any ``FullWindow`` family)."""
    if start_year > end_year:
        return []
    return list(range(start_year, end_year + 1))


def live_cftc() -> CotYearFetch:
    """The live ``cot_reports``-based transport (wired only at the composition root).

    ``cot_reports`` is imported lazily so this module imports cleanly even when the
    SDK is not installed. The transport calls ``cot.cot_year(year,
    cot_report_type="legacy_fut", store_txt=False, verbose=False)``. CFTC is keyless — there is no
    API key to hold or log."""

    def _cot_year(year: int) -> pd.DataFrame:
        import cot_reports as cot  # lazy, optional SDK

        frame: pd.DataFrame = cot.cot_year(
            year,
            cot_report_type="legacy_fut",
            store_txt=False,
            verbose=False,
        )
        return frame

    return _cot_year


@dataclass(frozen=True)
class CftcCotClient:
    """``MarketDataClient`` for the CFTC ``cftc_cot`` family (R2.1).

    ``fetch`` derives the calendar years covering ``req.start..req.end``, calls the
    injected per-year transport for each, and concatenates the raw Legacy-Futures
    frames — the RAW shape ``CftcCotNormalizer`` reads (the market-name fold and the
    per-symbol keying happen inside the normalizer, so ``req.provider_symbol`` is not
    used for mapping here). A bad year fails open (it is skipped). Any
    non-``cftc_cot`` dataset returns empty → the refresher fails open and skips it."""

    transport: CotYearFetch

    @property
    def provider(self) -> Provider:
        return Provider.CFTC

    def fetch(self, req: FetchRequest) -> pd.DataFrame:
        if req.dataset != "cftc_cot":
            return pd.DataFrame()  # CFTC serves only the COT family

        frames: list[pd.DataFrame] = []
        for year in _years_in_range(req.start.year, req.end.year):
            try:
                year_frame = self.transport(year)
            except Exception as exc:  # noqa: BLE001 — fail-open per year
                log.warning("CFTC cot_year failed for %s: %s", year, exc)
                continue
            if not year_frame.empty:
                frames.append(year_frame)

        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def datasets(self) -> tuple[str, ...]:
        return ("cftc_cot",)
