# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Weighted-voting quality ensemble + QualityReport (task 6.2; R12.1, R12.2).

The causal detectors (task 6.1) are combined by weighted voting into a per-row
outlier verdict with a consensus strength, then summarized into a per-(symbol,
dataset) :class:`QualityReport`. Consensus is normalized over the weights of
the detectors *applicable* to the frame (a price panel votes with the five
price detectors; a tick frame additionally votes with the tick-sequence
checks), so a row is flagged when applicable detectors carrying ≥ ``threshold``
of the weight agree.

Pure and causal: the same input yields the same report (the IsolationForest
seed is pinned in task 6.1), so the report is a faithful, reproducible artifact.
The :class:`QualityReport` is the value object persisted by the validation node
(task 6.3) and consumed by the :class:`QualityPolicy`; it lives in the library
tier because it is the return type of pure code.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

import pandas as pd

from facdrone_data.quality.detectors import (
    DetectorThresholds,
    bid_ask_outliers,
    fat_finger_outliers,
    flash_crash_outliers,
    hampel_outliers,
    iat_variance_outliers,
    isolation_forest_outliers,
    nonmonotonic_timestamps,
    nonpositive_prices,
    price_sign_changes,
    robust_zscore_outliers,
    thresholds_for,
    tick_gap_outliers,
)

DetectorFn = Callable[[pd.DataFrame, DetectorThresholds], pd.Series]


@dataclass(frozen=True)
class DetectorSpec:
    """One entry in the voting registry: a detector, its weight, and the
    columns it needs to be applicable."""

    name: str
    fn: DetectorFn
    weight: float
    required_columns: frozenset[str]


# Weights follow the reference quality stack (price detectors sum to 1.0); the
# tick-sequence checks carry their own weights and only apply to tick frames.
DETECTORS: tuple[DetectorSpec, ...] = (
    DetectorSpec("robust_z", robust_zscore_outliers, 0.15, frozenset({"close"})),
    DetectorSpec("hampel", hampel_outliers, 0.20, frozenset({"close"})),
    DetectorSpec("isolation_forest", isolation_forest_outliers, 0.30, frozenset({"close"})),
    DetectorSpec("fat_finger", fat_finger_outliers, 0.20, frozenset({"close", "volume"})),
    DetectorSpec("flash_crash", flash_crash_outliers, 0.15, frozenset({"close"})),
    DetectorSpec("nonmonotonic_ts", nonmonotonic_timestamps, 0.25, frozenset({"timestamp"})),
    DetectorSpec("tick_gap", tick_gap_outliers, 0.20, frozenset({"timestamp"})),
    DetectorSpec("iat_variance", iat_variance_outliers, 0.15, frozenset({"timestamp"})),
    DetectorSpec("bid_ask", bid_ask_outliers, 0.40, frozenset({"bid", "ask"})),
)

# Structural integrity checks sit OUTSIDE the vote. A non-positive price or a
# sign change is not an opinion to be outvoted by four statistical detectors
# that saw nothing unusual, and giving them a vote heavy enough to fire alone
# would halve every existing detector's share of the consensus. They therefore
# veto: any row they flag is an issue, whatever the vote said.
INTEGRITY_DETECTORS: tuple[DetectorSpec, ...] = (
    DetectorSpec("nonpositive_price", nonpositive_prices, 0.0, frozenset({"close"})),
    DetectorSpec("price_sign_change", price_sign_changes, 0.0, frozenset({"close"})),
)

# Of the two, only a non-positive close makes the row itself unusable: no return and
# no price feature can be taken from it. The sign change is a marker — it names both
# edges of a fabricated run so the operator sees where it begins and ends — and the
# exit edge (Air France's +61.65 on 2005-07-18) is a genuine print. A door that drops
# rows must therefore act on this set and leave the marker in the report.
UNMEASURABLE: frozenset[str] = frozenset({"nonpositive_price"})

_DEFAULT_THRESHOLD = 0.5


@dataclass(frozen=True)
class QualityIssue:
    """One flagged row: where it is, how strong the consensus, and which
    detectors fired."""

    position: int
    label: str
    consensus: float
    detectors: tuple[str, ...]


@dataclass(frozen=True)
class QualityReport:
    """Auditable per-(symbol, dataset) quality summary (R12.1)."""

    symbol: str
    dataset: str
    quality_score: float  # 0..100
    contamination_rate: float  # fraction of rows flagged
    issues: tuple[QualityIssue, ...]


def weighted_verdict(
    masks: Mapping[str, pd.Series],
    weights: Mapping[str, float],
    *,
    threshold: float = _DEFAULT_THRESHOLD,
) -> tuple[pd.Series, pd.Series]:
    """Combine detector masks into (consensus, verdict) by weighted voting.

    ``consensus`` is the fired-weight fraction of the total applicable weight;
    ``verdict`` is ``consensus >= threshold``. Empty input → empty results."""
    if not masks:
        empty = pd.Series([], dtype=float)
        return empty, empty.astype(bool)
    total_weight = sum(weights[name] for name in masks)
    index = next(iter(masks.values())).index
    score = pd.Series(0.0, index=index)
    for name, mask in masks.items():
        score = score + mask.astype(float) * weights[name]
    consensus = score / total_weight if total_weight > 0 else score * 0.0
    verdict = consensus >= threshold
    return consensus, verdict


def _row_labels(frame: pd.DataFrame) -> list[str]:
    if "date" in frame.columns:
        return [str(v) for v in pd.to_datetime(frame["date"]).to_list()]
    if "timestamp" in frame.columns:
        return [str(v) for v in pd.to_datetime(frame["timestamp"]).to_list()]
    return [str(v) for v in frame.index.to_list()]


@dataclass(frozen=True)
class EnsembleValidator:
    """The concrete weighted-voting validator (a typed component, not a flag).

    Bound at a composition root; runs every applicable detector and votes.
    """

    threshold: float = _DEFAULT_THRESHOLD

    def validate(
        self,
        frame: pd.DataFrame,
        *,
        symbol: str,
        dataset: str,
        asset_class: str,
    ) -> QualityReport:
        thr: DetectorThresholds = thresholds_for(asset_class)
        applicable = [spec for spec in DETECTORS if spec.required_columns <= set(frame.columns)]
        if not applicable or len(frame) == 0:
            return QualityReport(symbol, dataset, 100.0, 0.0, ())

        masks: dict[str, pd.Series] = {spec.name: spec.fn(frame, thr) for spec in applicable}
        weights = {spec.name: spec.weight for spec in applicable}
        consensus, verdict = weighted_verdict(masks, weights, threshold=self.threshold)

        integrity = {
            spec.name: spec.fn(frame, thr)
            for spec in INTEGRITY_DETECTORS
            if spec.required_columns <= set(frame.columns)
        }
        for mask in integrity.values():
            verdict = verdict | mask
        masks.update(integrity)

        labels = _row_labels(frame)
        issues: list[QualityIssue] = []
        verdict_values = verdict.to_numpy()
        consensus_values = consensus.to_numpy()
        for position in range(len(frame)):
            if not bool(verdict_values[position]):
                continue
            fired = tuple(name for name, mask in masks.items() if bool(mask.to_numpy()[position]))
            issues.append(
                QualityIssue(
                    position=position,
                    label=labels[position],
                    consensus=round(float(consensus_values[position]), 6),
                    detectors=fired,
                )
            )

        contamination = len(issues) / len(frame)
        quality_score = round(100.0 * (1.0 - contamination), 4)
        return QualityReport(symbol, dataset, quality_score, contamination, tuple(issues))
