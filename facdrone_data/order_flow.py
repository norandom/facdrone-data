# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Daily order-flow aggregation from intraday ticks.

Pure math, array-in / dict-out, no IO (library tier): given a day's bid/ask ticks
it computes the daily order-flow metrics stored in ``duka_order_flow`` — spread
statistics, tick volume, VWAP, the day's high/low/close, and the up/down tick
imbalance (a microstructure order-flow proxy: positive = more buy-side pressure).

The formulae, the rounding, and the ``mid = (ask+bid)/2`` / ``mid.diff()``
definitions are fixed so the metrics stay bit-comparable with stored rows. The
acquisition (``.bi5`` fetch/parse) lives in ``shells/providers/dukascopy_source`` — this module
is the quant kernel only, so a future order-flow factor can reuse it without touching the
network path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

# The order-flow metric columns this kernel produces (the value columns of
# ``duka_order_flow``, in table order — the symbol/date keys are added downstream).
ORDER_FLOW_METRICS: tuple[str, ...] = (
    "n_ticks",
    "avg_spread",
    "max_spread",
    "spread_std",
    "tick_vol",
    "vwap",
    "high",
    "low",
    "close",
    "upticks",
    "downticks",
    "tick_imbalance",
)


def aggregate_daily_order_flow(ticks: pd.DataFrame) -> dict[str, float | int]:
    """Aggregate one day's ticks into daily order-flow metrics.

    ``ticks`` carries the parsed columns ``ask``/``bid``/``spread``/``ask_vol``/
    ``bid_vol`` in chronological order. Returns ``{}`` for an empty frame (the day
    had no ticks — weekend/holiday)."""
    if ticks.empty:
        return {}
    mid = (ticks["ask"] + ticks["bid"]) / 2
    mid_changes = mid.diff().dropna()
    up = int((mid_changes > 0).sum())
    down = int((mid_changes < 0).sum())
    return {
        "n_ticks": len(ticks),
        "avg_spread": round(float(ticks["spread"].mean()), 6),
        "max_spread": round(float(ticks["spread"].max()), 6),
        "spread_std": round(float(ticks["spread"].std()), 6),
        "tick_vol": round(float(ticks["ask_vol"].sum() + ticks["bid_vol"].sum()), 1),
        "vwap": round(float(mid.mean()), 5),
        "high": round(float(ticks["ask"].max()), 5),
        "low": round(float(ticks["bid"].min()), 5),
        "close": round(float(mid.iloc[-1]), 5),
        # Order-flow imbalance: positive = more up-tick (buy-side) activity.
        "upticks": up,
        "downticks": down,
        "tick_imbalance": round(float(up - down) / max(len(mid_changes), 1), 4),
    }
