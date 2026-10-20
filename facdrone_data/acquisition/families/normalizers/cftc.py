# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""CFTC Commitment-of-Traders normalizer + the COT FamilyPort (R2.3, R5.2, R6.1).

Pulls the CFTC Legacy Futures report directly (via ``cot_reports.cot_year``) — NOT FMP — and
stores it under the canonical YF commodity symbol in ``cftc_cot`` at weekly
granularity. CFTC is keyless: there is no per-symbol provider call to map; the
market-name → YF fold (``cftc_market_to_yf``/``CFTC_MAP``) happens INSIDE the
normalizer, so the port sets no ``symbol_mapper``.

Payload quirks this normalizer applies:

* The ``Market and Exchange Names`` column maps to a YF symbol via the
  deliberately non-bijective ``CFTC_MAP`` (crude/natgas names fold to one symbol,
  R5.2); a row whose market is unmapped or NA is SKIPPED, never a failure.
* ``_safe_int``/``_safe_float`` ZERO-FILL bad values (``int(float(v))`` / ``float(v)``
  on failure → ``0`` / ``0.0``), and the derived ``noncomm_net``/``comm_net`` are a
  zero-fill-before-subtract (``long_all - short_all`` after coercion).
* ``date`` comes from ``As of Date in Form YYYY-MM-DD`` (first 10 chars, then
  ``coerce_date``); a blank/NA date row is skipped.
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
from facdrone_data.acquisition.families.symbol_mapping import cftc_market_to_yf
from facdrone_data.acquisition.merge import UpsertPanel
from facdrone_data.acquisition.schema import CFTC_COT
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from datetime import datetime

    from facdrone_data_types.symbols import CanonicalSymbol


def _safe_int(value: object) -> int:
    """``int(float(v))`` — bad/NA value ZERO-FILLs to ``0`` (not NULL)."""
    try:
        return int(float(value))  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return 0


def _safe_float(value: object) -> float:
    """``float(v)`` — bad/NA value ZERO-FILLs to ``0.0`` (not NULL)."""
    try:
        return float(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return 0.0


@dataclass(frozen=True)
class CftcCotNormalizer:
    """CFTC Legacy-Futures rows → ``cftc_cot``, keyed by the canonical YF symbol.

    ``raw`` is a frame of ``cot_reports.cot_year(..., cot_report_type="legacy_fut")``
    rows. Works row by row: map the market name, skip unmapped/NA markets and
    NA dates, zero-fill-coerce every numeric, derive the nets by subtraction."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        del symbol  # CFTC is keyless: the market-name fold supplies the symbol per row
        columns = [c.name for c in CFTC_COT.columns]
        if raw.empty:
            return pd.DataFrame(columns=columns)

        rows: list[dict[str, object]] = []
        for _, r in raw.iterrows():
            market = r.get("Market and Exchange Names", "")
            yf_symbol = cftc_market_to_yf(market) if isinstance(market, str) else None
            if yf_symbol is None:  # unmapped or NA market → skip, do not fail (R5.2)
                continue

            date_val = r.get("As of Date in Form YYYY-MM-DD", "")
            if date_val is None or date_val == "" or pd.isna(date_val):
                continue

            long_all = _safe_int(r.get("Noncommercial Positions-Long (All)", 0))
            short_all = _safe_int(r.get("Noncommercial Positions-Short (All)", 0))
            comm_long = _safe_int(r.get("Commercial Positions-Long (All)", 0))
            comm_short = _safe_int(r.get("Commercial Positions-Short (All)", 0))
            oi = _safe_int(r.get("Open Interest (All)", 0))

            rows.append(
                {
                    "symbol": yf_symbol,
                    "date": str(date_val)[:10],
                    "open_interest": oi,
                    "noncomm_long": long_all,
                    "noncomm_short": short_all,
                    "noncomm_net": long_all - short_all,  # zero-fill-before-subtract
                    "comm_long": comm_long,
                    "comm_short": comm_short,
                    "comm_net": comm_long - comm_short,  # zero-fill-before-subtract
                    "pct_noncomm_long": _safe_float(r.get("% of OI-Noncommercial-Long (All)", 0)),
                    "pct_noncomm_short": _safe_float(r.get("% of OI-Noncommercial-Short (All)", 0)),
                    "change_noncomm_long": _safe_int(
                        r.get("Change in Noncommercial-Long (All)", 0)
                    ),
                    "change_noncomm_short": _safe_int(
                        r.get("Change in Noncommercial-Short (All)", 0)
                    ),
                }
            )

        if not rows:
            return pd.DataFrame(columns=columns)

        out = pd.DataFrame(rows)
        out["date"] = coerce_date(out["date"])  # weekly granularity, plain ``.dt.date``
        # The non-bijective CFTC_MAP folds several market names onto one YF symbol (e.g. both
        # crude and both natural-gas contracts), so one fetch year can emit duplicate
        # ``(symbol, date)`` natural keys. Dedup keep="last" gives the same result as a per-row
        # ``ON CONFLICT DO UPDATE`` upsert (last write wins) and
        # satisfies the ``upsert_panel`` precondition (merge.py): a duplicate PK within one VALUES
        # batch is a Postgres cardinality error, never silently coalesced.
        out = out.drop_duplicates(subset=["symbol", "date"], keep="last").reset_index(drop=True)
        stamped = stamp_updated_at(out, CFTC_COT, as_of)
        return project_to_table(stamped, CFTC_COT)


def cftc_families() -> list[FamilyPort]:
    """The CFTC COT port (group ``cftc``): ``refresh_cftc_cot`` → ``cftc_cot``.

    Keyless (no ``symbol_mapper``): the normalizer folds the market name to the YF
    symbol internally; ``FullWindow`` always fetches the requested window (the
    endpoint returns full-year history and the upsert dedups on the natural key)."""
    return [
        FamilyPort(
            writer="refresh_cftc_cot",
            target_table=CFTC_COT,
            natural_key=("symbol", "date"),
            provider=Provider.CFTC,
            incremental=FullWindow(),
            merge_strategy=UpsertPanel(),
            normalizer=CftcCotNormalizer(),
            availability_column="date",
            quality_policy_default="keep_all",
            scopes=("global",),
        ),
    ]
