# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 7.1: tick-driven bar builder (Req 13.1, 13.3, 13.4, 13.6).

Dollar/volatility/information bars built from VALIDATED tick frames (not daily
OHLCV), mirroring the daily bar math. Each bar carries a knowledge_date (its
close time — the moment it became knowable), construction is lookahead-safe,
and the output is byte-identical across two runs (deterministic, R13.6).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from facdrone_data.bars.tick_bars import (
    make_dollar_bars,
    make_information_bars,
    make_volatility_bars,
)

BAR_COLUMNS = {"open", "high", "low", "close", "volume", "knowledge_date"}


def _tick_stream(n: int = 2000, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    price = 100.0 + np.cumsum(rng.normal(0.0, 0.01, n))
    ts = pd.date_range("2024-01-02 09:00", periods=n, freq="s", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "price": price, "volume": rng.uniform(1.0, 5.0, n)})


def test_dollar_bars_are_deterministic_across_two_runs() -> None:
    ticks = _tick_stream()
    pd.testing.assert_frame_equal(make_dollar_bars(ticks), make_dollar_bars(ticks))


def test_dollar_bars_carry_a_knowledge_date_equal_to_the_close_time() -> None:
    bars = make_dollar_bars(_tick_stream())
    assert not bars.empty
    assert BAR_COLUMNS <= set(bars.columns) | {bars.index.name}
    # knowledge_date is the bar's close time — when the bar became knowable.
    assert (bars["knowledge_date"].to_numpy() == bars.index.to_numpy()).all()


def test_knowledge_dates_are_monotonic_and_within_the_stream() -> None:
    ticks = _tick_stream()
    bars = make_dollar_bars(ticks)
    kd = bars["knowledge_date"]
    assert kd.is_monotonic_increasing
    # No bar is knowable before the last tick that closed it (lookahead-safe).
    assert kd.max() <= ticks["timestamp"].max()
    assert kd.min() >= ticks["timestamp"].min()


def test_ohlc_relationships_hold_for_every_bar() -> None:
    bars = make_dollar_bars(_tick_stream())
    assert (bars["high"] >= bars["low"]).all()
    assert (bars["high"] >= bars["close"]).all()
    assert (bars["high"] >= bars["open"]).all()
    assert (bars["low"] <= bars["close"]).all()
    assert (bars["low"] <= bars["open"]).all()
    assert (bars["volume"] > 0).all()


def test_volatility_bars_build_deterministically() -> None:
    ticks = _tick_stream()
    bars = make_volatility_bars(ticks)
    assert not bars.empty
    assert "knowledge_date" in bars.columns
    pd.testing.assert_frame_equal(bars, make_volatility_bars(ticks))


def test_information_bars_dispatch_to_dollar_bars_when_volume_present() -> None:
    ticks = _tick_stream()
    pd.testing.assert_frame_equal(make_information_bars(ticks), make_dollar_bars(ticks))


def test_information_bars_fall_back_to_volatility_without_volume() -> None:
    ticks = _tick_stream().drop(columns=["volume"])
    bars = make_information_bars(ticks)
    assert not bars.empty
    assert "knowledge_date" in bars.columns


def test_empty_tick_stream_yields_empty_bars() -> None:
    empty = pd.DataFrame({"timestamp": [], "price": [], "volume": []})
    assert make_dollar_bars(empty).empty
