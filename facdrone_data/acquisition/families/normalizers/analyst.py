# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Analyst-family normalizers + the analyst FamilyPorts (R2.1, R2.3; D6, D7).

Three FMP analyst writers, all keyed by the **canonical YF symbol** (``to_fmp`` is
identity for the covered equities/indices, so the panel stores the canonical id):

* ``refresh_analyst_estimates`` → ``fmp_analyst_estimates`` — ``/analyst-estimates``;
  OR-coalesces the new vs legacy FMP field names per metric
  (``revenueAvg or estimatedRevenueAvg`` …); the new API returns the bare field,
  older payloads the ``estimated*`` alias.
* ``refresh_analyst_grades`` → ``fmp_analyst_grades`` — monthly consensus counts;
  truncates ``date`` to 10 chars, drops empty-date rows, and ``int(x or 0)`` each
  grade bucket.
* ``refresh_grade_actions`` → ``fmp_grade_actions`` — individual upgrade/downgrade
  actions; truncates ``date`` to 10 chars and drops empty-date rows.

Each payload quirk lives in the normalizers below. The shared, quirk-free mechanics
(date coercion, ``_updated_at`` stamping, table projection) live in ``_base``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from facdrone_data.acquisition.families.cursors import FullWindow
from facdrone_data.acquisition.families.normalizers._base import (
    coerce_date,
    dedup_on_primary_key,
    project_to_table,
    stamp_updated_at,
)
from facdrone_data.acquisition.families.ports import FamilyPort
from facdrone_data.acquisition.families.symbol_mapping import ProviderSymbols
from facdrone_data.acquisition.merge import UpsertPanel
from facdrone_data.acquisition.schema import (
    FMP_ANALYST_ESTIMATES,
    FMP_ANALYST_GRADES,
    FMP_GRADE_ACTIONS,
)
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from datetime import datetime

    from facdrone_data_types.symbols import CanonicalSymbol


def _coalesce(raw: pd.DataFrame, primary: str, fallback: str) -> pd.Series:
    """``r.get(primary) or r.get(fallback)`` semantics: the new FMP field, else the legacy
    ``estimated*`` alias, else NULL. A missing column is treated as all-absent.

    The dict form ``.get()`` treats an absent key as ``None`` and ``None or x``
    yields ``x``; in the DataFrame an absent per-row value is ``NaN``, so NaN (and
    the other falsy values 0/``None``) on the left must fall through to the right."""
    left = raw[primary] if primary in raw.columns else pd.Series(index=raw.index, dtype="object")
    right = raw[fallback] if fallback in raw.columns else pd.Series(index=raw.index, dtype="object")
    # Falsy-coalescing like Python ``a or b``: NaN/None/0/"" on the left fall through.
    keep_left = left.map(lambda v: bool(v) if pd.notna(v) else False)
    return left.where(keep_left, right)


def _truncate_date10(series: pd.Series) -> pd.Series:
    """``r.get("date", "")[:10]`` semantics: keep the leading ``YYYY-MM-DD`` of a stamp."""
    return series.fillna("").astype(str).str.slice(0, 10)


@dataclass(frozen=True)
class AnalystEstimatesNormalizer:
    """FMP ``/analyst-estimates`` → ``fmp_analyst_estimates`` (R2.3).

    Applies a per-metric OR-coalesce of the new vs legacy field names and the
    string→date coercion of the ``date`` column."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        if raw.empty or symbol is None:
            return pd.DataFrame(columns=[c.name for c in FMP_ANALYST_ESTIMATES.columns])
        out = pd.DataFrame(index=raw.index)
        out["symbol"] = symbol.canonical_id
        out["date"] = coerce_date(raw["date"])
        out["estimated_revenue_avg"] = _coalesce(raw, "revenueAvg", "estimatedRevenueAvg")
        out["estimated_revenue_high"] = _coalesce(raw, "revenueHigh", "estimatedRevenueHigh")
        out["estimated_revenue_low"] = _coalesce(raw, "revenueLow", "estimatedRevenueLow")
        out["estimated_ebitda_avg"] = _coalesce(raw, "ebitdaAvg", "estimatedEbitdaAvg")
        out["estimated_eps_avg"] = _coalesce(raw, "epsAvg", "estimatedEpsAvg")
        out["estimated_eps_high"] = _coalesce(raw, "epsHigh", "estimatedEpsHigh")
        out["estimated_eps_low"] = _coalesce(raw, "epsLow", "estimatedEpsLow")
        out["number_analysts_estimated_revenue"] = (
            raw["numberAnalystsEstimatedRevenue"]
            if "numberAnalystsEstimatedRevenue" in raw.columns
            else None
        )
        out["number_analysts_estimated_eps"] = (
            raw["numberAnalystsEstimatedEps"]
            if "numberAnalystsEstimatedEps" in raw.columns
            else None
        )
        stamped = stamp_updated_at(out, FMP_ANALYST_ESTIMATES, as_of)
        projected = project_to_table(stamped, FMP_ANALYST_ESTIMATES)
        return dedup_on_primary_key(projected, FMP_ANALYST_ESTIMATES)


@dataclass(frozen=True)
class AnalystGradesNormalizer:
    """FMP ``/grades-historical`` → ``fmp_analyst_grades`` (R2.3).

    Applies a 10-char date truncation, empty-date drop, and ``int(x or 0)`` per
    grade bucket."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        if raw.empty or symbol is None:
            return pd.DataFrame(columns=[c.name for c in FMP_ANALYST_GRADES.columns])
        date10 = _truncate_date10(raw["date"])
        kept = raw[date10 != ""].copy()  # skip rows with an empty date
        if kept.empty:
            return pd.DataFrame(columns=[c.name for c in FMP_ANALYST_GRADES.columns])
        out = pd.DataFrame(index=kept.index)
        out["symbol"] = symbol.canonical_id
        out["date"] = coerce_date(_truncate_date10(kept["date"]))
        out["strong_buy"] = _int_or_zero(kept, "analystRatingsStrongBuy")
        out["buy"] = _int_or_zero(kept, "analystRatingsBuy")
        out["hold"] = _int_or_zero(kept, "analystRatingsHold")
        out["sell"] = _int_or_zero(kept, "analystRatingsSell")
        out["strong_sell"] = _int_or_zero(kept, "analystRatingsStrongSell")
        stamped = stamp_updated_at(out, FMP_ANALYST_GRADES, as_of)
        projected = project_to_table(stamped, FMP_ANALYST_GRADES)
        return dedup_on_primary_key(projected, FMP_ANALYST_GRADES)


