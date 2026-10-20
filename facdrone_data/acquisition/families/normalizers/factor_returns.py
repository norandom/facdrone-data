# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Style-factor return normalizer + the factor_returns FamilyPorts (factor-returns-api).

``factor_returns`` is a *named/factor-keyed* long family (mirrors ``fred_series``):
the scope is a ``factor_id`` (``MKT``/``SMB``/``HML``/``MOM``/``QMJ``/``BAB``),
supplied via the request's ``scope`` and carried on ``raw`` in a ``factor_id``
column. ``symbol`` is therefore ``None`` (no canonical-YF axis).

Two ports populate the one table, split by provider:

* ``refresh_factor_returns_kenfrench`` — ``Provider.KEN_FRENCH``: MKT/SMB/HML
  re-projected from the Dartmouth daily 3-factor CSV (so all six factors read
  through one interface) plus MOM from the Ken French daily momentum CSV.
* ``refresh_factor_returns_aqr`` — ``Provider.AQR``: QMJ and BAB from the AQR
  Data Library daily factor files.

Unit conversion (percent→decimal for Ken French, the AQR file's own units) lives
in each *client*, which knows its source's convention; this normalizer is the
generic quirk-free path — coerce date, numeric ``ret``, drop NaN, dedup on
``(factor_id, date)``, stamp ``_updated_at`` from the injected clock. These
writers are listed as native entries in
``tests/unit/test_data_acquisition_inventory.py``.
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
from facdrone_data.acquisition.merge import UpsertPanel
from facdrone_data.acquisition.schema import FACTOR_RETURNS
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from datetime import datetime

    from facdrone_data_types.symbols import CanonicalSymbol

# The factor ids each provider serves — the family's scope *data* (FamilyPort.scopes),
# not a behaviour flag. MKT/SMB/HML are re-projected from FF3 so all six read through
# factor_returns; MOM is the Ken French momentum series; QMJ/BAB are AQR. The ``_EU``
# ids are the same four factors from Ken French's European portfolios, kept as
# separate series because they describe a different market, not a different vintage
# of the US one.
_KENFRENCH_FACTORS: tuple[str, ...] = (
    "MKT",
    "SMB",
    "HML",
    "MOM",
    "MKT_EU",
    "SMB_EU",
    "HML_EU",
    "MOM_EU",
)
_AQR_FACTORS: tuple[str, ...] = ("QMJ", "BAB")


@dataclass(frozen=True)
class FactorReturnsNormalizer:
    """Raw ``(factor_id, date, ret)`` long frame → ``factor_returns``.

    ``raw`` carries ``factor_id`` (the scope), ``date`` and a decimal daily simple
    ``ret`` (the client already converted units). The generic path mirrors
    ``FredSeriesNormalizer``: drop NaN ``ret`` BEFORE persistence (never write 0 for
    a missing day), normalize ``date`` to a plain ``date``, dedup on the natural key
    (R6.2). ``symbol`` is ``None`` — the family is factor-keyed, not symbol-keyed."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        del symbol  # factor-keyed (scope=factor_id), never symbol-keyed
        if raw.empty:
            return pd.DataFrame(columns=[c.name for c in FACTOR_RETURNS.columns])
        out = pd.DataFrame(index=raw.index)
        out["factor_id"] = raw["factor_id"]
        out["date"] = coerce_date(raw["date"])
        out["ret"] = pd.to_numeric(raw["ret"], errors="coerce")
        out = out.dropna(subset=["ret"])  # omit missing days (spec §8.7), never 0
        out = out.drop_duplicates(subset=["factor_id", "date"], keep="last")
        out = out.reset_index(drop=True)
        stamped = stamp_updated_at(out, FACTOR_RETURNS, as_of)
        return project_to_table(stamped, FACTOR_RETURNS)


def factor_returns_families() -> list[FamilyPort]:
    """The two factor_returns ports (one per provider), both factor-keyed full-history
    upserts on ``(factor_id, date)``. ``scopes`` lists each provider's factor ids — the
    family iterates these, NOT the symbol universe."""
    normalizer = FactorReturnsNormalizer()
    return [
        FamilyPort(
            writer="refresh_factor_returns_kenfrench",
            target_table=FACTOR_RETURNS,
            natural_key=("factor_id", "date"),
            provider=Provider.KEN_FRENCH,
            incremental=FullWindow(),
            merge_strategy=UpsertPanel(),
            normalizer=normalizer,
            availability_column="date",
            quality_policy_default="keep_all",
            scopes=_KENFRENCH_FACTORS,
        ),
        FamilyPort(
            writer="refresh_factor_returns_aqr",
            target_table=FACTOR_RETURNS,
            natural_key=("factor_id", "date"),
            provider=Provider.AQR,
            incremental=FullWindow(),
            merge_strategy=UpsertPanel(),
            normalizer=normalizer,
            availability_column="date",
            quality_policy_default="keep_all",
            scopes=_AQR_FACTORS,
        ),
    ]
