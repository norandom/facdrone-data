# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""yfinance daily-price fallback transport (R4.2, R4.4; D8).

The yfinance fallback prices ONLY the symbols FMP cannot (e.g. wheat ``ZW=F``);
precedence is structural in ``normalizers/prices.py``, not a dispatcher here. This
module is the LIVE fetch layer for that port: it turns a native yfinance frame
into the RAW shape ``DailyPricesNormalizer`` reads.

The normalizer consumes a frame with lowercase ``date/open/high/low/close/volume``
columns — it re-keys ``symbol`` from the canonical, ``coerce_date``s ``date``,
``_int_volume``s ``volume``, and drops null-``close`` rows before projecting to the
``daily_prices`` table. So ``fetch`` returns exactly those lowercase columns.

The network call is an injected ``YfDownload`` transport so the adapter is pure and
offline-testable; the live transport (``live_yfinance``) lazily imports ``yfinance``
inside the factory, so the module imports cleanly even when the SDK is absent. The
native yfinance frame carries capitalized columns (``Open``/``High``/…) on a
``DatetimeIndex`` named ``Date``; the live transport lowercases the columns and
resets the index to a ``date`` column (``df.columns = [c.lower() …]``;
``df["date"] = df.index.date``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from datetime import date

    from facdrone_data_types.ports import FetchRequest

log = logging.getLogger(__name__)

# (yf_symbol, start, end) -> a native yfinance daily OHLCV frame. May raise; the
# adapter catches and fails open (an empty frame skips the symbol).
YfDownload = Callable[[str, "date", "date"], pd.DataFrame]

# The raw columns ``DailyPricesNormalizer`` reads (lowercase OHLCV + date).
_RAW_COLUMNS = ("date", "open", "high", "low", "close", "volume")


def _lowercase_reset(native: pd.DataFrame) -> pd.DataFrame:
    """Native yfinance frame → lowercase ``date/open/high/low/close/volume``.

    Collapses a possible ``MultiIndex`` to the
    field level, lowercases the OHLCV columns, and lifts the ``DatetimeIndex`` into a
    ``date`` column. Tolerant of an already-flat ``date`` column so a recorded
    payload can be fed straight through an injected transport in tests."""
    frame = native.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    frame.columns = [str(c).lower() for c in frame.columns]
    if "date" not in frame.columns:
        frame = frame.reset_index()
        frame.columns = [str(c).lower() for c in frame.columns]
        if "date" not in frame.columns and "index" in frame.columns:
            frame = frame.rename(columns={"index": "date"})
    present = [c for c in _RAW_COLUMNS if c in frame.columns]
    return frame.loc[:, present].reset_index(drop=True)


def live_yfinance() -> YfDownload:
    """The live ``yfinance``-based transport (wired only at the composition root).

    ``yfinance`` is imported lazily so this module imports cleanly even when the
    SDK is not installed. The transport calls ``yf.download(sym,
    start, end, interval="1d")``; the ``end`` is the inclusive range end, so we add
    a day because yfinance treats ``end`` as exclusive."""

    def _download(symbol: str, start: date, end: date) -> pd.DataFrame:
        from datetime import timedelta

        import yfinance as yf  # lazy, optional SDK

        native = yf.download(
            symbol,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),
            interval="1d",
            progress=False,
            auto_adjust=False,
        )
        if native is None or native.empty:
            return pd.DataFrame()
        return _lowercase_reset(native)

    return _download


@dataclass(frozen=True)
class YFinanceDailyClient:
    """``MarketDataClient`` for the yfinance daily-price fallback (R4.2).

    ``fetch`` returns the RAW lowercase ``date/open/high/low/close/volume`` frame
    that ``DailyPricesNormalizer`` reads; ``symbol`` is re-keyed to the canonical by
    the normalizer (R4.3), so it is not added here. Any non-``daily_prices`` dataset
    returns empty → the refresher fails open and skips it."""

    transport: YfDownload

    @property
    def provider(self) -> Provider:
        return Provider.YFINANCE

    def fetch(self, req: FetchRequest) -> pd.DataFrame:
        if req.dataset != "daily_prices":
            return pd.DataFrame()  # yfinance serves only the daily fallback
        try:
            native = self.transport(req.provider_symbol, req.start, req.end)
        except Exception as exc:  # noqa: BLE001 — fail-open per symbol
            log.warning("yfinance download failed for %s: %s", req.provider_symbol, exc)
            return pd.DataFrame()
        if native.empty:
            return pd.DataFrame()
        return _lowercase_reset(native)

    def datasets(self) -> tuple[str, ...]:
        return ("daily_prices",)
