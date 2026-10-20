# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""A deterministic, offline ``MarketDataClient`` fake (task 3; R6.1, R6.4).

``fetch`` derives every row from ``(provider_symbol, date)`` by a pure formula,
so:

* re-fetching the same range returns byte-identical rows (drives idempotency),
* fetching a sub-range returns exactly that range (drives partial-range), and
* a second client constructed with a different ``price_offset`` returns a
  different value for the same key (drives the merge conflict path).

Every ``fetch`` is recorded in ``calls`` so a test can assert zero downloads
over an already-covered range. No network is ever touched (no ``hash()`` — its
per-process salt would break determinism; a stable char-sum seed is used).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pandas as pd

from facdrone_data_types.ports import FetchRequest
from facdrone_data_types.symbols import Provider

_KNOWLEDGE_TIME = time(23, 0, tzinfo=UTC)  # daily values knowable at the close


def _stable_seed(symbol: str) -> int:
    """A per-process-stable seed (unlike the salted builtin ``hash``)."""
    return sum(ord(ch) for ch in symbol)


def _days(start: date, end: date) -> list[date]:
    if start > end:
        return []
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _row(symbol: str, day: date, price_offset: float) -> dict[str, Any]:
    seed = _stable_seed(symbol)
    close = 100.0 + (seed % 50) + (day.toordinal() % 7) * 0.5 + price_offset
    return {
        "symbol": symbol,
        "date": day,
        "open": round(close - 0.2, 4),
        "high": round(close + 0.3, 4),
        "low": round(close - 0.3, 4),
        "close": round(close, 4),
        "volume": 1000.0 + (seed % 200),
        "event_date": day,
        "knowledge_date": datetime.combine(day, _KNOWLEDGE_TIME),
    }


def synthetic_daily_rows(
    symbol: str, start: date, end: date, price_offset: float = 0.0
) -> pd.DataFrame:
    """The pure backing series — exposed so tests can build the expected panel."""
    return pd.DataFrame([_row(symbol, day, price_offset) for day in _days(start, end)])


@dataclass
class FakeMarketDataClient:
    """A provider stand-in conforming to ``domain.ports.MarketDataClient``."""

    provider: Provider
    served_datasets: tuple[str, ...] = ("daily_prices",)
    price_offset: float = 0.0
    calls: list[FetchRequest] = field(default_factory=list)

    def fetch(self, req: FetchRequest) -> pd.DataFrame:
        self.calls.append(req)
        return synthetic_daily_rows(req.provider_symbol, req.start, req.end, self.price_offset)

    def datasets(self) -> tuple[str, ...]:
        return self.served_datasets

    @property
    def fetched_days(self) -> int:
        """Total calendar days requested across all fetch calls."""
        return sum(len(_days(call.start, call.end)) for call in self.calls)
