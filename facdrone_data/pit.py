# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Point-in-time evidence views (task 5.3; R3.5, R3.6).

The view is the only sanctioned read path for backtests and
evaluation: rows are visible only when their KNOWLEDGE date is at or
before the as-of moment (R3.6), and — when the partition carries a
``matures_on`` column — only when their forward-return window has
matured (R3.5, the ``forward_date <= as_of`` gate).

``matures_on`` is the trading-day the forward window closes, computed
by the evaluation pipeline from each symbol's own price calendar
(``groupby(symbol).date.shift(-forward_days)``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from datetime import datetime

MATURITY_COLUMN = "matures_on"


def point_in_time_view(evidence: pd.DataFrame, as_of: datetime) -> pd.DataFrame:
    """Filter an evidence partition to what was knowable and matured at
    ``as_of``. Returns a new frame; never mutates the input."""
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")

    knowledge = pd.to_datetime(evidence["knowledge_date"], utc=True)
    visible = evidence[knowledge <= as_of]

    if MATURITY_COLUMN in visible.columns:
        matures = pd.to_datetime(visible[MATURITY_COLUMN])
        visible = visible[matures <= pd.Timestamp(as_of.date())]

    return visible.reset_index(drop=True)
