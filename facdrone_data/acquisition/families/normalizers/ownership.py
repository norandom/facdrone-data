# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Ownership-family normalizers + the ownership FamilyPorts (R2.1, R2.3, R6.x).

Three FMP "ownership" writers:

* ``refresh_insider_trading`` → ``fmp_insider_trading`` — SEC Form 4 filings, one
  row per (symbol, filing_date, reporting_name, transaction_type). Keys on the
  YF input ``symbol`` (no price re-map), truncates ``filingDate``/``transactionDate``
  to ``[:10]``, coerces ``securitiesTransacted``/``price`` with ``float(... or 0)``,
  and defaults the string fields to ``""``.
* ``refresh_institutional_ownership`` → ``fmp_institutional_ownership`` — 13F
  holder-level records **aggregated** per (symbol, year, quarter): holder/new/
  sold-out/increased/decreased counts, summed shares and net change, and the
  ``net_flow_pct`` ratio (``round(.., 4)``, 0.0 when no shares). The per-year/
  per-quarter fetch *iteration* is an adapter concern; the aggregation of one
  recorded holder payload into the quarterly row is the normalization carried here.
* ``refresh_positions_summary`` → ``fmp_positions_summary`` — the pre-aggregated
  symbol-positions-summary endpoint, one row per (symbol, year, quarter), every
  count ``int(... or 0)`` and every ratio ``float(... or 0)``.

All three stamp ``_updated_at`` from the injected ``as_of`` (time-as-injected-data,
never the wall clock). All three are ``UpsertPanel``
(revisable: a later 13F amendment or a re-filed Form 4 revises the stored row).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import pandas as pd

from facdrone_data.acquisition.families.cursors import FullWindow, MaxDateBySymbol
from facdrone_data.acquisition.families.normalizers._base import (
    coerce_date,
    project_to_table,
    stamp_updated_at,
)
from facdrone_data.acquisition.families.ports import FamilyPort
from facdrone_data.acquisition.families.symbol_mapping import ProviderSymbols
from facdrone_data.acquisition.merge import UpsertPanel
from facdrone_data.acquisition.schema import (
    FMP_INSIDER_TRADING,
    FMP_INSTITUTIONAL_OWNERSHIP,
    FMP_POSITIONS_SUMMARY,
)
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from datetime import datetime

    from facdrone_data_types.symbols import CanonicalSymbol


def _trunc10(series: pd.Series) -> pd.Series:
    """``r.get("...Date", "")[:10]`` semantics: keep the leading ``YYYY-MM-DD``."""
    return series.fillna("").astype(str).str.slice(0, 10)


def _float_or_zero(series: pd.Series) -> pd.Series:
    """``float(r.get(key, 0) or 0)`` semantics: missing/NaN/falsy becomes ``0.0``."""
    return pd.to_numeric(series, errors="coerce").fillna(0.0).astype("float64")


def _int_or_zero(series: pd.Series) -> pd.Series:
    """``int(r.get(key, 0) or 0)`` semantics: missing/NaN/falsy becomes ``0``."""
    return pd.to_numeric(series, errors="coerce").fillna(0).astype("int64")


