# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Dukascopy order-flow normalizer + FamilyPort (R2.1; the deferred tick path, now
ported for future order-flow factor research).

The transport (``shells/providers/dukascopy_source``) already aggregates ticks to
the daily order-flow row via the pure ``library.order_flow`` kernel, so this
normalizer is thin: re-key to the canonical symbol, normalize the date, and project
onto ``duka_order_flow``. No order-flow *factor* is resurrected here — only the data
acquisition, so the panel is available when a factor wants it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from facdrone_data.acquisition.families.cursors import MinMaxRangeGapFill
from facdrone_data.acquisition.families.normalizers._base import (
    coerce_date,
    dedup_on_primary_key,
    project_to_table,
)
from facdrone_data.acquisition.families.ports import FamilyPort
from facdrone_data.acquisition.families.symbol_mapping import ProviderSymbols
from facdrone_data.acquisition.merge import UpsertPanel
from facdrone_data.acquisition.schema import DUKA_ORDER_FLOW
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from datetime import datetime

    from facdrone_data_types.symbols import CanonicalSymbol

_COLUMNS = [c.name for c in DUKA_ORDER_FLOW.columns]


@dataclass(frozen=True)
class DukascopyNormalizer:
    """Aggregated daily order-flow rows → ``duka_order_flow``, re-keyed to canonical."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        del as_of
        if raw.empty or symbol is None:
            return pd.DataFrame(columns=_COLUMNS)
        out = raw.copy()
        out["symbol"] = symbol.canonical_id  # store the canonical symbol, not the Duka name
        out["date"] = coerce_date(out["date"])
        projected = project_to_table(out, DUKA_ORDER_FLOW)
        return dedup_on_primary_key(projected, DUKA_ORDER_FLOW)


def dukascopy_families() -> list[FamilyPort]:
    """The Dukascopy order-flow port."""
    return [
        FamilyPort(
            writer="refresh_tick_data",
            target_table=DUKA_ORDER_FLOW,
            natural_key=("symbol", "date"),
            provider=Provider.DUKASCOPY,
            incremental=MinMaxRangeGapFill(date_column="date", scope_column="symbol"),
            merge_strategy=UpsertPanel(),
            normalizer=DukascopyNormalizer(),
            availability_column="date",
            quality_policy_default="keep_all",
            symbol_mapper=ProviderSymbols.duka,
        )
    ]
