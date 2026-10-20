# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Tick-driven information/dollar/volatility bars (task 7.1; R13.1, R13.3,
R13.4, R13.6).

Builds bars from VALIDATED tick/intraday frames (each row a single trade:
``timestamp``, ``price``, ``volume``) rather than daily OHLCV, using the same
adaptive-threshold accumulation as the daily path so tick-derived bars stay
consistent with the daily bars.

Each bar is stamped with a ``knowledge_date`` equal to its CLOSE time — the
moment its final tick arrived and the bar became knowable — which is what makes
downstream model training lookahead-safe (R13.4). Construction is purely
mechanical, so the same tick stream yields byte-identical bars on every run
(R13.6). Library tier: array/frame-in, frame-out, no IO.

This is additive: the daily bar functions are untouched; these operate on ticks
and feed an opt-in tick-derived ``RegimeSource`` (task 7.2).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

_DEFAULT_TARGET_BARS = 200
_DEFAULT_WARMUP = 720


def _initial_threshold(increments: np.ndarray, target_bars: int, warmup: int) -> float:  # type: ignore[type-arg]
    """Seed the accumulation threshold from a warmup window."""
    n = len(increments)
    span = min(warmup, n)
    seed = float(increments[:span].sum())
    bars_expected = max(1.0, target_bars * span / max(n, 1))
    return seed / bars_expected if bars_expected > 0 else seed


def _recalibrated_threshold(recent: list[float], warmup: int, n_bars: int) -> float:
    """Adapt the threshold to the recent increment rate."""
    if len(recent) > warmup:
        del recent[:-warmup]
    rate = len(recent) / max(n_bars, 1)
    return sum(recent) / max(1, len(recent)) * rate


def _accumulate_bars(
    ticks: pd.DataFrame,
    increments: np.ndarray,  # type: ignore[type-arg]
    target_bars: int,
    warmup: int,
) -> pd.DataFrame:
    """Accumulate ticks until the running sum of ``increments`` crosses the
    (adaptive) threshold, then emit one OHLCV bar stamped with its close time."""
    if ticks.empty:
        return pd.DataFrame()

    prices = ticks["price"].to_numpy(dtype=float)
    volumes = (
        ticks["volume"].to_numpy(dtype=float) if "volume" in ticks.columns else np.zeros(len(ticks))
    )
    times = pd.to_datetime(ticks["timestamp"]).to_list()

    threshold = _initial_threshold(increments, target_bars, warmup)
    bars: list[dict[str, Any]] = []
    recent: list[float] = []
    cum = 0.0
    op = hi = lo = cl = 0.0
    vol_acc = 0.0
    open_bar = False
    close_ts: Any = None

    for i in range(len(ticks)):
        price = prices[i]
        if not open_bar:
            op = hi = lo = price
            open_bar = True
        else:
            hi = max(hi, price)
            lo = min(lo, price)
        cl = price
        vol_acc += volumes[i]
        close_ts = times[i]
        cum += increments[i]
        recent.append(float(increments[i]))

        if cum >= threshold:
            bars.append(_bar(close_ts, (op, hi, lo, cl), vol_acc))
            cum = 0.0
            open_bar = False
            vol_acc = 0.0
            threshold = _recalibrated_threshold(recent, warmup, len(bars))

    if open_bar:
        bars.append(_bar(close_ts, (op, hi, lo, cl), vol_acc))

    if not bars:
        return pd.DataFrame()
    return pd.DataFrame(bars).set_index("timestamp")


def _bar(close_ts: Any, ohlc: tuple[float, float, float, float], volume: float) -> dict[str, Any]:
    op, hi, lo, cl = ohlc
    return {
        "timestamp": close_ts,
        "open": op,
        "high": hi,
        "low": lo,
        "close": cl,
        "volume": volume,
        "knowledge_date": close_ts,
    }


def make_dollar_bars(
    ticks: pd.DataFrame,
    target_bars: int = _DEFAULT_TARGET_BARS,
    warmup: int = _DEFAULT_WARMUP,
) -> pd.DataFrame:
    """Dollar bars: accumulate price × volume until the threshold."""
    if ticks.empty:
        return pd.DataFrame()
    prices = ticks["price"].to_numpy(dtype=float)
    volumes = ticks["volume"].to_numpy(dtype=float)
    return _accumulate_bars(ticks, prices * volumes, target_bars, warmup)


def make_volatility_bars(
    ticks: pd.DataFrame,
    target_bars: int = _DEFAULT_TARGET_BARS,
    warmup: int = _DEFAULT_WARMUP,
) -> pd.DataFrame:
    """Volatility bars: accumulate absolute price change until the threshold —
    the fallback for instruments without a meaningful trade volume."""
    if ticks.empty:
        return pd.DataFrame()
    increments = ticks["price"].diff().abs().fillna(0.0).to_numpy(dtype=float)
    return _accumulate_bars(ticks, increments, target_bars, warmup)


def make_information_bars(
    ticks: pd.DataFrame,
    target_bars: int = _DEFAULT_TARGET_BARS,
    warmup: int = _DEFAULT_WARMUP,
) -> pd.DataFrame:
    """Information bars: dollar bars where volume is meaningful, volatility
    bars otherwise (mirrors the daily ``make_daily_info_bars`` dispatch)."""
    if ticks.empty:
        return pd.DataFrame()
    has_volume = "volume" in ticks.columns and float(ticks["volume"].sum()) > 0.0
    if has_volume:
        return make_dollar_bars(ticks, target_bars, warmup)
    return make_volatility_bars(ticks, target_bars, warmup)
