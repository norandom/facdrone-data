# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""FMP fundamentals transport — income / balance / key-metrics / estimates, plus
the fresh-path cash-flow and earnings-calendar datasets (R2.2; equity-factors 1.1).

The live fetch layer for the FMP fundamentals datasets whose normalizers
live in ``pipelines/data/families/normalizers/`` (``fundamentals.py`` +
``analyst.py``). Each ``fetch`` returns the RAW FMP records as a DataFrame whose
columns are exactly what the matching ``.normalize(raw, …)`` reads — never a
projected/renamed frame — so the normalizer carries every provider quirk itself.

These are full-history, symbol-keyed fetches (income statement, balance sheet,
key metrics, analyst estimates). The ``limit`` param is optional; we omit it and let
FMP return full history.
``req.provider_symbol`` is already the FMP fundamentals symbol (the refresher maps
it via ``to_fmp`` before calling).

KEY-METRICS BVPS JOIN (D4): the client fetches BOTH ``/key-metrics`` AND ``/ratios`` with
the same ``(symbol, period)`` and LEFT-joins ``bookValuePerShare`` from ``/ratios``
into the key-metrics row on ``(symbol, date, period)``. The
:class:`~facdrone_data.acquisition.families.normalizers.fundamentals.KeyMetricsNormalizer`
reads that joined value from the ``ratios_bookValuePerShare`` column and falls back
to the legacy ``/key-metrics`` ``bookValuePerShare`` per row. So ``fetch`` performs
the join here and emits the combined raw: the ``/key-metrics`` fields PLUS a
``ratios_bookValuePerShare`` column. On a ``/ratios`` outage that column is emitted
absent (the join contributes nothing) so the normalizer falls back to the legacy
value for every row — never NULL.

The HTTP call is the injected ``HttpJson`` transport from ``fmp_endpoints`` so the
adapter is pure and offline-testable; the live transport (``live_http_json``) is
wired at the data composition root, importing ``requests`` lazily.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pandas as pd

from facdrone_data.providers.fmp_endpoints import HttpJson, fmp_get, live_http_json
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from facdrone_data_types.ports import FetchRequest

# The normalizer column the KeyMetricsNormalizer reads for the joined /ratios
# book value per share (D4); see KeyMetricsNormalizer.ratios_bvps_column.
_RATIOS_BVPS_COLUMN = "ratios_bookValuePerShare"

_INCOME_STATEMENT = "fmp_income_statement"
_BALANCE_SHEET = "fmp_balance_sheet"
_KEY_METRICS = "fmp_key_metrics"
_ANALYST_ESTIMATES = "fmp_analyst_estimates"
_CASH_FLOW = "fmp_cash_flow"
_EARNINGS_CALENDAR = "fmp_earnings_calendar"

# The normalizer column carrying the /income-statement weightedAverageShsOut joined
# into the cash-flow raw on (date, period) — the D4 bvps-join pattern. Absent on an
# /income-statement outage so shares normalize to missing, never zero.
_INCOME_SHARES_COLUMN = "income_weightedAverageShsOut"


