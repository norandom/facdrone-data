# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Golden parity for the order-flow aggregation kernel."""

from __future__ import annotations

import pandas as pd

from facdrone_data.order_flow import ORDER_FLOW_METRICS, aggregate_daily_order_flow

# Four chronological ticks (constant 0.0002 spread); mids 1.1001, 1.1004, 1.1000,
# 1.1007 → changes [+,-,+] → up=2, down=1.
_TICKS = pd.DataFrame(
    [
        {"ask": 1.1002, "bid": 1.1000, "spread": 0.0002, "ask_vol": 1.0, "bid_vol": 2.0},
        {"ask": 1.1005, "bid": 1.1003, "spread": 0.0002, "ask_vol": 3.0, "bid_vol": 1.0},
        {"ask": 1.1001, "bid": 1.0999, "spread": 0.0002, "ask_vol": 2.0, "bid_vol": 2.0},
        {"ask": 1.1008, "bid": 1.1006, "spread": 0.0002, "ask_vol": 1.0, "bid_vol": 1.0},
    ]
)


def test_empty_ticks_yield_empty_metrics() -> None:
    assert aggregate_daily_order_flow(pd.DataFrame()) == {}


def test_aggregation_matches_the_reference_formula() -> None:
    out = aggregate_daily_order_flow(_TICKS)
    assert set(out) == set(ORDER_FLOW_METRICS)
    assert out["n_ticks"] == 4
    assert out["avg_spread"] == 0.0002
    assert out["max_spread"] == 0.0002
    assert out["spread_std"] == 0.0  # constant spread
    assert out["tick_vol"] == 13.0  # ask_vol sum 7 + bid_vol sum 6
    assert out["high"] == 1.1008  # max ask
    assert out["low"] == 1.0999  # min bid
    assert out["close"] == 1.1007  # last mid
    assert out["upticks"] == 2 and out["downticks"] == 1
    assert out["tick_imbalance"] == round((2 - 1) / 3, 4)  # 0.3333
    assert out["vwap"] == round((1.1001 + 1.1004 + 1.1000 + 1.1007) / 4, 5)


def test_imbalance_is_zero_for_balanced_flow() -> None:
    balanced = pd.DataFrame(
        [
            {"ask": 1.0, "bid": 1.0, "spread": 0.0, "ask_vol": 1.0, "bid_vol": 1.0},
            {"ask": 1.1, "bid": 1.1, "spread": 0.0, "ask_vol": 1.0, "bid_vol": 1.0},  # up
            {"ask": 1.0, "bid": 1.0, "spread": 0.0, "ask_vol": 1.0, "bid_vol": 1.0},  # down
        ]
    )
    assert aggregate_daily_order_flow(balanced)["tick_imbalance"] == 0.0
