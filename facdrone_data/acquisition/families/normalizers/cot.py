# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""COT-family normalizers + the COT FamilyPorts (R2.3, R6.1; D11).

Two FMP commitment-of-traders families, keyed by the **canonical YF symbol** (the
stored ``symbol`` is the YF symbol the caller passed, not the mapped FMP symbol):

* ``refresh_cot`` → ``fmp_cot`` (the raw COT report). Quirks kept for compatibility
  with earlier stored rows: the long/short operands are **zero-filled**
  (``r.get("current_long_all", 0) or 0``) and stored zero-filled, and
  ``net_position`` is the *subtraction of the zero-filled operands*
  (``long_all - short_all``) — NOT ``(long - short) or 0``. ``short_name``/``sector``
  default to ``""``.
* ``refresh_cot_analysis`` → ``fmp_cot_analysis`` (the enhanced analysis).
  Quirks kept for compatibility with earlier stored rows: the FMP payload key is **misspelled**
  ``netPostion`` (sic) → ``net_position``; ``change_short`` is **always** ``None``;
  the trend fields are cross-wired (``net_position_trend`` ← ``marketSentiment``,
  ``change_long`` ← ``changeInNetPosition``); ``reversal_trend`` is stringified.

Both write through ``UpsertPanel`` (a later report revises an earlier one) and
stamp the injected-clock ``_updated_at`` (time-as-injected-data).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from facdrone_data.acquisition.families.cursors import FullWindow, MaxDateBySymbol
from facdrone_data.acquisition.families.normalizers._base import (
    coerce_date,
    dedup_on_primary_key,
    project_to_table,
    stamp_updated_at,
)
from facdrone_data.acquisition.families.ports import FamilyPort
from facdrone_data.acquisition.families.symbol_mapping import ProviderSymbols
from facdrone_data.acquisition.merge import UpsertPanel
from facdrone_data.acquisition.schema import FMP_COT, FMP_COT_ANALYSIS
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from datetime import datetime

    from facdrone_data_types.symbols import CanonicalSymbol


def _json_records(raw: pd.DataFrame) -> list[dict[str, object]]:
    """Re-create per-row JSON dicts from the columnarized payload.

    In raw FMP JSON an absent field is a *missing key* and
    ``r.get(field, default)`` yields ``default``. A DataFrame instead materializes
    that absence as ``NaN`` in every row, which ``.get`` would surface in place of
    the default — silently defeating the ``short_name``/``sector`` ``""`` defaults
    and the operand zero-fill. Dropping NaN per row restores the missing-key
    semantics so the ``.get`` defaults apply exactly."""
    return [
        {str(k): v for k, v in r.items() if not (isinstance(v, float) and pd.isna(v))}
        for r in raw.to_dict(orient="records")
    ]


def _zero_fill(value: object) -> float:
    """``r.get(key, 0) or 0``: a missing (post-:func:`_json_records`) or falsy
    operand collapses to ``0`` before the subtraction; mirrors the JSON-dict path."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return value or 0
    raise TypeError(f"COT operand must be numeric or missing, got {type(value).__name__}")


@dataclass(frozen=True)
class CotNormalizer:
    """FMP commitment-of-traders report → ``fmp_cot``, re-keyed to the canonical symbol.

    The long/short operands are zero-filled and stored
    zero-filled, and ``net_position`` is the difference of the *zero-filled*
    operands (``long_all - short_all``), not ``(long - short) or 0``."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        if raw.empty or symbol is None:
            return pd.DataFrame(columns=[c.name for c in FMP_COT.columns])
        rows = []
        for r in _json_records(raw):
            long_all = _zero_fill(r.get("current_long_all", 0))  # zero-fill the OPERAND
            short_all = _zero_fill(r.get("current_short_all", 0))  # zero-fill the OPERAND
            rows.append(
                {
                    "symbol": symbol.canonical_id,  # store the YF symbol
                    "date": r.get("date"),
                    "short_name": r.get("short_name", ""),
                    "sector": r.get("sector", ""),
                    "current_long_all": long_all,
                    "current_short_all": short_all,
                    "change_long_all": r.get("change_long_all"),
                    "change_short_all": r.get("change_short_all"),
                    "pct_long_all": r.get("pct_long_all"),
                    "pct_short_all": r.get("pct_short_all"),
                    "net_position": long_all - short_all,  # subtract the zero-filled operands
                }
            )
        out = pd.DataFrame(rows)
        out["date"] = coerce_date(out["date"])
        stamped = stamp_updated_at(out, FMP_COT, as_of)
        projected = project_to_table(stamped, FMP_COT)
        return dedup_on_primary_key(projected, FMP_COT)


@dataclass(frozen=True)
class CotAnalysisNormalizer:
    """FMP commitment-of-traders analysis → ``fmp_cot_analysis``, re-keyed to canonical.

    The FMP key is the misspelled ``netPostion``
    (sic); ``change_short`` is always ``None``; the trend fields are cross-wired."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        if raw.empty or symbol is None:
            return pd.DataFrame(columns=[c.name for c in FMP_COT_ANALYSIS.columns])
        rows = []
        for r in _json_records(raw):
            raw_date = r.get("date", "")
            date_str = str(raw_date) if raw_date else ""
            rows.append(
                {
                    "symbol": symbol.canonical_id,  # store the YF symbol
                    "date": date_str[:10],  # first 10 chars of ``date``
                    "current_long": r.get("currentLongMarketSituation"),
                    "current_short": r.get("currentShortMarketSituation"),
                    "net_position": r.get("netPostion"),  # FMP typo: "netPostion" (sic)
                    "change_long": r.get("changeInNetPosition"),
                    "change_short": None,  # always None
                    "reversal_trend": str(r.get("reversalTrend", "")),
                    "net_position_trend": r.get("marketSentiment", ""),  # cross-wired
                    "market_situation": r.get("marketSituation", ""),
                }
            )
        out = pd.DataFrame(rows)
        out["date"] = coerce_date(out["date"])
        stamped = stamp_updated_at(out, FMP_COT_ANALYSIS, as_of)
        projected = project_to_table(stamped, FMP_COT_ANALYSIS)
        return dedup_on_primary_key(projected, FMP_COT_ANALYSIS)


def cot_families() -> list[FamilyPort]:
    """The FMP COT report (full window) and COT analysis (max-date, ≤7d skip) ports."""
    return [
        FamilyPort(
            writer="refresh_cot",
            target_table=FMP_COT,
            natural_key=("symbol", "date"),
            provider=Provider.FMP,
            incremental=FullWindow(),
            merge_strategy=UpsertPanel(),
            normalizer=CotNormalizer(),
            availability_column="date",
            quality_policy_default="keep_all",
            symbol_mapper=ProviderSymbols.fmp,
        ),
        FamilyPort(
            writer="refresh_cot_analysis",
            target_table=FMP_COT_ANALYSIS,
            natural_key=("symbol", "date"),
            provider=Provider.FMP,
            incremental=MaxDateBySymbol(skip_days=7),
            merge_strategy=UpsertPanel(),
            normalizer=CotAnalysisNormalizer(),
            availability_column="date",
            quality_policy_default="keep_all",
            symbol_mapper=ProviderSymbols.fmp,
        ),
    ]