@dataclass(frozen=True)
class InsiderTradingNormalizer:
    """FMP ``/insider-trading/search`` rows → ``fmp_insider_trading``
    (``refresh_insider_trading``). Keyed on the canonical symbol; ``filing_date`` is
    truncated then date-coerced; ``transaction_date`` is ``[:10] or None``."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        if raw.empty or symbol is None:
            return pd.DataFrame(columns=[c.name for c in FMP_INSIDER_TRADING.columns])
        out = pd.DataFrame(index=raw.index)
        out["symbol"] = symbol.canonical_id
        # Rows with an empty filing date are skipped (filtered below).
        filing = _trunc10(raw["filing_date"])
        out["filing_date"] = filing
        # ``(r.get("transactionDate") or "")[:10] or None`` — empty stays NULL;
        # the column is ``Date``, so the kept value is date-coerced (NaT → None).
        trans = _trunc10(raw["transaction_date"])
        trans_date = pd.to_datetime(trans.where(trans != ""), errors="coerce").dt.date
        out["transaction_date"] = trans_date.where(trans_date.notna(), other=None)
        out["transaction_type"] = raw["transaction_type"].fillna("").astype(str)
        out["securities_transacted"] = _float_or_zero(raw["securities_transacted"])
        out["price"] = _float_or_zero(raw["price"])
        out["reporting_name"] = raw["reporting_name"].fillna("").astype(str)
        out["type_of_owner"] = raw["type_of_owner"].fillna("").astype(str)
        out["acquisition_or_disposition"] = raw["acquisition_or_disposition"].fillna("").astype(str)
        out = out[out["filing_date"] != ""].copy()
        out["filing_date"] = coerce_date(out["filing_date"])
        # Dedup on the natural key (keep last write), as an upsert would.
        out = out.drop_duplicates(
            subset=["symbol", "filing_date", "reporting_name", "transaction_type"], keep="last"
        ).reset_index(drop=True)
        out = stamp_updated_at(out, FMP_INSIDER_TRADING, as_of)
        return project_to_table(out, FMP_INSIDER_TRADING)


@dataclass(frozen=True)
class InstitutionalOwnershipNormalizer:
    """FMP 13F holder records → ``fmp_institutional_ownership``, aggregated per
    (symbol, year, quarter) (``refresh_institutional_ownership``).

    ``raw`` carries one or more (year, quarter) blocks of holder rows (the per-period
    fetch iteration is the adapter's job); each block aggregates to one stored row.
    The ``year``/``quarter`` of each block travel as columns on the payload."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        if raw.empty or symbol is None:
            return pd.DataFrame(columns=[c.name for c in FMP_INSTITUTIONAL_OWNERSHIP.columns])
        work = raw.copy()
        change_pct = pd.to_numeric(work["change_in_shares_number_percentage"], errors="coerce")
        work["_change_pct"] = change_pct.fillna(0.0)
        work["_is_new"] = work["is_new"].fillna(False).astype(bool)
        work["_is_sold_out"] = work["is_sold_out"].fillna(False).astype(bool)
        work["_shares"] = _float_or_zero(work["shares_number"])
        work["_change"] = _float_or_zero(work["change_in_shares_number"])
        work["_filing"] = _trunc10(work["filing_date"])

        rows: list[dict[str, object]] = []
        for key, block in work.groupby(["year", "quarter"], sort=True):
            year_val, quarter_val = cast("tuple[object, object]", key)
            year = int(str(year_val))
            quarter = int(str(quarter_val))
            total_shares = float(block["_shares"].sum())
            net_change = float(block["_change"].sum())
            net_flow_pct = (net_change / total_shares * 100) if total_shares > 0 else 0.0
            filings = [f for f in block["_filing"].tolist() if f]
            filing_date = max(filings) if filings else f"{year}-{quarter * 3:02d}-30"
            rows.append(
                {
                    "symbol": symbol.canonical_id,
                    "year": year,
                    "quarter": quarter,
                    "filing_date": filing_date,
                    "n_holders": int(len(block)),
                    "n_new": int(block["_is_new"].sum()),
                    "n_sold_out": int(block["_is_sold_out"].sum()),
                    "n_increased": int((block["_change_pct"] > 0).sum()),
                    "n_decreased": int((block["_change_pct"] < 0).sum()),
                    "total_shares": int(total_shares),
                    "net_shares_change": int(net_change),
                    "net_flow_pct": round(net_flow_pct, 4),
                }
            )

        out = pd.DataFrame(rows)
        out["filing_date"] = coerce_date(out["filing_date"])
        out = out.drop_duplicates(subset=["symbol", "year", "quarter"], keep="last").reset_index(
            drop=True
        )
        out = stamp_updated_at(out, FMP_INSTITUTIONAL_OWNERSHIP, as_of)
        return project_to_table(out, FMP_INSTITUTIONAL_OWNERSHIP)


@dataclass(frozen=True)
class PositionsSummaryNormalizer:
    """FMP symbol-positions-summary rows → ``fmp_positions_summary``
    (``refresh_positions_summary``), one row per (symbol, year, quarter); counts
    ``int(... or 0)``, ratios ``float(... or 0)``."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        if raw.empty or symbol is None:
            return pd.DataFrame(columns=[c.name for c in FMP_POSITIONS_SUMMARY.columns])
        out = pd.DataFrame(index=raw.index)
        out["symbol"] = symbol.canonical_id
        out["year"] = _int_or_zero(raw["year"])
        out["quarter"] = _int_or_zero(raw["quarter"])
        out["investors_holding"] = _int_or_zero(raw["investors_holding"])
        out["investors_holding_change"] = _int_or_zero(raw["investors_holding_change"])
        out["new_positions"] = _int_or_zero(raw["new_positions"])
        out["closed_positions"] = _int_or_zero(raw["closed_positions"])
        out["increased_positions"] = _int_or_zero(raw["increased_positions"])
        out["reduced_positions"] = _int_or_zero(raw["reduced_positions"])
        out["put_call_ratio"] = _float_or_zero(raw["put_call_ratio"])
        out["put_call_ratio_change"] = _float_or_zero(raw["put_call_ratio_change"])
        out["total_invested_change"] = _float_or_zero(raw["total_invested_change"])
        out["ownership_pct_change"] = _float_or_zero(raw["ownership_pct_change"])
        out = out.drop_duplicates(subset=["symbol", "year", "quarter"], keep="last").reset_index(
            drop=True
        )
        out = stamp_updated_at(out, FMP_POSITIONS_SUMMARY, as_of)
        return project_to_table(out, FMP_POSITIONS_SUMMARY)


def ownership_families() -> list[FamilyPort]:
    """The three FMP ownership ports (R2.1): insider trading, institutional
    ownership, positions summary. All ``UpsertPanel`` + ``to_fmp`` + ``keep_all``."""
    return [
        FamilyPort(
            writer="refresh_insider_trading",
            target_table=FMP_INSIDER_TRADING,
            natural_key=("symbol", "filing_date", "reporting_name", "transaction_type"),
            provider=Provider.FMP,
            incremental=MaxDateBySymbol(date_column="filing_date"),
            merge_strategy=UpsertPanel(),
            normalizer=InsiderTradingNormalizer(),
            availability_column="filing_date",
            quality_policy_default="keep_all",
            symbol_mapper=ProviderSymbols.fmp,
        ),
        FamilyPort(
            writer="refresh_institutional_ownership",
            target_table=FMP_INSTITUTIONAL_OWNERSHIP,
            natural_key=("symbol", "year", "quarter"),
            provider=Provider.FMP,
            incremental=FullWindow(),
            merge_strategy=UpsertPanel(),
            normalizer=InstitutionalOwnershipNormalizer(),
            availability_column="filing_date",
            quality_policy_default="keep_all",
            symbol_mapper=ProviderSymbols.fmp,
        ),
        FamilyPort(
            writer="refresh_positions_summary",
            target_table=FMP_POSITIONS_SUMMARY,
            natural_key=("symbol", "year", "quarter"),
            provider=Provider.FMP,
            incremental=FullWindow(),
            merge_strategy=UpsertPanel(),
            normalizer=PositionsSummaryNormalizer(),
            availability_column="year",
            quality_policy_default="keep_all",
            symbol_mapper=ProviderSymbols.fmp,
        ),
    ]
