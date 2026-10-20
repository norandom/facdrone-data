# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 6.3: quality policy components + validation node (Req 12.3, 12.4, 12.6).

Validation runs BEFORE data is consumed (detect-before-consume). Cleaning is a
typed policy component selected at a composition root — KeepAll (default,
preserve raw + report) vs QuarantineContaminated (drop flagged rows, optional
reaggregation) — never a behavioral mode flag. The report serializes to a
loadable catalog frame.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from facdrone_data.acquisition.validation import (
    KeepAll,
    QualityPolicy,
    QuarantineContaminated,
    apply_policy,
    report_to_frame,
    run_validation,
)
from facdrone_data.quality.ensemble import EnsembleValidator, QualityIssue, QualityReport


def _frame(n: int = 6) -> pd.DataFrame:
    return pd.DataFrame({"close": [100.0 + i for i in range(n)], "volume": [1000.0] * n})


def _report_with_issue_at(position: int, n: int = 6) -> QualityReport:
    issue = QualityIssue(
        position=position, label=f"row-{position}", consensus=0.8, detectors=("robust_z",)
    )
    return QualityReport(
        symbol="X",
        dataset="daily_prices",
        quality_score=83.3,
        contamination_rate=1 / n,
        issues=(issue,),
    )


def _contaminated_frame(n: int = 120, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.1, n))
    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=n, freq="D"), "close": close})
    df.loc[80, "close"] = 300.0
    return df


def test_keepall_leaves_the_panel_unchanged() -> None:
    frame = _frame()
    out = KeepAll().apply(frame, _report_with_issue_at(2))
    pd.testing.assert_frame_equal(out, frame)


def test_quarantine_drops_exactly_the_flagged_rows() -> None:
    frame = _frame()
    out = QuarantineContaminated().apply(frame, _report_with_issue_at(2))
    assert len(out) == len(frame) - 1
    assert 102.0 not in out["close"].to_numpy()  # the flagged row (position 2) is gone


def test_quarantine_with_reaggregate_runs_on_survivors() -> None:
    seen: list[pd.DataFrame] = []

    def _reaggregate(cleaned: pd.DataFrame) -> pd.DataFrame:
        seen.append(cleaned)
        return cleaned.assign(reaggregated=True)

    frame = _frame()
    out = QuarantineContaminated(reaggregate=_reaggregate).apply(frame, _report_with_issue_at(2))
    assert len(seen) == 1
    assert len(seen[0]) == len(frame) - 1  # reaggregate sees the cleaned survivors
    assert "reaggregated" in out.columns


def test_quarantine_drops_by_position_not_index_label() -> None:
    frame = _frame(6)
    frame.index = pd.Index([5, 4, 3, 2, 1, 0])  # shuffled: position != index label
    out = QuarantineContaminated().apply(frame, _report_with_issue_at(2))  # issue.label "row-2"
    assert len(out) == 5
    # Position 2 (close 102.0) is removed; a .loc/label-based drop would remove 103.0.
    assert 102.0 not in out["close"].to_numpy()
    assert 103.0 in out["close"].to_numpy()


def test_quarantine_on_a_clean_report_is_a_no_op() -> None:
    frame = _frame()
    clean = QualityReport("X", "daily_prices", 100.0, 0.0, ())
    pd.testing.assert_frame_equal(QuarantineContaminated().apply(frame, clean), frame)


def test_quarantine_introduces_no_lookahead_output_is_a_subset() -> None:
    frame = _frame(10)
    out = QuarantineContaminated().apply(frame, _report_with_issue_at(4, 10))
    assert len(out) <= len(frame)
    assert set(out["close"]).issubset(set(frame["close"]))
    assert list(out.index) == list(range(len(out)))  # monotonic, no future rows


def test_run_validation_delegates_to_the_ensemble() -> None:
    report = run_validation(
        _contaminated_frame(),
        EnsembleValidator(),
        symbol="XAUUSD",
        dataset="daily_prices",
        asset_class="precious metals",
    )
    assert isinstance(report, QualityReport)
    assert 80 in {issue.position for issue in report.issues}


def test_report_to_frame_serializes_issues_for_the_catalog() -> None:
    two = QualityReport(
        "X",
        "daily_prices",
        50.0,
        2 / 6,
        (
            QualityIssue(2, "row-2", 0.8, ("robust_z", "hampel")),
            QualityIssue(4, "row-4", 0.6, ("isolation_forest",)),
        ),
    )
    frame = report_to_frame(two)
    assert len(frame) == 2
    assert (frame["symbol"] == "X").all()
    assert (frame["quality_score"] == 50.0).all()  # header scalars repeat per issue row
    assert (frame["contamination_rate"] == 2 / 6).all()
    # Per-issue field ALIGNMENT (position ↔ consensus ↔ label ↔ detectors).
    by_pos = frame.set_index("issue_position")
    assert by_pos.loc[2, "issue_consensus"] == 0.8
    assert by_pos.loc[2, "issue_label"] == "row-2"
    assert by_pos.loc[2, "issue_detectors"] == "robust_z,hampel"
    assert by_pos.loc[4, "issue_consensus"] == 0.6
    assert by_pos.loc[4, "issue_detectors"] == "isolation_forest"


def test_report_to_frame_emits_a_single_header_row_when_clean() -> None:
    clean = report_to_frame(QualityReport("X", "daily_prices", 100.0, 0.0, ()))
    issues = report_to_frame(
        QualityReport("X", "daily_prices", 50.0, 0.5, (QualityIssue(0, "row-0", 0.9, ("hampel",)),))
    )
    assert len(clean) == 1
    assert clean["quality_score"].iloc[0] == 100.0
    assert clean["contamination_rate"].iloc[0] == 0.0
    # Clean and contaminated reports share one catalog schema (uniform columns).
    assert set(clean.columns) == set(issues.columns)
    assert (
        clean[["issue_position", "issue_label", "issue_consensus", "issue_detectors"]]
        .isna()
        .all()
        .all()
    )


def test_policies_are_selected_by_type_not_a_flag() -> None:
    assert isinstance(KeepAll(), QualityPolicy)
    assert isinstance(QuarantineContaminated(), QualityPolicy)
    # apply_policy dispatches structurally — no mode branch.
    frame = _frame()
    assert (
        len(apply_policy(frame, _report_with_issue_at(2), QuarantineContaminated()))
        == len(frame) - 1
    )
    pd.testing.assert_frame_equal(apply_policy(frame, _report_with_issue_at(2), KeepAll()), frame)
