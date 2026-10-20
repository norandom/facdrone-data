# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""News-family normalizer + the news FamilyPort (R2.1, R2.3, R6.2; D5, D8).

``fmp_news`` is keyed on ``(symbol, published_date, title)`` and written under the
canonical YF symbol (R4.3). Write-time cleaning: map ``publishedDate``/``title``/
(``publisher`` or ``site``), skip any record missing the publication time or the
title, and — the load-bearing quirk — keep the FULL ``published_date`` datetime via
``pd.to_datetime(df["published_date"])``, NOT truncated with ``.dt.date`` (the
table column is ``DateTime`` and the time component is part of the primary key).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from facdrone_data.acquisition.families.cursors import MaxDateBySymbol
from facdrone_data.acquisition.families.normalizers._base import project_to_table, stamp_updated_at
from facdrone_data.acquisition.families.ports import FamilyPort
from facdrone_data.acquisition.families.symbol_mapping import ProviderSymbols
from facdrone_data.acquisition.merge import UpsertPanel
from facdrone_data.acquisition.schema import FMP_NEWS
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from datetime import datetime

    from facdrone_data_types.symbols import CanonicalSymbol


@dataclass(frozen=True)
class NewsNormalizer:
    """FMP ``/news/stock`` records → ``fmp_news``, re-keyed to the canonical symbol.

    Field map: ``published_date``←``publishedDate``,
    ``title``←``title``, ``publisher``←(``publisher`` or ``site``); records missing
    the publication time or the title are skipped. The full datetime is preserved
    (NOT date-truncated) — it is part of the ``fmp_news`` primary key."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        if raw.empty or symbol is None:
            return pd.DataFrame(columns=[c.name for c in FMP_NEWS.columns])
        pub = raw.get("publishedDate", pd.Series([""] * len(raw), index=raw.index))
        title = raw.get("title", pd.Series([""] * len(raw), index=raw.index))
        # ``r.get("publisher") or r.get("site", "")`` semantics: publisher falls back to
        # the source site, an empty string when both are absent.
        publisher = raw.get("publisher", pd.Series([None] * len(raw), index=raw.index))
        site = raw.get("site", pd.Series([""] * len(raw), index=raw.index))
        out = pd.DataFrame(
            {
                "symbol": symbol.canonical_id,  # store the canonical YF symbol (R4.3)
                "published_date": pub,
                "title": title,
                "publisher": publisher.where(publisher.notna() & (publisher != ""), site),
            }
        )
        # ``if not pub or not title: continue`` semantics: drop records missing either.
        keep = out["published_date"].astype(bool) & out["title"].astype(bool)
        out = out[keep].copy()
        # THE quirk: keep the full datetime (``pd.to_datetime(...)``); never ``.dt.date``.
        out["published_date"] = pd.to_datetime(out["published_date"])
        out = out.drop_duplicates(subset=["symbol", "published_date", "title"], keep="last")
        out = out.reset_index(drop=True)
        stamped = stamp_updated_at(out, FMP_NEWS, as_of)
        return project_to_table(stamped, FMP_NEWS)


def news_families() -> list[FamilyPort]:
    """The FMP news port (R2.1): ``refresh_news`` → ``fmp_news``."""
    return [
        FamilyPort(
            writer="refresh_news",
            target_table=FMP_NEWS,
            natural_key=("symbol", "published_date", "title"),
            provider=Provider.FMP,
            incremental=MaxDateBySymbol(date_column="published_date", skip_days=1, overlap_days=1),
            merge_strategy=UpsertPanel(),
            normalizer=NewsNormalizer(),
            availability_column="published_date",
            quality_policy_default="keep_all",
            symbol_mapper=ProviderSymbols.fmp,
        ),
    ]