@dataclass(frozen=True)
class FmpFundamentalsClient:
    """``MarketDataClient`` for the six FMP fundamentals datasets.

    Returns the raw FMP records as a DataFrame; the per-family normalizer shapes
    them. A dataset this adapter does not serve returns an empty frame so the
    refresher fails open and skips it."""

    transport: HttpJson
    api_key: str

    @property
    def provider(self) -> Provider:
        return Provider.FMP

    def fetch(self, req: FetchRequest) -> pd.DataFrame:
        if req.dataset == _INCOME_STATEMENT:
            records = self._get("/income-statement", req.provider_symbol)
            return pd.DataFrame(records)
        if req.dataset == _BALANCE_SHEET:
            records = self._get("/balance-sheet-statement", req.provider_symbol)
            return pd.DataFrame(records)
        if req.dataset == _KEY_METRICS:
            return self._fetch_key_metrics(req.provider_symbol)
        if req.dataset == _ANALYST_ESTIMATES:
            records = self._get(
                "/analyst-estimates", req.provider_symbol, params={"period": "annual"}
            )
            return pd.DataFrame(records)
        if req.dataset == _CASH_FLOW:
            return self._fetch_cash_flow(req.provider_symbol)
        if req.dataset == _EARNINGS_CALENDAR:
            records = self._get("/earnings", req.provider_symbol)
            return pd.DataFrame(records)
        return pd.DataFrame()  # not a served dataset → fail-open skip

    def datasets(self) -> tuple[str, ...]:
        return (
            _INCOME_STATEMENT,
            _BALANCE_SHEET,
            _KEY_METRICS,
            _ANALYST_ESTIMATES,
            _CASH_FLOW,
            _EARNINGS_CALENDAR,
        )

    def _fetch_cash_flow(self, symbol: str) -> pd.DataFrame:
        """``/cash-flow-statement`` LEFT-joined with ``/income-statement``
        ``weightedAverageShsOut`` on ``(date, period)`` (the D4 bvps-join
        pattern). The owner-yield leg needs a daily market-cap series, which is
        close × these FY-constant shares; neither the cash-flow payload nor any
        live table carries shares, and altering the populated income table is a
        migration this fresh path deliberately avoids. On an income outage the
        column is left ABSENT so shares normalize to missing, never zero."""
        records = self._get("/cash-flow-statement", symbol)
        if not records:
            return pd.DataFrame()
        cash_flow = pd.DataFrame(records)

        income_records = self._get("/income-statement", symbol)
        if not income_records:
            return cash_flow  # income outage → shares column absent (missing ≠ 0)
        income = pd.DataFrame(income_records)
        if "weightedAverageShsOut" not in income.columns or "date" not in income.columns:
            return cash_flow

        join = income[["date", "weightedAverageShsOut"]].copy()
        join["period"] = _period_key(income)
        join = join.rename(columns={"weightedAverageShsOut": _INCOME_SHARES_COLUMN})
        join = join.drop_duplicates(subset=["date", "period"], keep="last")

        cash_flow = cash_flow.copy()
        cash_flow["__period_key"] = _period_key(cash_flow)
        merged = cash_flow.merge(
            join,
            how="left",
            left_on=["date", "__period_key"],
            right_on=["date", "period"],
            suffixes=("", "__income"),
        )
        merged = merged.drop(columns=["__period_key"])
        if "period__income" in merged.columns:
            merged = merged.drop(columns=["period__income"])
        return merged

    def _get(
        self, endpoint: str, symbol: str, *, params: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        query = {"symbol": symbol, **(params or {})}
        return fmp_get(self.transport, endpoint, query, api_key=self.api_key)

    def _fetch_key_metrics(self, symbol: str) -> pd.DataFrame:
        """``/key-metrics`` LEFT-joined with ``/ratios`` ``bookValuePerShare`` on
        ``(date, period)`` (the symbol is fixed for the call) (D4).

        Emits the ``/key-metrics`` fields PLUS a ``ratios_bookValuePerShare``
        column carrying the joined ``/ratios`` value. On a ``/ratios`` outage
        (empty/failed) the column is left ABSENT so the normalizer falls back to
        the legacy ``/key-metrics`` value for every row — never NULL."""
        records = self._get("/key-metrics", symbol)
        if not records:
            return pd.DataFrame()
        metrics = pd.DataFrame(records)

        ratios_records = self._get("/ratios", symbol)
        if not ratios_records:
            return metrics  # /ratios outage → ratios column absent (legacy fallback)
        ratios = pd.DataFrame(ratios_records)
        if "bookValuePerShare" not in ratios.columns or "date" not in ratios.columns:
            return metrics  # nothing joinable → legacy fallback

        # The /ratios bvps lookup is keyed by (date, period) with period defaulting
        # to "annual" when absent; each row then reads its (date, period) value.
        join = ratios[["date", "bookValuePerShare"]].copy()
        join["period"] = _period_key(ratios)
        join = join.rename(columns={"bookValuePerShare": _RATIOS_BVPS_COLUMN})
        # Last record per (date, period) wins (a dict-overwrite semantics).
        join = join.drop_duplicates(subset=["date", "period"], keep="last")

        metrics = metrics.copy()
        metrics["__period_key"] = _period_key(metrics)
        merged = metrics.merge(
            join,
            how="left",
            left_on=["date", "__period_key"],
            right_on=["date", "period"],
            suffixes=("", "__ratios"),
        )
        merged = merged.drop(columns=["__period_key"])
        # The merge added a duplicate `period` from /ratios; keep the /key-metrics one.
        if "period__ratios" in merged.columns:
            merged = merged.drop(columns=["period__ratios"])
        return merged


def _period_key(frame: pd.DataFrame) -> pd.Series:
    """The (date,)``period`` join key with a ``r.get("period", "annual")``
    default — the literal ``"annual"`` only where the column is absent or NaN."""
    if "period" not in frame.columns:
        return pd.Series(["annual"] * len(frame), index=frame.index, dtype=object)
    return frame["period"].where(frame["period"].notna(), "annual")


def live_fmp_fundamentals(api_key: str) -> FmpFundamentalsClient:
    """The live client over the FMP transport (``requests`` loads inside ``live_http_json``)."""
    return FmpFundamentalsClient(transport=live_http_json(), api_key=api_key)
