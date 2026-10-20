# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Dukascopy tick transport → daily order-flow.

The acquisition half of the order-flow family: fetch Dukascopy hourly ``.bi5`` tick
files (lzma-compressed bid/ask + volume), parse the binary records, and aggregate
each UTC feed day into the ``duka_order_flow`` row via the pure
``library.order_flow.aggregate_daily_order_flow`` kernel. The hour fetch is an
injected ``HourFetch`` transport so the client is offline-testable with recorded
``.bi5`` bytes; ``live_dukascopy`` builds the real transport, lazily importing
``requests``/``lzma`` so the module imports without a network stack.

Dukascopy is keyless. ``provider_symbol`` is the Dukascopy symbol (via ``to_duka``);
its price divisor decodes the integer-encoded bid/ask (``DUKA_SYMBOLS``).
"""

from __future__ import annotations

import struct
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import sleep as _sleep
from typing import TYPE_CHECKING, Any

import pandas as pd

from facdrone_data.order_flow import aggregate_daily_order_flow
from facdrone_data.providers.policy import TIMEOUT_SECONDS as _TIMEOUT_SECONDS
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from facdrone_data_types.ports import FetchRequest

_BASE = "https://www.dukascopy.com/datafeed"
_HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36", "Accept": "*/*"}
_DEFAULT_DIVISOR = 100000
_HOUR_RETRIES = 3  # tolerate a transient fault on one hour

# Dukascopy price divisors: decode the integer bid/ask.
DUKA_SYMBOLS: dict[str, int] = {
    "XAUUSD": 1000,
    "XAGUSD": 100000,
    "XPTUSD": 1000,
    "XPDUSD": 1000,
    "XCUUSD": 100000,
    "USOUSD": 1000,
    "NGUSD": 1000,
    "HOOUSD": 1000,
    "UKOUSD": 1000,
    "COCOUSD": 1,
    "COFUSD": 1000,
    "COTUSD": 100000,
    "SUGUSD": 100000,
    "CORNUSD": 1000,
    "SOYUSD": 1000,
    "WHTUSD": 1000,
    "EURUSD": 100000,
    "GBPUSD": 100000,
    "USDJPY": 1000,
    "USDCHF": 100000,
    "AUDUSD": 100000,
    "NZDUSD": 100000,
    "USDCAD": 100000,
    "EURJPY": 1000,
    "GBPJPY": 1000,
    "EURGBP": 100000,
    "EURAUD": 100000,
    "EURCHF": 100000,
    "EURCAD": 100000,
    "CADCHF": 100000,
}

# (dukascopy symbol, hour start) -> decompressed .bi5 bytes, or None for no data.
HourFetch = Callable[[str, datetime], "bytes | None"]


def _feed_is_open(hour_start: datetime) -> bool:
    """Feed hours are UTC; the weekly gap is Friday 22:00 through Sunday 21:00."""
    hour_of_week = hour_start.weekday() * 24 + hour_start.hour
    return not 118 <= hour_of_week < 166


def _parse_bi5(raw: bytes, hour_start: datetime, divisor: float) -> list[dict[str, Any]]:
    """Parse one hour of decompressed ``.bi5`` ticks.

    Each 20-byte record is ``>IIIff`` = (ms-into-hour, ask_raw, bid_raw, ask_vol,
    bid_vol); raw prices are divided by the symbol divisor."""
    ticks: list[dict[str, Any]] = []
    for i in range(len(raw) // 20):
        ms, ask_raw, bid_raw, ask_vol, bid_vol = struct.unpack(">IIIff", raw[i * 20 : (i + 1) * 20])
        ask = ask_raw / divisor
        bid = bid_raw / divisor
        ticks.append(
            {
                "timestamp": hour_start + timedelta(milliseconds=ms),
                "ask": ask,
                "bid": bid,
                "spread": round(ask - bid, 6),
                "ask_vol": ask_vol,
                "bid_vol": bid_vol,
            }
        )
    return ticks


@dataclass(frozen=True)
class DukascopyOrderFlowClient:
    """``MarketDataClient`` for Dukascopy daily order-flow (``duka_order_flow``)."""

    fetch_hour: HourFetch

    @property
    def provider(self) -> Provider:
        return Provider.DUKASCOPY

    def fetch(self, req: FetchRequest) -> pd.DataFrame:
        if req.dataset != "duka_order_flow":
            return pd.DataFrame()
        divisor = DUKA_SYMBOLS.get(req.provider_symbol, _DEFAULT_DIVISOR)
        rows: list[dict[str, Any]] = []
        day = req.start
        while day <= req.end:
            ticks: list[dict[str, Any]] = []
            for hour in range(24):
                hour_start = datetime(day.year, day.month, day.day, hour)
                if not _feed_is_open(hour_start):
                    continue
                raw = self.fetch_hour(req.provider_symbol, hour_start)
                if raw:
                    ticks.extend(_parse_bi5(raw, hour_start, divisor))
            if ticks:
                metrics = aggregate_daily_order_flow(pd.DataFrame(ticks))
                rows.append({**metrics, "symbol": req.provider_symbol, "date": day})
            day += timedelta(days=1)
        return pd.DataFrame(rows)

    def datasets(self) -> tuple[str, ...]:
        return ("duka_order_flow",)


# One hour's retry policy by outcome: (attempts allowed before giving up, sleep before the
# next). A throttle (429/503) gets three retries with 1 + 2 + 4 s; a transient fault keeps
# three total attempts with 1 + 2 s; any other status gives up after three attempts.
_RETRY_LIMIT: Mapping[str, int] = {
    "throttle": _HOUR_RETRIES,
    "fault": _HOUR_RETRIES - 1,
    "other": _HOUR_RETRIES - 1,
}
_RETRY_DELAY: Mapping[str, Callable[[int], float]] = {
    "throttle": lambda attempt: float(2**attempt),
    "fault": lambda attempt: float(1 + attempt),
    "other": lambda _attempt: 0.0,
}


def _attempt(session: Any, url: str) -> tuple[bytes | None, str]:
    """One request: (payload, outcome) with outcome done | throttle | fault | other.

    200 with a payload decompresses (done); 404 is a definitive "no ticks" (done, None); a
    request or decompress fault is swallowed (fault), never raised."""
    import lzma

    try:
        response = session.get(url, headers=_HEADERS, timeout=_TIMEOUT_SECONDS)
        if response.status_code == 200 and len(response.content) > 50:
            return lzma.decompress(response.content), "done"
    except Exception:  # noqa: BLE001, swallow transient request/decompress faults
        return None, "fault"
    if response.status_code == 404:
        return None, "done"
    return None, "throttle" if response.status_code in (429, 503) else "other"


def live_dukascopy(*, sleep: Callable[[float], None] = _sleep) -> HourFetch:
    """The live hour fetcher (wired only at the composition root). Lazily imports
    ``requests``/``lzma`` and owns one session for the fetcher's lifetime."""
    import requests

    session = requests.Session()

    def _fetch_hour(duka_sym: str, hour_start: datetime) -> bytes | None:
        if not _feed_is_open(hour_start):
            return None
        # Dukascopy months are 0-indexed (January = 00). URL parts are derived from
        # the fixed feed base + the validated symbol/timestamp, not user input.
        url = (
            f"{_BASE}/{duka_sym}/{hour_start.year}/{hour_start.month - 1:02d}/"
            f"{hour_start.day:02d}/{hour_start.hour:02d}h_ticks.bi5"
        )
        for attempt in range(_HOUR_RETRIES + 1):
            payload, outcome = _attempt(session, url)
            if outcome == "done" or attempt >= _RETRY_LIMIT[outcome]:
                return payload
            delay = _RETRY_DELAY[outcome](attempt)
            if delay:
                sleep(delay)
        return None

    return _fetch_hour
