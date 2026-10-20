# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 6.1: causal data-quality detectors (Req 12.1, 12.5).

Each detector flags its known synthetic anomaly and leaves clean fixtures
unflagged. Detectors are causal — they use only past/rolling context, never
future rows — so a flag at row T is unchanged by what comes after T (the
no-lookahead property that makes the validation gate safe, R12.5).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from facdrone_data.quality.detectors import (
    DetectorThresholds,
    bid_ask_outliers,
    fat_finger_outliers,
    flash_crash_outliers,
    hampel_outliers,
    isolation_forest_outliers,
    nonmonotonic_timestamps,
    robust_zscore_outliers,
    thresholds_for,
    tick_gap_outliers,
)


def _clean_prices(n: int = 120, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.1, n))
    return pd.DataFrame(
        {
            "close": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "open": close,
            "volume": rng.uniform(900.0, 1100.0, n),
        }
    )


THR = DetectorThresholds()


def test_thresholds_are_asset_class_aware() -> None:
    # Crypto tolerates bigger moves than forex before a fat-finger flag.
    assert thresholds_for("crypto").fat_finger_return > thresholds_for("forex").fat_finger_return


def test_robust_zscore_flags_a_gross_level_outlier() -> None:
    df = _clean_prices()
    df.loc[80, "close"] = 200.0  # a value far from the running distribution
    flags = robust_zscore_outliers(df, THR)
    assert bool(flags.loc[80])


def test_robust_zscore_leaves_a_clean_series_unflagged() -> None:
    assert not robust_zscore_outliers(_clean_prices(), THR).any()


def test_hampel_flags_a_local_spike() -> None:
    df = _clean_prices()
    df.loc[60, "close"] = float(df["close"].iloc[60]) + 15.0  # sharp local spike
    flags = hampel_outliers(df, THR)
    assert bool(flags.loc[60])


def test_hampel_leaves_a_clean_series_unflagged() -> None:
    assert not hampel_outliers(_clean_prices(), THR).any()


def test_isolation_forest_flags_a_gross_outlier_deterministically() -> None:
    df = _clean_prices()
    df.loc[90, "close"] = 500.0
    first = isolation_forest_outliers(df, THR)
    second = isolation_forest_outliers(df, THR)
    assert bool(first.loc[90])
    assert first.equals(second)  # pinned random_state → deterministic


def test_fat_finger_needs_both_price_jump_and_volume_spike() -> None:
    df = _clean_prices()
    df.loc[70, "close"] = float(df["close"].iloc[69]) * 1.20  # +20% jump
    df.loc[70, "volume"] = 50_000.0  # with a huge volume spike
    flags = fat_finger_outliers(df, THR)
    assert bool(flags.loc[70])
    # A price jump alone (normal volume) is not a fat-finger.
    df2 = _clean_prices()
    df2.loc[70, "close"] = float(df2["close"].iloc[69]) * 1.20
    assert not bool(fat_finger_outliers(df2, THR).loc[70])


def test_flash_crash_flags_the_drop_and_recovery() -> None:
    df = _clean_prices()
    base = float(df["close"].iloc[50])
    df.loc[51, "close"] = base * 0.90  # −10% drop
    df.loc[52, "close"] = base * 0.985  # recovery toward the prior level
    flags = flash_crash_outliers(df, THR)
    assert bool(flags.loc[52])


def test_flash_crash_leaves_a_clean_series_unflagged() -> None:
    assert not flash_crash_outliers(_clean_prices(), THR).any()


# ── tick-sequence checks (the facdrone-specific addition, R12.1) ──────────


def _clean_ticks(n: int = 50) -> pd.DataFrame:
    t0 = datetime(2024, 1, 1, 9, 0, 0)
    ts = [t0 + timedelta(seconds=i) for i in range(n)]
    return pd.DataFrame({"timestamp": ts, "bid": 1.1000, "ask": 1.1002})


def test_nonmonotonic_timestamps_flags_out_of_order_rows() -> None:
    df = _clean_ticks()
    df.loc[30, "timestamp"] = df["timestamp"].iloc[28]  # goes backwards vs row 29
    flags = nonmonotonic_timestamps(df, THR)
    assert bool(flags.loc[30])
    assert not bool(flags.loc[10])


def test_tick_gap_flags_a_long_inter_arrival_gap() -> None:
    df = _clean_ticks()
    df.loc[40, "timestamp"] = df["timestamp"].iloc[39] + timedelta(seconds=600)
    flags = tick_gap_outliers(df, DetectorThresholds(max_gap_seconds=60.0))
    assert bool(flags.loc[40])


def test_bid_ask_sanity_flags_a_crossed_quote() -> None:
    df = _clean_ticks()
    df.loc[20, "bid"] = 1.1010  # bid now above ask → crossed/inverted book
    flags = bid_ask_outliers(df, THR)
    assert bool(flags.loc[20])
    assert not bool(flags.loc[5])


def test_detectors_are_causal_appending_future_rows_does_not_change_past_flags() -> None:
    df = _clean_prices(100)
    df.loc[40, "close"] = 180.0
    before = robust_zscore_outliers(df, THR)
    extended = pd.concat([df, _clean_prices(40, seed=99)], ignore_index=True)
    after = robust_zscore_outliers(extended, THR)
    # The verdict for the first 100 rows is unchanged by appending future data.
    assert before.equals(after.iloc[:100])
