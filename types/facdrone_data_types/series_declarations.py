# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""How a futures price series is declared: construction, adjustment and roll gaps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Construction(StrEnum):
    PROVIDER_FRONT_MONTH = "provider_front_month"


class Adjustment(StrEnum):
    NONE = "none"


class RollGaps(StrEnum):
    PRESENT = "present"


@dataclass(frozen=True)
class SeriesDeclaration:
    series: str
    construction: Construction
    adjustment: Adjustment
    roll_gaps: RollGaps
    known_at: date
    score_on: str | None = None
