# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Information holders and the trading-calendar contract; no calendar IO."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Protocol


class CalendarNotCovered(ValueError):
    """A requested session is outside the declaration's knowledge/coverage interval."""


class BoundaryKind(StrEnum):
    OPEN = "open"
    FLAT_DEADLINE = "flat_deadline"
    CLOSE = "close"
    ASSESSMENT = "assessment"


@dataclass(frozen=True)
class SessionBounds:
    opens_at: datetime
    closes_at: datetime


@dataclass(frozen=True)
class Boundary:
    kind: BoundaryKind
    at: datetime


class TradingCalendar(Protocol):
    @property
    def calendar_id(self) -> str: ...

    def trading_day(self, as_of: datetime) -> date: ...

    def session(self, as_of: datetime) -> SessionBounds | None: ...

    def next_boundary(self, as_of: datetime) -> Boundary: ...

    def is_covered(self, day: date) -> bool: ...

    def boundaries(self, day: date) -> tuple[Boundary, ...]: ...


def trading_days(calendar: TradingCalendar, first: date, last: date) -> tuple[date, ...]:
    """Enumerate only declared trading days; missing calendar coverage refuses the report."""
    days = (first + timedelta(days=n) for n in range((last - first).days + 1))
    return tuple(day for day in days if calendar.boundaries(day))


def flat_deadline(calendar: TradingCalendar, as_of: datetime) -> datetime | None:
    """The product-adjusted deadline of the trading day containing this instant."""
    return next(
        (
            boundary.at
            for boundary in calendar.boundaries(calendar.trading_day(as_of))
            if boundary.kind is BoundaryKind.FLAT_DEADLINE
        ),
        None,
    )
