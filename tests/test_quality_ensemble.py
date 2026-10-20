# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 6.2: weighted-voting ensemble + QualityReport (Req 12.1, 12.2).

The ensemble combines the causal detectors by weighted voting into a per-row
verdict with a consensus strength, and emits a deterministic per-(symbol,
dataset) quality report (score 0–100, contamination rate, dated row-level
issues). Same input → same report.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from facdrone_data.quality.ensemble import (
    EnsembleValidator,
    QualityReport,
    weighted_verdict,
)


def _clean_prices(n: int = 120, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.1, n))
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=n, freq="D"),
            "close": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "open": close,
            "volume": rng.uniform(900.0, 1100.0, n),
        }
    )


def test_weighted_verdict_normalizes_over_applicable_weights() -> None:
    masks = {
        "a": pd.Series([True, False, False]),
        "b": pd.Series([True, True, False]),
        "c": pd.Series([False, True, False]),
    }
    weights = {"a": 0.5, "b": 0.3, "c": 0.2}
    consensus, verdict = weighted_verdict(masks, weights, threshold=0.5)
    # row0: a+b (0.8) ; row1: b+c (0.5) ; row2: none (0.0)
    assert list(consensus) == [0.8, 0.5, 0.0]
    assert list(verdict) == [True, True, False]


def test_validate_flags_a_gross_outlier_and_reports_it() -> None:
    df = _clean_prices()
    df.loc[80, "close"] = 300.0  # robust-z + Hampel + IsolationForest agree
    report = EnsembleValidator().validate(
        df, symbol="XAUUSD", dataset="daily_prices", asset_class="precious metals"
    )
    assert report.symbol == "XAUUSD"
    assert report.dataset == "daily_prices"
    flagged_positions = {issue.position for issue in report.issues}
    assert 80 in flagged_positions
    assert report.contamination_rate > 0.0
    assert 0.0 <= report.quality_score <= 100.0
    # The flagging detectors are named on the row-level issue.
    issue = next(i for i in report.issues if i.position == 80)
    assert "robust_z" in issue.detectors
    assert issue.consensus >= 0.5


def test_validate_leaves_a_clean_panel_with_a_top_score() -> None:
    report = EnsembleValidator().validate(
        _clean_prices(), symbol="EURUSD", dataset="daily_prices", asset_class="forex"
    )
    # A lone IsolationForest vote (contamination-forced) is below the 0.5
    # consensus threshold, so a clean panel reports no consumed issues.
    assert report.issues == ()
    assert report.contamination_rate == 0.0
    assert report.quality_score == 100.0


def test_report_is_deterministic() -> None:
    df = _clean_prices()
    df.loc[80, "close"] = 300.0
    first = EnsembleValidator().validate(
        df, symbol="XAUUSD", dataset="daily_prices", asset_class="precious metals"
    )
    second = EnsembleValidator().validate(
        df, symbol="XAUUSD", dataset="daily_prices", asset_class="precious metals"
    )
    assert first == second
    assert isinstance(first, QualityReport)


def test_issues_carry_a_human_readable_date_label() -> None:
    df = _clean_prices()
    df.loc[80, "close"] = 300.0
    report = EnsembleValidator().validate(
        df, symbol="XAUUSD", dataset="daily_prices", asset_class="precious metals"
    )
    issue = next(i for i in report.issues if i.position == 80)
    assert "2024-03-21" in issue.label  # 2024-01-01 + 80 days
