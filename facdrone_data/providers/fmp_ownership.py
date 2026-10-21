# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""FMP institutional-ownership transport — 13F holders + positions summary (R2.2).

The live fetch layer for the two quarterly FMP "ownership" datasets whose
normalizers live in ``pipelines/data/families/normalizers/ownership.py``
(:class:`InstitutionalOwnershipNormalizer`,
:class:`PositionsSummaryNormalizer`). Each ``fetch`` returns the RAW records as a
DataFrame whose columns are EXACTLY what the matching ``.normalize(raw, …)`` reads
— so the normalizer carries every provider quirk itself.

PER-YEAR/QUARTER ITERATION: both endpoints are queried per period, so the
client LOOPS over every ``(year, quarter)`` in the requested range and calls the
endpoint once per period:

* ``/institutional-ownership/extract-analytics/holder`` — many holder rows per
  period; the normalizer AGGREGATES each ``(year, quarter)`` block into one stored
  row, so the period must travel as ``year``/``quarter`` columns on every row.
* ``/institutional-ownership/symbol-positions-summary`` — one pre-aggregated row
  per period; again the period travels as ``year``/``quarter`` columns.

``fetch`` reproduces that loop: it derives the ``(year, quarter)`` pairs covering
``req.start..req.end``, fetches each, stamps ``year``/``quarter`` on every returned
row, and concatenates the blocks. The FMP payload uses camelCase keys; the
normalizers read snake_case, so ``fetch`` renames the consumed keys to the exact
snake_case names ``.normalize`` reads (e.g. ``isNew`` → ``is_new``,
``sharesNumber`` → ``shares_number``, ``investorsHolding`` → ``investors_holding``).
Unread keys are passed through untouched.

The HTTP call is the injected ``HttpJson`` transport from ``fmp_endpoints`` so the
adapter is pure and offline-testable; the live transport (``live_http_json``) is
wired at the data composition root, importing ``requests`` lazily.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from facdrone_data.providers.fmp_endpoints import HttpJson, fmp_get, live_http_json
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from datetime import date

    from facdrone_data_types.ports import FetchRequest

_INSTITUTIONAL_OWNERSHIP = "fmp_institutional_ownership"
_POSITIONS_SUMMARY = "fmp_positions_summary"

# camelCase FMP key → the snake_case column InstitutionalOwnershipNormalizer reads.
_HOLDER_RENAME = {
    "changeInSharesNumberPercentage": "change_in_shares_number_percentage",
    "isNew": "is_new",
    "isSoldOut": "is_sold_out",
    "sharesNumber": "shares_number",
    "changeInSharesNumber": "change_in_shares_number",
    "filingDate": "filing_date",
}

# camelCase FMP key → the snake_case column PositionsSummaryNormalizer reads.
_SUMMARY_RENAME = {
    "investorsHolding": "investors_holding",
    "investorsHoldingChange": "investors_holding_change",
    "newPositions": "new_positions",
    "closedPositions": "closed_positions",
    "increasedPositions": "increased_positions",
    "reducedPositions": "reduced_positions",
    "putCallRatio": "put_call_ratio",
    "putCallRatioChange": "put_call_ratio_change",
    "totalInvestedChange": "total_invested_change",
    "ownershipPercentChange": "ownership_pct_change",
}


def year_quarters(start: date, end: date) -> list[tuple[int, int]]:
    """The ``(year, quarter)`` pairs covering ``[start, end]`` inclusive, ascending.

    Equivalent to a ``for year in years: for quarter in [1, 2, 3, 4]`` loop, but
    bounded by the request window's first/last calendar quarter (a quarter is
    included whenever its 3-month span overlaps the window)."""
    if start > end:
        return []
    pairs: list[tuple[int, int]] = []
    start_q = (start.month - 1) // 3 + 1
    end_q = (end.month - 1) // 3 + 1
    for year in range(start.year, end.year + 1):
        first_q = start_q if year == start.year else 1
        last_q = end_q if year == end.year else 4
        for quarter in range(first_q, last_q + 1):
            pairs.append((year, quarter))
    return pairs


@dataclass(frozen=True)
class FmpOwnershipClient:
    """``MarketDataClient`` for the two quarterly FMP ownership datasets.

    Returns the raw FMP records as a DataFrame, looping the endpoint per
    ``(year, quarter)`` and stamping the period on every row; the per-family
    normalizer aggregates/shapes them. A dataset this adapter does not serve
    returns an empty frame so the refresher fails open and skips it."""

    transport: HttpJson
    api_key: str

    @property
    def provider(self) -> Provider:
        return Provider.FMP

    def fetch(self, req: FetchRequest) -> pd.DataFrame:
        if req.dataset == _INSTITUTIONAL_OWNERSHIP:
            return self._fetch_periodic(
                req,
                "/institutional-ownership/extract-analytics/holder",
                rename=_HOLDER_RENAME,
                params={"limit": "500", "page": "0"},
            )
        if req.dataset == _POSITIONS_SUMMARY:
            return self._fetch_periodic(
                req,
                "/institutional-ownership/symbol-positions-summary",
                rename=_SUMMARY_RENAME,
                params=None,
            )
        return pd.DataFrame()  # not a served dataset → fail-open skip

    def datasets(self) -> tuple[str, ...]:
        return (_INSTITUTIONAL_OWNERSHIP, _POSITIONS_SUMMARY)

    def _fetch_periodic(
        self,
        req: FetchRequest,
        endpoint: str,
        *,
        rename: dict[str, str],
        params: dict[str, str] | None,
    ) -> pd.DataFrame:
        """Loop the endpoint per ``(year, quarter)`` in the request window, rename
        the consumed camelCase keys, stamp ``year``/``quarter`` on every row, and
        concatenate the blocks into one raw frame (empty when nothing fetched)."""
        blocks: list[pd.DataFrame] = []
        for year, quarter in year_quarters(req.start, req.end):
            query = {
                "symbol": req.provider_symbol,
                "year": str(year),
                "quarter": str(quarter),
                **(params or {}),
            }
            records = fmp_get(self.transport, endpoint, query, api_key=self.api_key)
            if not records:
                continue
            block = pd.DataFrame(records).rename(columns=rename)
            block["year"] = year
            block["quarter"] = quarter
            blocks.append(block)
        if not blocks:
            return pd.DataFrame()
        return pd.concat(blocks, ignore_index=True)


def live_fmp_ownership(api_key: str) -> FmpOwnershipClient:
    """The live client over the FMP transport (``requests`` loads inside ``live_http_json``)."""
    return FmpOwnershipClient(transport=live_http_json(), api_key=api_key)
