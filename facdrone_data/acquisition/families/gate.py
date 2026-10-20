# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The write-time quality gate (R6.1, R6.2, R6.4, R6.5).

Every acquired frame passes through this gate BEFORE persistence, never at read
time (R6.1). The gate is the refresher's injected ``QualityGate`` seam, bound at
the data composition root:

1. **validate-before-process** — for families that opt into quarantine
   (``quality_policy_default == "quarantine"``), run the causal ensemble and apply
   the composition-root ``QualityPolicy`` so contaminated rows are dropped before
   merge (R6.4). Keep-all families skip the price-specific ensemble; their
   write-time cleaning already happened in the per-family normalizer (R6.2, R6.5).
2. **stamp + causal guard** — stamp a transient ``knowledge_date`` from the family's
   availability column; fail loud if it precedes the event date (lookahead guard).
3. **project** — return exactly the target table's columns (the transient
   ``knowledge_date`` is dropped; no target table stores it).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from facdrone_data.acquisition.families.normalizers._base import project_to_table
from facdrone_data.acquisition.merge import stamp_knowledge_date
from facdrone_data.acquisition.validation import apply_policy, run_validation

if TYPE_CHECKING:
    from facdrone_data.acquisition.families.ports import FamilyPort
    from facdrone_data.acquisition.families.refresher import DownloadInputs
    from facdrone_data_types.symbols import CanonicalSymbol


def quality_gate(
    frame: pd.DataFrame,
    port: FamilyPort,
    inputs: DownloadInputs,
    symbol: CanonicalSymbol | None,
) -> pd.DataFrame:
    """Validate → quarantine → stamp → project (the refresher's bound gate)."""
    cleaned = frame
    if port.quality_policy_default == "quarantine" and not cleaned.empty:
        scope = symbol.canonical_id if symbol is not None else _frame_symbol(cleaned)
        asset_class = symbol.asset_class.value if symbol is not None else "unknown"
        report = run_validation(
            cleaned,
            inputs.validator,
            symbol=scope,
            dataset=port.target_table.name,
            asset_class=asset_class,
        )
        cleaned = apply_policy(cleaned, report, inputs.policy)
    cleaned = _stamp_pit(cleaned, port)
    return project_to_table(cleaned, port.target_table)


def _frame_symbol(frame: pd.DataFrame) -> str:
    if "symbol" in frame.columns and len(frame):
        return str(frame["symbol"].iloc[0])
    return "global"


def _stamp_pit(frame: pd.DataFrame, port: FamilyPort) -> pd.DataFrame:
    """Stamp a transient knowledge_date for the lookahead guard; a no-op when the
    availability column is absent (e.g. the frame was fully quarantined)."""
    if frame.empty or port.availability_column not in frame.columns:
        return frame
    event_column = "event_date" if "event_date" in frame.columns else None
    return stamp_knowledge_date(
        frame,
        availability_column=port.availability_column,
        event_column=event_column,
    )
