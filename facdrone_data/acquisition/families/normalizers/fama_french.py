# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Kenneth French daily Fama-French 3-factor normalizer + FamilyPort (D5, D8, D11).

A single global series (no symbol axis): the daily F-F CSV ships factor values in
*percent* units with ``-99.99`` / ``-999`` missing-data sentinels. This
normalizer converts percent → decimal (``/100``) and the two sentinels → NULL,
**checking the sentinel before the divide**. ``raw`` is the already-parsed daily
block (parsing stops at the annual section before this stage), so the columns are the
Ken French headers ``Date``/``Mkt-RF``/``SMB``/``HML``/``RF`` (strings or numbers).
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
from facdrone_data.acquisition.merge import UpsertPanel
from facdrone_data.acquisition.schema import FAMA_FRENCH_FACTORS
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from datetime import datetime

    from facdrone_data_types.symbols import CanonicalSymbol

# Missing-data markers checked
# BEFORE the percent→decimal divide.
_FAMA_FRENCH_SENTINELS = (-99.99, -999.0)

# Ken French header → table column. ``Date`` is parsed separately (%Y%m%d).
_FACTOR_COLUMNS = {"Mkt-RF": "mkt_rf", "SMB": "smb", "HML": "hml", "RF": "rf"}


def _percent_to_decimal(series: pd.Series) -> pd.Series:
    """``-99.99``/``-999`` → NULL (before the divide), else ``v/100``."""
    values = pd.to_numeric(series, errors="coerce")
    sentinel = values.isin(_FAMA_FRENCH_SENTINELS)
    return (values / 100.0).where(~sentinel, other=None)


@dataclass(frozen=True)
class FamaFrenchNormalizer:
    """Parsed Ken French daily 3-factor block → ``fama_french_factors``.

    Percent→decimal (``/100``) and sentinel→NULL (``-99.99``/``-999``, checked
    first); ``Date`` is coerced from the
    ``%Y%m%d`` token. No symbol axis (single global series)."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        del symbol  # keyless global series — no symbol mapping
        if raw.empty:
            return pd.DataFrame(columns=[c.name for c in FAMA_FRENCH_FACTORS.columns])
        out = pd.DataFrame(index=raw.index)
        out["date"] = coerce_date(raw["Date"])
        for source, target in _FACTOR_COLUMNS.items():
            out[target] = _percent_to_decimal(raw[source])
        stamped = stamp_updated_at(out, FAMA_FRENCH_FACTORS, as_of)
        projected = project_to_table(stamped, FAMA_FRENCH_FACTORS)
        return dedup_on_primary_key(projected, FAMA_FRENCH_FACTORS)


def fama_french_families() -> list[FamilyPort]:
    """The keyless Ken French daily 3-factor port (group ``fama_french``)."""
    return [
        FamilyPort(
            writer="refresh_fama_french",
            target_table=FAMA_FRENCH_FACTORS,
            natural_key=("date",),
            provider=Provider.KEN_FRENCH,
            incremental=FullWindow(),
            merge_strategy=UpsertPanel(),
            normalizer=FamaFrenchNormalizer(),
            availability_column="date",
            quality_policy_default="keep_all",
        ),
    ]
