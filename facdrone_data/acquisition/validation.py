# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Validation gate: detect-before-consume + typed cleaning policy (task 6.3;
R12.3, R12.4, R12.6).

The pure causal ensemble (``library.quality``) produces a :class:`QualityReport`
BEFORE any data is built into bars/features (detect-before-consume, R12.3). What
happens to flagged rows is then decided by a typed :class:`QualityPolicy`
component chosen at a composition root — never a behavioral mode flag (R12.4):

* :class:`KeepAll` — the default: preserve the raw panel and the report.
* :class:`QuarantineContaminated` — drop the flagged rows (by their authoritative
  0-based position from the ensemble) and optionally reaggregate the survivors
  (e.g. rebuild tick bars from the clean ticks) to preserve valid bars.
* :class:`QuarantineUnmeasurable` — the door for vendor corruption of the Air
  France kind (2004-07-22 .. 2005-07-15: the whole OHLC sign-flipped by naive
  back-adjustment through the KLM exchange offer, served that way to this day):
  drop only the rows a structural detector declares unmeasurable; a row the vote
  merely finds unusual is kept, because an opinion is the report's business.

Removal only ever drops rows, so it introduces no lookahead. ``report_to_frame``
serializes the report to a loadable catalog frame (the catalog declaration is
task 11). pipelines tier: orchestration over the pure library, no quant math.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import pandas as pd

from facdrone_data.quality.ensemble import UNMEASURABLE, EnsembleValidator, QualityReport


@runtime_checkable
class QualityPolicy(Protocol):
    """A typed cleaning policy (R12.4): a variant is a class, not a flag."""

    def apply(self, frame: pd.DataFrame, report: QualityReport) -> pd.DataFrame: ...


@dataclass(frozen=True)
class KeepAll:
    """Default policy: keep every row; the report is the audit trail (R12.4)."""

    def apply(self, frame: pd.DataFrame, report: QualityReport) -> pd.DataFrame:
        del report  # KeepAll consults nothing; the report is persisted separately
        return frame


@dataclass(frozen=True)
class QuarantineContaminated:
    """Opt-in policy: drop flagged rows, then optionally reaggregate survivors.

    ``reaggregate`` is an injected typed seam (e.g. the tick-bar builder), not a
    behavioral flag — for a daily frame it is ``None`` and the survivors are the
    result; for ticks it rebuilds bars from the cleaned high-resolution rows.
    """

    reaggregate: Callable[[pd.DataFrame], pd.DataFrame] | None = None

    def apply(self, frame: pd.DataFrame, report: QualityReport) -> pd.DataFrame:
        drop_positions = {issue.position for issue in report.issues}
        keep = [i for i in range(len(frame)) if i not in drop_positions]
        cleaned = frame.iloc[keep].reset_index(drop=True)
        if self.reaggregate is not None:
            return self.reaggregate(cleaned)
        return cleaned


@dataclass(frozen=True)
class QuarantineUnmeasurable:
    """Drop the rows no price can be read from; keep every opinion and marker.

    A close that is not positive is a structural fact, not a statistical verdict:
    it may be corruption (Air France's 257 fabricated closes) or a real print no
    return can be taken from (WTI's -37.63 of 2020-04-20), and in both cases the
    panel must not carry it as a price. The statistical vote and the sign-change
    marker stay in the report for the operator; they never drop a row here.
    """

    def apply(self, frame: pd.DataFrame, report: QualityReport) -> pd.DataFrame:
        drop_positions = {
            issue.position for issue in report.issues if UNMEASURABLE & set(issue.detectors)
        }
        keep = [i for i in range(len(frame)) if i not in drop_positions]
        return frame.iloc[keep].reset_index(drop=True)


def run_validation(
    frame: pd.DataFrame,
    validator: EnsembleValidator,
    *,
    symbol: str,
    dataset: str,
    asset_class: str,
) -> QualityReport:
    """Run the validator BEFORE consumption (detect-before-consume, R12.3)."""
    return validator.validate(frame, symbol=symbol, dataset=dataset, asset_class=asset_class)


def apply_policy(frame: pd.DataFrame, report: QualityReport, policy: QualityPolicy) -> pd.DataFrame:
    """Apply the composition-root-selected cleaning policy (structural dispatch)."""
    return policy.apply(frame, report)


def report_to_frame(report: QualityReport) -> pd.DataFrame:
    """Serialize a report to a long-form catalog frame: one row per issue, or a
    single header row when clean (R12.6)."""
    base: dict[str, Any] = {
        "symbol": report.symbol,
        "dataset": report.dataset,
        "quality_score": report.quality_score,
        "contamination_rate": report.contamination_rate,
    }
    if not report.issues:
        return pd.DataFrame(
            [
                {
                    **base,
                    "issue_position": pd.NA,
                    "issue_label": pd.NA,
                    "issue_consensus": pd.NA,
                    "issue_detectors": pd.NA,
                }
            ]
        )
    rows = [
        {
            **base,
            "issue_position": issue.position,
            "issue_label": issue.label,
            "issue_consensus": issue.consensus,
            "issue_detectors": ",".join(issue.detectors),
        }
        for issue in report.issues
    ]
    return pd.DataFrame(rows)
