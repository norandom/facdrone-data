# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Kedro node adapters for the data pipeline (task 11.2).

Thin wrappers that shape the already-built typed functions (acquisition, the
quality ensemble, the merge, the tick-bar builder) into Kedro nodes — no quant
math here (tech.md: pipelines bind functions to catalog datasets). The injected
boundaries (provider client + registry via ``AcquireDeps``, the DB ``Engine``,
the target ``Table``, the validator, and the cleaning policy) arrive as named
catalog slots a composition root writes before the run; the pure scalars
(universe, dataset, ranges, asset class, column names) arrive via ``params:``.

The graph is acquire → concat → validate-detect → apply-policy → stamp →
project → merge. ``project_to_table_node`` is the explicit, inspectable step
that drops any column not in the target table (e.g. a daily-price panel keeps no
knowledge_date) so the strict merge accepts the frame — projection is a node,
never hidden inside the merge.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd

from facdrone_data.acquisition.acquisition import AcquireDeps, AcquireResult, acquire
from facdrone_data.acquisition.merge import MergeReceipt, merge_panel, stamp_knowledge_date
from facdrone_data.acquisition.schemas import DATASET_SCHEMAS
from facdrone_data.acquisition.validation import QualityPolicy, apply_policy, run_validation
from facdrone_data.quality.ensemble import EnsembleValidator, QualityReport

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy import Engine, Table

    from facdrone_data_types.symbols import CanonicalSymbol


def acquire_node(
    panel: pd.DataFrame,
    acquire_deps: AcquireDeps,
    symbol: CanonicalSymbol,
    dataset: str,
    want: tuple[date, date],
) -> AcquireResult:
    """Resolve → derive coverage → fetch only gaps → schema-validate (R1.2, R2.1).

    One symbol per run: validation and quality detectors are per-series, so the
    universe is iterated at the composition root, not stacked into one frame
    (which would let cross-symbol pct_change artifacts trip the detectors)."""
    return acquire((symbol,), dataset, want, acquire_deps, panel)


def concat_frames_node(result: AcquireResult, dataset: str) -> pd.DataFrame:
    """Concatenate the fetched gap frames; an empty (fully-covered) run yields an
    empty frame carrying the dataset's declared columns so downstream nodes keep
    their contract."""
    if result.frames:
        return pd.concat(result.frames, ignore_index=True)
    return pd.DataFrame(columns=sorted(DATASET_SCHEMAS[dataset].required_columns))


def validate_detect_node(
    frame: pd.DataFrame,
    validator: EnsembleValidator,
    symbol: CanonicalSymbol,
    dataset: str,
) -> QualityReport:
    """Detect-before-consume: run the causal ensemble over the acquired frame.

    The symbol's canonical id and asset class come from the one injected
    CanonicalSymbol (asset-class-aware thresholds), so the report is correctly
    labelled and the detectors run on a single series."""
    return run_validation(
        frame,
        validator,
        symbol=symbol.canonical_id,
        dataset=dataset,
        asset_class=str(symbol.asset_class),
    )


def apply_policy_node(
    frame: pd.DataFrame, report: QualityReport, policy: QualityPolicy
) -> pd.DataFrame:
    """Apply the composition-root-selected cleaning policy (KeepAll / Quarantine)."""
    return apply_policy(frame, report, policy)


def stamp_node(frame: pd.DataFrame, availability_column: str, event_column: str) -> pd.DataFrame:
    """Stamp knowledge_date from the publication/availability column (R4.3)."""
    return stamp_knowledge_date(
        frame, availability_column=availability_column, event_column=event_column or None
    )


def project_to_table_node(frame: pd.DataFrame, table: Table) -> pd.DataFrame:
    """Project the frame onto the target table's columns (in table order) so the
    strict merge accepts it; drops columns the table does not store."""
    keep = [c.name for c in table.columns if c.name in frame.columns]
    return frame.loc[:, keep]


def merge_node(engine: Engine, table: Table, frame: pd.DataFrame) -> MergeReceipt:
    """Idempotent upsert into the canonical panel (first-write-wins, R2.3, R2.4)."""
    return merge_panel(engine, table, frame)
