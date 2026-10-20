# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Fundamentals-family normalizers + their FamilyPorts (R2.1, R2.3, R6.1; D4).

Three FMP fundamentals writers, each a full-history upsert keyed on
``(symbol, date, period)`` and re-keyed to the canonical YF symbol (R4.3). The
normalizers carry these provider quirks verbatim:

* the ``fiscalYear or calendarYear`` and ``period`` defaults,
* the OR-coalesce over renamed FMP fields (``epsDiluted``/``epsdiluted``,
  ``totalStockholdersEquity``/``totalEquity``, ``evToEBITDA``/``evToEbitda``,
  ``returnOnInvestedCapital``/``roic`` …),
* the ``peRatio or 1/earningsYield`` derivation, and
* the ``book_value_per_share`` legacy fallback (D4).

``date``/``filingDate`` are not left as raw strings for Postgres to cast; they are
coerced explicitly with :func:`coerce_date` (the merge target is a typed
``Date`` column, not a free-text cast).

KEY-METRICS BVPS FALLBACK (D4): ``/key-metrics ⨝ /ratios`` is left-joined on
``(symbol, date, period)`` and the value is computed per row as
``ratios_bvps if ratios_bvps is not None else keymetrics_bookValuePerShare``. The
join is done upstream by the adapter, so ``raw`` already carries BOTH the legacy
``/key-metrics`` ``bookValuePerShare`` column AND the joined ``/ratios`` value in
``ratios_bookValuePerShare``; the normalizer reproduces the per-row fallback. On a
full ``/ratios`` outage (the joined column is entirely null) EVERY row falls back
to the legacy ``/key-metrics`` value — never NULL.
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
    FMP_BALANCE_SHEET,
    FMP_CASH_FLOW,
    FMP_EARNINGS_CALENDAR,
    FMP_INCOME_STATEMENT,
    FMP_KEY_METRICS,
)
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import Table

    from facdrone_data_types.symbols import CanonicalSymbol


def _coalesce(frame: pd.DataFrame, *names: str) -> pd.Series:
    """``r.get(a) or r.get(b) or …`` per row: the first non-null/non-falsy of
    the named columns (missing columns treated as absent). Mirrors the ``or``
    chain that tolerates FMP's renamed fields — a 0/None/NaN cell falls through to
    the next candidate exactly as Python's ``or`` would."""
    result = pd.Series([None] * len(frame), index=frame.index, dtype=object)
    for name in names:
        if name not in frame.columns:
            continue
        candidate = frame[name]
        # Truthiness (`or`): a None/NaN OR a falsy 0/"" falls through.
        fill = candidate.where(candidate.map(lambda v: bool(v) if pd.notna(v) else False))
        result = result.where(result.map(lambda v: bool(v) if pd.notna(v) else False), fill)
    return result


def _fiscal_year(frame: pd.DataFrame) -> pd.Series:
    """``r.get("fiscalYear") or r.get("calendarYear")``."""
    return _coalesce(frame, "fiscalYear", "calendarYear")


def _period(frame: pd.DataFrame, default: str) -> pd.Series:
    """``r.get("period", period)`` — default only when the key is ABSENT."""
    if "period" not in frame.columns:
        return pd.Series([default] * len(frame), index=frame.index, dtype=object)
    return frame["period"].where(frame["period"].notna(), default)


