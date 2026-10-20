# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""FRED-family normalizer + the FRED FamilyPort (R2.1, R2.3, R6.2; D5).

``fred_series`` is a *named/series-keyed* family — NOT symbol-keyed. The scope is
a FRED series id (``VIXCLS``, ``T10Y2Y``, …), supplied via the request's ``scope``
and carried on ``raw`` in a ``series_id`` column; ``symbol`` is therefore ``None``
for this family (it has no canonical-YF axis). The normalizer:

* keys on ``series_id``,
* drops NaN observations before building the frame, and
* normalizes the date to a plain ``date``.

The 24 default series ids are the family's :attr:`FamilyPort.scopes` — the family's
scope *data*, not a behaviour flag — a fixed default list. JPY CPI
is intentionally absent: FRED stopped ingesting the OECD MEI consumer-price
dataflow in 2021, so the live JPY source is OECD SDMX via ``refresh_oecd_japan_cpi``
under the sentinel ``OECD_JPN_CPI`` (a DIFFERENT writer/port, also targeting
``fred_series`` — not registered here).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from facdrone_data.acquisition.families.cursors import FullWindow
from facdrone_data.acquisition.families.normalizers._base import (
    coerce_date,
    project_to_table,
    stamp_updated_at,
)
from facdrone_data.acquisition.families.ports import FamilyPort
from facdrone_data.acquisition.merge import UpsertPanel
from facdrone_data.acquisition.schema import FRED_SERIES
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from datetime import datetime

    from facdrone_data_types.symbols import CanonicalSymbol

# Default FRED series ids, order-preserving. JPY CPI is intentionally NOT here
# (see module docstring).
_FRED_DEFAULT_SERIES: tuple[str, ...] = (
    "VIXCLS",  # VIX close
    "BAMLH0A0HYM2",  # ICE BofA HY OAS
    "BAMLC0A0CM",  # ICE BofA IG OAS
    "T10Y2Y",  # 10Y-2Y spread
    "DFF",  # Federal Funds Effective Rate (USD overnight rate)
    "DGS10",  # 10y Treasury constant maturity
    "DGS2",  # 2y Treasury constant maturity
    "CPIAUCSL",  # USD — US All Urban Consumers CPI (monthly)
    "CP0000EZ19M086NEST",  # EUR — Eurozone 19 HICP all items (monthly)
    "GBRCPIALLMINMEI",  # GBP — UK CPI all items (monthly)
    "AUSCPIALLQINMEI",  # AUD — Australia CPI all items (quarterly)
    "CANCPIALLMINMEI",  # CAD — Canada CPI all items (monthly)
    "CHECPIALLMINMEI",  # CHF — Switzerland CPI all items (monthly)
    "ECBMRRFR",  # EUR ECB Main Refinancing Rate
    # EUR overnight risk-free rate. Added 2026-08-21 to complete the overnight set
    # beside DFF (USD) and IUDSOIA (GBP), which had no euro counterpart.
    #
    # This is the RATE. Reporting's EUR risk-free is pinned by frozen schema to the ECB
    # compounded INDEX (EST.B.EU000A2QQF08.CI from ECB_DATA_PORTAL), so this series does
    # not and must not feed it — RawIndexSnapshot forbids relabelling bytes obtained for
    # a different request or series. It serves the read path and analysis, where an
    # overnight EUR rate was previously missing.
    "ECBESTRVOLWGTTRMDMNRT",  # EUR €STR volume-weighted trimmed mean rate
    "IUDSOIA",  # GBP SONIA (BoE overnight)
    "IR3TIB01JPM156N",  # JPY 3-month interbank (monthly)
    "IR3TIB01AUM156N",  # AUD 3-month interbank (monthly)
    "IR3TIB01CAM156N",  # CAD 3-month interbank (monthly)
    "IR3TIB01NZM156N",  # NZD 3-month interbank (monthly)
    "IR3TIB01CHM156N",  # CHF 3-month interbank (monthly)
    # The macro-lens additions (equity-factors 5.1; R13.1): the four axes the
    # relative family lens declared missing — growth/activity, inflation
    # expectations, the dollar, and financial conditions — entering through
    # this existing family, no new vendor. Data-only until the lens passes
    # its census; the lens declares WHICH of these it reads.
    "INDPRO",  # USD industrial production index (monthly, growth/activity)
    "T10YIE",  # USD 10y breakeven inflation (daily, expectations)
    "DTWEXBGS",  # USD broad trade-weighted dollar index (daily)
    "NFCI",  # Chicago Fed national financial conditions index (weekly)
)


@dataclass(frozen=True)
class FredSeriesNormalizer:
    """FRED observations for ONE series → ``fred_series``.

    ``raw`` is the observation frame for a single series carrying ``series_id``,
    ``date`` and ``value`` columns (``symbol`` is ``None`` — FRED is series-keyed,
    not symbol-keyed). Behaviour: drop NaN ``value`` rows, normalize ``date``
    to a plain ``date``, key on ``series_id``."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        del symbol  # FRED is series-keyed (scope=series_id), never symbol-keyed.
        if raw.empty:
            return pd.DataFrame(columns=[c.name for c in FRED_SERIES.columns])
        out = pd.DataFrame(index=raw.index)
        out["series_id"] = raw["series_id"]
        out["date"] = coerce_date(raw["date"])
        out["value"] = pd.to_numeric(raw["value"], errors="coerce")
        # Drop NaN observations BEFORE persistence.
        out = out.dropna(subset=["value"])
        # Idempotent against a doubled observation on the natural key (R6.2).
        out = out.drop_duplicates(subset=["series_id", "date"], keep="last")
        out = out.reset_index(drop=True)
        stamped = stamp_updated_at(out, FRED_SERIES, as_of)
        return project_to_table(stamped, FRED_SERIES)


def fred_families() -> list[FamilyPort]:
    """The FRED port (R2.1): ``refresh_fred_series`` → ``fred_series``.

    A series-keyed full-history upsert: the cursor (:class:`FullWindow`) always
    fetches the requested window and the upsert dedups on ``(series_id, date)``.
    ``scopes`` is the 24 default series ids — the family iterates these, NOT the
    symbol universe (``natural_key`` has no symbol axis)."""
    return [
        FamilyPort(
            writer="refresh_fred_series",
            target_table=FRED_SERIES,
            natural_key=("series_id", "date"),
            provider=Provider.FRED,
            incremental=FullWindow(),
            merge_strategy=UpsertPanel(),
            normalizer=FredSeriesNormalizer(),
            availability_column="date",
            quality_policy_default="keep_all",
            scopes=_FRED_DEFAULT_SERIES,
        ),
    ]
