# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The exchange specification record of one futures contract."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal


@dataclass(frozen=True)
class FuturesContract:
    """Contract codes share a canonical price series; micros have no separate series.

    For FX, ``multiplier`` is the base-currency contract size and one price point
    is 1.0 USD per base-currency unit. ``micro_of`` names an in-table parent when
    present; a micro-only offering may have no declared parent.
    """

    symbol: str
    exchange: str
    parent_series: str
    multiplier: float
    tick_size: float
    tick_value: float
    micro_of: str | None
    limit_weight: float
    session_open: str  # HH:MM, America/New_York
    session_close: str  # HH:MM, America/New_York
    commission_per_side: float
    fees_per_side: float
    initial_margin: float
    sizing_contract: bool
    currency: Literal["USD"]

    def __post_init__(self) -> None:
        for name in ("multiplier", "tick_size", "tick_value"):
            value = getattr(self, name)
            if not isfinite(value) or value <= 0:
                raise ValueError(f"{self.symbol}: {name} must be positive and finite")
        if not abs(self.tick_value - self.multiplier * self.tick_size) <= 1e-9 * max(
            1.0, self.tick_value
        ):
            raise ValueError(f"{self.symbol}: tick_value must equal multiplier * tick_size")
        if self.limit_weight not in {1.0, 0.1}:
            raise ValueError(f"{self.symbol}: limit_weight must be 1.0 or 0.1")
        if self.micro_of == self.symbol:
            raise ValueError(f"{self.symbol}: micro_of must not refer to itself")
