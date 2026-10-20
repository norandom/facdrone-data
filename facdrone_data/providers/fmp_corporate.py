# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""FMP corporate-action transports: analyst grades, grade actions, insider trading.

One ``MarketDataClient`` (``FmpCorporateClient``) over the injected ``HttpJson``
transport (the same wrapper ``FmpPriceClient`` uses), serving three FMP datasets:

* ``fmp_analyst_grades`` —
  ``/grades-historical`` (limit 100). Monthly consensus counts; the raw payload
  carries ``date`` + ``analystRatings*`` buckets that ``AnalystGradesNormalizer``
  reads verbatim, so ``fetch`` returns the FMP records untouched.
* ``fmp_grade_actions`` —
  ``/grades`` (limit 500). Individual upgrade/downgrade actions; the raw payload
  carries ``date`` + ``gradingCompany``/``previousGrade``/``newGrade``/``action``
  that ``GradeActionsNormalizer`` reads verbatim.
* ``fmp_insider_trading`` —
  ``/insider-trading/search`` **paged** (limit 500, ``page=0..``). Paging stops once
  a page is empty or has gone past the stored max ``filingDate``; the cursor already
  gates the window through ``req.start``, so this adapter pages and concatenates,
  short-circuiting a page whose newest filing predates ``req.start``. The raw FMP
  fields are camelCase (``filingDate`` …); ``InsiderTradingNormalizer`` reads the
  **snake_case** row keys (``filing_date`` …), so ``fetch`` renames before
  returning.

The HTTP call is the injected ``HttpJson`` transport (offline-testable); the live
``requests`` transport is ``fmp_endpoints.live_http_json`` and is wired at the
composition root. Never logs the api key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pandas as pd

from facdrone_data.providers.fmp_endpoints import HttpJson, fmp_get
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from facdrone_data_types.ports import FetchRequest

_GRADES_LIMIT = 100
_GRADE_ACTIONS_LIMIT = 500
_INSIDER_LIMIT = 500
_INSIDER_MAX_PAGES = 5

# FMP camelCase field -> the snake_case key the InsiderTradingNormalizer reads.
_INSIDER_RENAME = {
    "filingDate": "filing_date",
    "transactionDate": "transaction_date",
    "transactionType": "transaction_type",
    "securitiesTransacted": "securities_transacted",
    "price": "price",
    "reportingName": "reporting_name",
    "typeOfOwner": "type_of_owner",
    "acquisitionOrDisposition": "acquisition_or_disposition",
}
_INSIDER_COLUMNS = tuple(_INSIDER_RENAME.values())


@dataclass(frozen=True)
class FmpCorporateClient:
    """``MarketDataClient`` for FMP analyst grades, grade actions, insider trading.

    Returns RAW records as a DataFrame shaped to exactly the columns the matching
    normalizer reads; a non-corporate dataset returns empty so the refresher fails
    open and skips it."""

    transport: HttpJson
    api_key: str

    @property
    def provider(self) -> Provider:
        return Provider.FMP

    def fetch(self, req: FetchRequest) -> pd.DataFrame:
        if req.dataset == "fmp_analyst_grades":
            records = fmp_get(
                self.transport,
                "/grades-historical",
                {"symbol": req.provider_symbol, "limit": str(_GRADES_LIMIT)},
                api_key=self.api_key,
            )
            return pd.DataFrame(records)
        if req.dataset == "fmp_grade_actions":
            records = fmp_get(
                self.transport,
                "/grades",
                {"symbol": req.provider_symbol, "limit": str(_GRADE_ACTIONS_LIMIT)},
                api_key=self.api_key,
            )
            return pd.DataFrame(records)
        if req.dataset == "fmp_insider_trading":
            return self._fetch_insider(req)
        return pd.DataFrame()  # not a corporate dataset → fail-open skip

    def _fetch_insider(self, req: FetchRequest) -> pd.DataFrame:
        """Page ``/insider-trading/search`` and concatenate.

        Stop when a page is empty (no more history) or when the page's newest
        ``filingDate`` predates ``req.start`` — the cursor's stored max filing
        date — so already-stored windows are not re-paged. The newest page is page
        0; later pages are strictly older, so this is a monotone short-circuit."""
        floor = req.start.isoformat()
        frames: list[pd.DataFrame] = []
        for page in range(_INSIDER_MAX_PAGES):
            records = fmp_get(
                self.transport,
                "/insider-trading/search",
                {
                    "symbol": req.provider_symbol,
                    "limit": str(_INSIDER_LIMIT),
                    "page": str(page),
                },
                api_key=self.api_key,
            )
            if not records:
                break
            page_frame = self._shape_insider(records)
            frames.append(page_frame)
            # Short-circuit: once this page's newest filing is at/below the
            # stored max (and we are past page 0), no later page can add rows.
            newest = page_frame["filing_date"].max()
            if page > 0 and isinstance(newest, str) and newest <= floor:
                break
        if not frames:
            return self._empty_insider()
        return pd.concat(frames, ignore_index=True)

    @staticmethod
    def _shape_insider(records: list[dict[str, Any]]) -> pd.DataFrame:
        """Rename one page of raw FMP records to the snake_case the normalizer reads,
        guaranteeing every column the normalizer indexes is present (even if absent
        in the payload) so it never raises on a sparse page."""
        frame = pd.DataFrame(records).rename(columns=_INSIDER_RENAME)
        for column in _INSIDER_COLUMNS:
            if column not in frame.columns:
                frame[column] = pd.NA
        # Normalizer truncates ``filingDate[:10]``; keep it as-is for the max() gate.
        frame["filing_date"] = frame["filing_date"].fillna("").astype(str).str.slice(0, 10)
        return frame

    @staticmethod
    def _empty_insider() -> pd.DataFrame:
        return pd.DataFrame(columns=list(_INSIDER_COLUMNS))

    def datasets(self) -> tuple[str, ...]:
        return ("fmp_analyst_grades", "fmp_grade_actions", "fmp_insider_trading")
