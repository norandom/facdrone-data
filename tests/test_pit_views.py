# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 5.3: point-in-time views filter on knowledge date and
forward-return maturity (R3.5, R3.6)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd

from facdrone_data.pit import point_in_time_view

AS_OF = datetime(2025, 6, 10, 18, 0, tzinfo=UTC)


def evidence_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "factor_name": ["momentum"] * 4,
            "factor_version": [1] * 4,
            "event_date": [date(2025, 6, 2), date(2025, 6, 2), date(2025, 6, 9), date(2025, 6, 2)],
            "symbol": ["GER40", "XAUUSD", "GER40", "CL=F"],
            "knowledge_date": [
                datetime(2025, 6, 2, 22, 0, tzinfo=UTC),  # knowable
                datetime(2025, 6, 11, 8, 0, tzinfo=UTC),  # post-as-of knowledge -> excluded
                datetime(2025, 6, 9, 22, 0, tzinfo=UTC),  # knowable
                datetime(2025, 6, 2, 22, 0, tzinfo=UTC),  # knowable
            ],
            "value": [0.7, 0.9, 0.1, -0.3],
            # Maturity convention: the trading-day the
            # forward-return window closes; gate is matures_on <= as_of.
            "matures_on": [
                date(2025, 6, 9),  # matured
                date(2025, 6, 9),  # matured (but unknowable)
                date(2025, 6, 16),  # matures after as-of -> excluded for evaluation
                date(2025, 6, 10),  # matures exactly on as-of -> included
            ],
        }
    )


def test_knowledge_date_filter() -> None:
    view = point_in_time_view(evidence_frame().drop(columns=["matures_on"]), as_of=AS_OF)
    assert set(view["symbol"]) == {"GER40", "CL=F"}
    assert len(view) == 3  # both GER40 rows + CL=F; XAUUSD unknowable


def test_maturity_filter_applies_when_column_present() -> None:
    view = point_in_time_view(evidence_frame(), as_of=AS_OF)
    # XAUUSD: unknowable. GER40 (Jun 9 event): immature. Boundary row
    # (matures exactly on as-of) is included, matching the `<=` gate.
    assert sorted(zip(view["symbol"], view["event_date"], strict=True)) == [
        ("CL=F", date(2025, 6, 2)),
        ("GER40", date(2025, 6, 2)),
    ]


def test_view_never_mutates_input() -> None:
    frame = evidence_frame()
    before = frame.copy()
    point_in_time_view(frame, as_of=AS_OF)
    pd.testing.assert_frame_equal(frame, before)
