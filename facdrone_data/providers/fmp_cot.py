# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""FMP commitment-of-traders transport: COT report + COT analysis (R2.2, R2.4).

One ``MarketDataClient`` over the two FMP COT endpoints:

* ``fmp_cot`` → ``/commitment-of-traders-report`` (a ``limit``-week window).
* ``fmp_cot_analysis`` → ``/commitment-of-traders-analysis`` (full snapshot).

The HTTP call is the injected ``HttpJson`` transport (so the adapter is pure and
offline-testable) and goes through the shared ``fmp_get`` wrapper, which fails
open to ``[]`` so one bad family never aborts the download.

``fetch`` returns the RAW FMP records as a DataFrame **faithfully** — it does not
pre-fill defaults. The COT normalizers reconstruct the missing-key semantics
from the columnar frame (a per-row ``NaN`` means an absent key), and the analysis
normalizer reads the misspelled FMP key ``netPostion`` (sic), so the raw frame
must carry the FMP field names verbatim. ``provider_symbol`` is the FMP COT symbol
the caller resolved via ``to_fmp``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from facdrone_data.providers.fmp_endpoints import HttpJson, fmp_get
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from facdrone_data_types.ports import FetchRequest

# Default window: the report is a weekly series,
# fetched as the most-recent ``limit`` weeks. ~52 weeks ≈ one year of reports.
_COT_LIMIT_WEEKS = 52


@dataclass(frozen=True)
class FmpCotClient:
    """``MarketDataClient`` for the FMP commitment-of-traders endpoints.

    Serves ``fmp_cot`` and ``fmp_cot_analysis``; any other dataset returns an
    empty frame so the refresher fails open and skips it. Returns the raw FMP
    records unmodified — the per-family normalizer shapes them (and depends on
    the verbatim FMP field names, including the misspelled ``netPostion``)."""

    transport: HttpJson
    api_key: str

    @property
    def provider(self) -> Provider:
        return Provider.FMP

    def fetch(self, req: FetchRequest) -> pd.DataFrame:
        if req.dataset == "fmp_cot":
            records = fmp_get(
                self.transport,
                "/commitment-of-traders-report",
                {"symbol": req.provider_symbol, "limit": str(_COT_LIMIT_WEEKS)},
                api_key=self.api_key,
            )
            return pd.DataFrame(records)
        if req.dataset == "fmp_cot_analysis":
            records = fmp_get(
                self.transport,
                "/commitment-of-traders-analysis",
                {"symbol": req.provider_symbol},
                api_key=self.api_key,
            )
            return pd.DataFrame(records)
        return pd.DataFrame()  # not a COT dataset → fail-open skip

    def datasets(self) -> tuple[str, ...]:
        return ("fmp_cot", "fmp_cot_analysis")


def live_fmp_cot(api_key: str) -> FmpCotClient:
    """Build the live client over the ``requests``-based FMP transport.

    Wired only at the data composition root; ``requests`` is imported lazily
    inside ``live_http_json`` so this module carries no network import."""
    from facdrone_data.providers.fmp_endpoints import live_http_json

    return FmpCotClient(transport=live_http_json(), api_key=api_key)