@dataclass(frozen=True)
class IncomeStatementNormalizer:
    """FMP ``/income-statement`` → ``fmp_income_statement``."""

    period_default: str = "annual"

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        if raw.empty or symbol is None:
            return pd.DataFrame(columns=[c.name for c in FMP_INCOME_STATEMENT.columns])
        out = pd.DataFrame(index=raw.index)
        out["symbol"] = symbol.canonical_id
        out["date"] = coerce_date(raw["date"])
        out["fiscal_year"] = _fiscal_year(raw)
        out["period"] = _period(raw, self.period_default)
        out["revenue"] = raw.get("revenue")
        out["gross_profit"] = raw.get("grossProfit")
        out["ebitda"] = raw.get("ebitda")
        out["operating_income"] = raw.get("operatingIncome")
        out["net_income"] = raw.get("netIncome")
        out["eps"] = raw.get("eps")
        out["eps_diluted"] = _coalesce(raw, "epsDiluted", "epsdiluted")
        out["gross_profit_ratio"] = raw.get("grossProfitRatio")
        out["operating_income_ratio"] = raw.get("operatingIncomeRatio")
        out["net_income_ratio"] = raw.get("netIncomeRatio")
        out["filing_date"] = coerce_date(raw["filingDate"])
        stamped = stamp_updated_at(out, FMP_INCOME_STATEMENT, as_of)
        projected = project_to_table(stamped, FMP_INCOME_STATEMENT)
        return dedup_on_primary_key(projected, FMP_INCOME_STATEMENT)


@dataclass(frozen=True)
class BalanceSheetNormalizer:
    """FMP ``/balance-sheet-statement`` → ``fmp_balance_sheet``."""

    period_default: str = "annual"

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        if raw.empty or symbol is None:
            return pd.DataFrame(columns=[c.name for c in FMP_BALANCE_SHEET.columns])
        out = pd.DataFrame(index=raw.index)
        out["symbol"] = symbol.canonical_id
        out["date"] = coerce_date(raw["date"])
        out["fiscal_year"] = _fiscal_year(raw)
        out["period"] = _period(raw, self.period_default)
        out["total_assets"] = raw.get("totalAssets")
        out["total_debt"] = raw.get("totalDebt")
        out["total_equity"] = _coalesce(raw, "totalStockholdersEquity", "totalEquity")
        out["cash_and_equivalents"] = raw.get("cashAndCashEquivalents")
        out["total_current_assets"] = raw.get("totalCurrentAssets")
        out["total_current_liabilities"] = raw.get("totalCurrentLiabilities")
        out["filing_date"] = coerce_date(raw["filingDate"])
        stamped = stamp_updated_at(out, FMP_BALANCE_SHEET, as_of)
        projected = project_to_table(stamped, FMP_BALANCE_SHEET)
        return dedup_on_primary_key(projected, FMP_BALANCE_SHEET)


def _pe_ratio(frame: pd.DataFrame) -> pd.Series:
    """``r.get("peRatio") or (1.0 / r["earningsYield"] if r.get("earningsYield") else None)``.

    Use ``peRatio`` when truthy; else derive ``1 / earningsYield`` when that is
    truthy (the ``if ... else None`` guards a zero/None earnings yield), else None."""
    pe = (
        frame["peRatio"]
        if "peRatio" in frame.columns
        else pd.Series([None] * len(frame), index=frame.index, dtype=object)
    )
    ey = (
        frame["earningsYield"]
        if "earningsYield" in frame.columns
        else pd.Series([None] * len(frame), index=frame.index, dtype=object)
    )
    derived = ey.map(lambda y: 1.0 / y if (pd.notna(y) and bool(y)) else None)
    pe_truthy = pe.map(lambda v: bool(v) if pd.notna(v) else False)
    return pe.where(pe_truthy, derived)


