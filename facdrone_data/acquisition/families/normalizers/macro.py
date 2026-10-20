# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Macro-family normalizers + the macro FamilyPorts (R2.1, R2.3; D5, D6, D11).

Five FMP "macro" families with no per-symbol price axis (group="macro"):

* economic calendar — global event panel keyed on
  ``(date, country, event)``; the FMP ``date`` field is split into a ``date``
  (``[:10]``) and an HH:MM:SS ``time`` (``[11:]``) string.
* economic indicators — a single named series keyed
  on ``(name, date)``; the 12 indicator names come from ``ECONOMIC_INDICATOR_NAMES``
  and are carried as the port ``scopes`` (the family's scope DATA, not a flag).
  Rows are kept only when the record has a ``date``.
* treasury rates — the global yield curve keyed on
  ``date``, the 12 maturity columns carried verbatim.
* sector performance — global panel keyed on
  ``(date, sector)``; maps ``averageChange`` → ``change_percentage`` and keeps
  only rows with a ``sector``.
* ETF holdings — per-ETF holdings keyed on
  ``(symbol, date, asset)``; coalesces ``asset``→``name``→``"unknown"`` and
  ``sharesNumber``→``shares``. The only symbol-keyed family here, mapped via
  ``to_fmp``.

Every macro table declares ``_updated_at``; the normalizers stamp it from the
injected ``as_of`` clock (time-as-injected-data), never the wall clock. The four
symbol-less families ignore ``symbol`` (it is ``None``); only ETF holdings keys on
it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from facdrone_data.acquisition.families.cursors import MaxDateByName, MaxDateBySymbol, MaxDateGlobal
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
    FMP_ECONOMIC_CALENDAR,
    FMP_ECONOMIC_INDICATORS,
    FMP_ETF_HOLDINGS,
    FMP_SECTOR_PERFORMANCE,
    FMP_TREASURY_RATES,
)
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import Table

    from facdrone_data_types.symbols import CanonicalSymbol

# The 12 FMP economic-indicator series fetched by default. Carried as the indicator
# port's ``scopes``.
ECONOMIC_INDICATOR_NAMES: tuple[str, ...] = (
    "CPI",
    "GDP",
    "realGDP",
    "inflationRate",
    "unemploymentRate",
    "retailSales",
    "industrialProductionTotalIndex",
    "consumerSentiment",
    "federalFunds",
    "totalNonfarmPayroll",
    "initialClaims",
    "durableGoods",
)


def _empty(table: Table) -> pd.DataFrame:
    return pd.DataFrame(columns=[c.name for c in table.columns])


@dataclass(frozen=True)
class EconomicCalendarNormalizer:
    """FMP ``/economic-calendar`` → ``fmp_economic_calendar``.

    Splits the FMP ``date`` field: ``date`` is ``date[:10]`` and ``time`` is
    ``date[11:]`` (the HH:MM:SS tail) when present, else ``""``."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        del symbol  # symbol-less global family
        if raw.empty:
            return _empty(FMP_ECONOMIC_CALENDAR)
        text = raw["date"].astype(str)
        out = pd.DataFrame(
            {
                "date": text.str[:10],
                "time": text.where(text.str.len() > 10, "").str[11:],
                "country": raw.get("country", ""),
                "event": raw.get("event", ""),
                "actual": raw.get("actual"),
                "estimate": raw.get("estimate"),
                "previous": raw.get("previous"),
                "impact": raw.get("impact", ""),
            }
        )
        out["date"] = coerce_date(out["date"])
        stamped = stamp_updated_at(out, FMP_ECONOMIC_CALENDAR, as_of)
        projected = project_to_table(stamped, FMP_ECONOMIC_CALENDAR)
        return dedup_on_primary_key(projected, FMP_ECONOMIC_CALENDAR)


@dataclass(frozen=True)
class EconomicIndicatorNormalizer:
    """FMP ``/economic-indicators`` → ``fmp_economic_indicators``.

    Keyed on the series ``name`` (the scope), NOT a symbol — so ``symbol`` is
    ``None`` and the scope ``name`` is carried on ``raw`` (the refresher fetches one
    scope at a time and the client stamps the ``name`` column, exactly as FRED
    carries ``series_id``). Keeps only records that carry a ``date``."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        del symbol  # named family — keyed on the scope ``name``, not a symbol
        if raw.empty:
            return _empty(FMP_ECONOMIC_INDICATORS)
        kept = raw[raw["date"].notna()]
        out = pd.DataFrame(
            {
                "name": kept["name"],
                "date": kept["date"],
                "value": kept.get("value"),
            }
        )
        out["date"] = coerce_date(out["date"])
        stamped = stamp_updated_at(out, FMP_ECONOMIC_INDICATORS, as_of)
        projected = project_to_table(stamped, FMP_ECONOMIC_INDICATORS)
        return dedup_on_primary_key(projected, FMP_ECONOMIC_INDICATORS)


# The 12 yield-curve maturities
# carried verbatim; each is an identity field on the FMP record.
_TREASURY_FIELDS: tuple[str, ...] = (
    "month1",
    "month2",
    "month3",
    "month6",
    "year1",
    "year2",
    "year3",
    "year5",
    "year7",
    "year10",
    "year20",
    "year30",
)