@dataclass(frozen=True)
class GradeActionsNormalizer:
    """FMP ``/grades`` → ``fmp_grade_actions`` (R2.3).

    Applies a 10-char date truncation and empty-date drop; the grade/action
    strings default to ``""`` (``r.get(..., "")`` semantics)."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        if raw.empty or symbol is None:
            return pd.DataFrame(columns=[c.name for c in FMP_GRADE_ACTIONS.columns])
        date10 = _truncate_date10(raw["date"])
        kept = raw[date10 != ""].copy()  # skip rows with an empty date
        if kept.empty:
            return pd.DataFrame(columns=[c.name for c in FMP_GRADE_ACTIONS.columns])
        out = pd.DataFrame(index=kept.index)
        out["symbol"] = symbol.canonical_id
        out["date"] = coerce_date(_truncate_date10(kept["date"]))
        out["grading_company"] = _str_or_empty(kept, "gradingCompany")
        out["previous_grade"] = _str_or_empty(kept, "previousGrade")
        out["new_grade"] = _str_or_empty(kept, "newGrade")
        out["action"] = _str_or_empty(kept, "action")
        stamped = stamp_updated_at(out, FMP_GRADE_ACTIONS, as_of)
        projected = project_to_table(stamped, FMP_GRADE_ACTIONS)
        return dedup_on_primary_key(projected, FMP_GRADE_ACTIONS)


def _int_or_zero(raw: pd.DataFrame, field: str) -> pd.Series:
    """``int(r.get(field, 0) or 0)`` semantics: missing/NaN/falsy becomes integer 0."""
    if field not in raw.columns:
        return pd.Series(0, index=raw.index, dtype="int64")
    return pd.to_numeric(raw[field], errors="coerce").fillna(0).astype("int64")


def _str_or_empty(raw: pd.DataFrame, field: str) -> pd.Series:
    """``r.get(field, "")`` semantics: missing/NaN becomes the empty string."""
    if field not in raw.columns:
        return pd.Series("", index=raw.index, dtype="object")
    return raw[field].fillna("").astype(str)


def analyst_families() -> list[FamilyPort]:
    """The three FMP analyst ports — estimates, consensus grades, grade actions (R2.1).

    All carry ``FullWindow`` (the endpoint returns full history; the upsert dedups),
    ``UpsertPanel`` (analyst rows are revised over time), and map via ``to_fmp``
    (identity for the covered equities/indices)."""
    return [
        FamilyPort(
            writer="refresh_analyst_estimates",
            target_table=FMP_ANALYST_ESTIMATES,
            natural_key=("symbol", "date"),
            provider=Provider.FMP,
            incremental=FullWindow(),
            merge_strategy=UpsertPanel(),
            normalizer=AnalystEstimatesNormalizer(),
            availability_column="date",
            quality_policy_default="keep_all",
            symbol_mapper=ProviderSymbols.fmp,
        ),
        FamilyPort(
            writer="refresh_analyst_grades",
            target_table=FMP_ANALYST_GRADES,
            natural_key=("symbol", "date"),
            provider=Provider.FMP,
            incremental=FullWindow(),
            merge_strategy=UpsertPanel(),
            normalizer=AnalystGradesNormalizer(),
            availability_column="date",
            quality_policy_default="keep_all",
            symbol_mapper=ProviderSymbols.fmp,
        ),
        FamilyPort(
            writer="refresh_grade_actions",
            target_table=FMP_GRADE_ACTIONS,
            natural_key=("symbol", "date", "grading_company"),
            provider=Provider.FMP,
            incremental=FullWindow(),
            merge_strategy=UpsertPanel(),
            normalizer=GradeActionsNormalizer(),
            availability_column="date",
            quality_policy_default="keep_all",
            symbol_mapper=ProviderSymbols.fmp,
        ),
    ]