@dataclass(frozen=True)
class KeyMetricsNormalizer:
    """FMP ``/key-metrics`` (⨝ ``/ratios`` upstream) → ``fmp_key_metrics``.

    ``raw`` carries the ``/key-metrics`` fields plus the joined ``/ratios``
    ``bookValuePerShare`` in ``ratios_bookValuePerShare`` (D4). The per-row
    fallback writes the legacy ``/key-metrics`` value when the joined ``/ratios``
    value is null — so a full ``/ratios`` outage never produces a NULL bvps."""

    period_default: str = "annual"
    ratios_bvps_column: str = "ratios_bookValuePerShare"

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        if raw.empty or symbol is None:
            return pd.DataFrame(columns=[c.name for c in FMP_KEY_METRICS.columns])
        out = pd.DataFrame(index=raw.index)
        out["symbol"] = symbol.canonical_id
        out["date"] = coerce_date(raw["date"])
        out["fiscal_year"] = _fiscal_year(raw)
        out["period"] = _period(raw, self.period_default)
        out["market_cap"] = raw.get("marketCap")
        out["enterprise_value"] = raw.get("enterpriseValue")
        out["pe_ratio"] = _pe_ratio(raw)
        out["ev_to_ebitda"] = _coalesce(raw, "evToEBITDA", "evToEbitda")
        out["ev_to_sales"] = raw.get("evToSales")
        out["roic"] = _coalesce(raw, "returnOnInvestedCapital", "roic")
        out["roe"] = _coalesce(raw, "returnOnEquity", "roe")
        out["roa"] = _coalesce(raw, "returnOnAssets", "roa")
        out["current_ratio"] = raw.get("currentRatio")
        out["debt_to_equity"] = raw.get("debtToEquity")
        out["fcf_per_share"] = raw.get("freeCashFlowPerShare")
        out["fcf_yield"] = raw.get("freeCashFlowYield")
        out["dividend_yield"] = raw.get("dividendYield")
        out["payout_ratio"] = raw.get("payoutRatio")
        out["revenue_per_share"] = raw.get("revenuePerShare")
        out["book_value_per_share"] = self._book_value_per_share(raw)
        stamped = stamp_updated_at(out, FMP_KEY_METRICS, as_of)
        projected = project_to_table(stamped, FMP_KEY_METRICS)
        return dedup_on_primary_key(projected, FMP_KEY_METRICS)

    def _book_value_per_share(self, frame: pd.DataFrame) -> pd.Series:
        """``bvps if bvps is not None else r.get("bookValuePerShare")`` (D4).

        ``bvps`` is the ``/ratios`` value joined upstream; the legacy fallback is
        the ``/key-metrics`` ``bookValuePerShare``. The test for ``is not None``
        is exact (NOT truthiness): a ``/ratios`` 0.0 stays 0.0 and does NOT fall
        back. A full ``/ratios`` outage (joined column absent or all-null) makes
        every row fall back to the legacy value, never NULL."""
        legacy = (
            frame["bookValuePerShare"]
            if "bookValuePerShare" in frame.columns
            else pd.Series([None] * len(frame), index=frame.index, dtype=object)
        )
        if self.ratios_bvps_column not in frame.columns:
            return legacy
        ratios = frame[self.ratios_bvps_column]
        return ratios.where(ratios.notna(), legacy)


@dataclass(frozen=True)
class CashFlowNormalizer:
    """FMP ``/cash-flow-statement`` (⨝ income shares upstream) → ``fmp_cash_flow``.

    Fresh-path (equity-factors 1.1; R3.1). Values survive
    VERBATIM via plain column reads, never the truthiness ``_coalesce``: a
    vendor zero stock-based compensation is a value (R1.3's present-zero rule)
    and a missing column normalizes to missing, never zero. ``filing_date`` is
    the column the as-of read bounds on (R3.3); the knowledge derivation lives
    in the fundamentals channel, not here."""

    period_default: str = "annual"
    income_shares_column: str = "income_weightedAverageShsOut"

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        if raw.empty or symbol is None:
            return pd.DataFrame(columns=[c.name for c in FMP_CASH_FLOW.columns])
        out = pd.DataFrame(index=raw.index)
        out["symbol"] = symbol.canonical_id
        out["date"] = coerce_date(raw["date"])
        out["fiscal_year"] = _fiscal_year(raw)
        out["period"] = _period(raw, self.period_default)
        out["operating_cash_flow"] = raw.get("operatingCashFlow")
        out["capital_expenditure"] = raw.get("capitalExpenditure")
        out["free_cash_flow"] = raw.get("freeCashFlow")
        out["stock_based_compensation"] = raw.get("stockBasedCompensation")
        out["weighted_average_shares"] = raw.get(self.income_shares_column)
        out["filing_date"] = coerce_date(raw["filingDate"])
        stamped = stamp_updated_at(out, FMP_CASH_FLOW, as_of)
        projected = project_to_table(stamped, FMP_CASH_FLOW)
        return dedup_on_primary_key(projected, FMP_CASH_FLOW)