@dataclass(frozen=True)
class TreasuryRatesNormalizer:
    """FMP ``/treasury-rates`` → ``fmp_treasury_rates``."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        del symbol  # symbol-less global family
        if raw.empty:
            return _empty(FMP_TREASURY_RATES)
        data: dict[str, object] = {"date": raw["date"]}
        for field in _TREASURY_FIELDS:
            data[field] = raw.get(field)
        out = pd.DataFrame(data)
        out["date"] = coerce_date(out["date"])
        stamped = stamp_updated_at(out, FMP_TREASURY_RATES, as_of)
        projected = project_to_table(stamped, FMP_TREASURY_RATES)
        return dedup_on_primary_key(projected, FMP_TREASURY_RATES)


@dataclass(frozen=True)
class SectorPerformanceNormalizer:
    """FMP ``/sector-performance-snapshot`` → ``fmp_sector_performance``.

    Maps ``averageChange`` → ``change_percentage`` and keeps
    only rows that carry a ``sector``."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        del symbol  # symbol-less global family
        if raw.empty:
            return _empty(FMP_SECTOR_PERFORMANCE)
        # Keep only records whose ``sector`` is non-empty.
        sector = raw["sector"].fillna("").astype(str)
        kept = raw[sector.str.len() > 0]
        out = pd.DataFrame(
            {
                "date": kept["date"],
                "sector": kept["sector"],
                "change_percentage": kept.get("averageChange"),
            }
        )
        out["date"] = coerce_date(out["date"])
        stamped = stamp_updated_at(out, FMP_SECTOR_PERFORMANCE, as_of)
        projected = project_to_table(stamped, FMP_SECTOR_PERFORMANCE)
        return dedup_on_primary_key(projected, FMP_SECTOR_PERFORMANCE)


@dataclass(frozen=True)
class EtfHoldingsNormalizer:
    """FMP ``/etf/holdings`` → ``fmp_etf_holdings``.

    The only symbol-keyed macro family. Coalesces ``asset``→``name``→``"unknown"``
    and ``sharesNumber``→``shares``; ``date`` is the snapshot date the row carries."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        if raw.empty or symbol is None:
            return _empty(FMP_ETF_HOLDINGS)
        name = raw.get("name")
        asset = raw.get("asset")
        if asset is None:
            asset = pd.Series("unknown", index=raw.index)
        elif name is not None:
            asset = asset.fillna(name)
        asset = asset.fillna("unknown")
        shares = raw.get("sharesNumber")
        fallback = raw.get("shares")
        if shares is None:
            shares = fallback
        elif fallback is not None:
            # ``sharesNumber or shares``: a missing/0/NaN ``sharesNumber`` falls
            # back (Python truthiness: NaN and 0 are falsy, unlike ``astype``).
            primary = pd.to_numeric(shares, errors="coerce")
            use_primary = primary.notna() & (primary != 0)
            shares = primary.where(use_primary, fallback)
        out = pd.DataFrame(
            {
                "symbol": symbol.canonical_id,
                "date": raw["date"],
                "asset": asset,
                "shares": shares,
                "weight_percentage": raw.get("weightPercentage"),
                "market_value": raw.get("marketValue"),
            }
        )
        out["date"] = coerce_date(out["date"])
        stamped = stamp_updated_at(out, FMP_ETF_HOLDINGS, as_of)
        projected = project_to_table(stamped, FMP_ETF_HOLDINGS)
        return dedup_on_primary_key(projected, FMP_ETF_HOLDINGS)


def macro_families() -> list[FamilyPort]:
    """The five FMP macro ports (R2.1) — calendar, indicators, treasury, sector,
    ETF holdings. All ``UpsertPanel`` (revisable panels), provider FMP."""
    return [
        FamilyPort(
            writer="refresh_economic_calendar",
            target_table=FMP_ECONOMIC_CALENDAR,
            natural_key=("date", "country", "event"),
            provider=Provider.FMP,
            incremental=MaxDateGlobal(skip_days=1),
            merge_strategy=UpsertPanel(),
            normalizer=EconomicCalendarNormalizer(),
            availability_column="date",
            quality_policy_default="keep_all",
        ),
        FamilyPort(
            writer="refresh_economic_indicator",
            target_table=FMP_ECONOMIC_INDICATORS,
            natural_key=("name", "date"),
            provider=Provider.FMP,
            incremental=MaxDateByName(skip_days=30),
            merge_strategy=UpsertPanel(),
            normalizer=EconomicIndicatorNormalizer(),
            availability_column="date",
            quality_policy_default="keep_all",
            scopes=ECONOMIC_INDICATOR_NAMES,
        ),
        FamilyPort(
            writer="refresh_treasury_rates",
            target_table=FMP_TREASURY_RATES,
            natural_key=("date",),
            provider=Provider.FMP,
            incremental=MaxDateGlobal(skip_days=1),
            merge_strategy=UpsertPanel(),
            normalizer=TreasuryRatesNormalizer(),
            availability_column="date",
            quality_policy_default="keep_all",
        ),
        FamilyPort(
            writer="refresh_sector_performance",
            target_table=FMP_SECTOR_PERFORMANCE,
            natural_key=("date", "sector"),
            provider=Provider.FMP,
            incremental=MaxDateGlobal(skip_days=1),
            merge_strategy=UpsertPanel(),
            normalizer=SectorPerformanceNormalizer(),
            availability_column="date",
            quality_policy_default="keep_all",
        ),
        FamilyPort(
            writer="refresh_etf_holdings",
            target_table=FMP_ETF_HOLDINGS,
            natural_key=("symbol", "date", "asset"),
            provider=Provider.FMP,
            incremental=MaxDateBySymbol(skip_days=1),
            merge_strategy=UpsertPanel(),
            normalizer=EtfHoldingsNormalizer(),
            availability_column="date",
            quality_policy_default="keep_all",
            symbol_mapper=ProviderSymbols.fmp,
        ),
    ]