@dataclass(frozen=True)
class EarningsCalendarNormalizer:
    """FMP ``/earnings`` → ``fmp_earnings_calendar`` (fresh-path, equity-factors 1.1).

    The announcement date IS the row's date — the earnings/ex-earnings
    provenance split (R12.8) needs when the print happened, nothing more.
    Estimates ride along for the revisions dimension."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        if raw.empty or symbol is None:
            return pd.DataFrame(columns=[c.name for c in FMP_EARNINGS_CALENDAR.columns])
        out = pd.DataFrame(index=raw.index)
        out["symbol"] = symbol.canonical_id
        out["date"] = coerce_date(raw["date"])
        out["eps_actual"] = raw.get("epsActual")
        out["eps_estimate"] = raw.get("epsEstimated")
        out["revenue_actual"] = raw.get("revenueActual")
        out["revenue_estimate"] = raw.get("revenueEstimated")
        stamped = stamp_updated_at(out, FMP_EARNINGS_CALENDAR, as_of)
        projected = project_to_table(stamped, FMP_EARNINGS_CALENDAR)
        return dedup_on_primary_key(projected, FMP_EARNINGS_CALENDAR)


def fundamentals_families() -> list[FamilyPort]:
    """The five FMP fundamentals ports — the three ``refresh_*`` writers plus the
    two fresh-path ``acquire_*`` datasets, full-history upserts mapped via ``to_fmp``
    (R2.1, R5.2; equity-factors 1.1). The cash-flow natural key INCLUDES
    ``filing_date`` so a restatement upserts BESIDE its prior vintage (R3.2)."""
    return [
        _port("refresh_income_statement", FMP_INCOME_STATEMENT, IncomeStatementNormalizer()),
        _port("refresh_balance_sheet", FMP_BALANCE_SHEET, BalanceSheetNormalizer()),
        _port("refresh_key_metrics", FMP_KEY_METRICS, KeyMetricsNormalizer()),
        FamilyPort(
            writer="acquire_cash_flow_statement",  # fresh-path dataset
            target_table=FMP_CASH_FLOW,
            natural_key=("symbol", "date", "period", "filing_date"),
            provider=Provider.FMP,
            incremental=FullWindow(),
            merge_strategy=UpsertPanel(),
            normalizer=CashFlowNormalizer(),
            availability_column="date",
            quality_policy_default="keep_all",
            symbol_mapper=ProviderSymbols.fmp,
        ),
        FamilyPort(
            writer="acquire_earnings_calendar",  # fresh-path dataset
            target_table=FMP_EARNINGS_CALENDAR,
            natural_key=("symbol", "date"),
            provider=Provider.FMP,
            incremental=FullWindow(),
            merge_strategy=UpsertPanel(),
            normalizer=EarningsCalendarNormalizer(),
            availability_column="date",
            quality_policy_default="keep_all",
            symbol_mapper=ProviderSymbols.fmp,
        ),
    ]


def _port(
    writer: str,
    table: Table,
    normalizer: IncomeStatementNormalizer | BalanceSheetNormalizer | KeyMetricsNormalizer,
) -> FamilyPort:
    return FamilyPort(
        writer=writer,
        target_table=table,
        natural_key=("symbol", "date", "period"),
        provider=Provider.FMP,
        incremental=FullWindow(),
        merge_strategy=UpsertPanel(),
        normalizer=normalizer,
        availability_column="date",
        quality_policy_default="keep_all",
        symbol_mapper=ProviderSymbols.fmp,
    )
