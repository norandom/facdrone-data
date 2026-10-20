# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""FMP request wrapper + per-endpoint fetchers (R2.2, R2.4).

The wrapper prepends the ``apikey`` param, normalizes list/dict/string/error payloads, and
**fails open**: any error returns ``[]`` so one bad family never aborts the download (R2.4).
The live transport takes its timeout and its throttle retries from FMP's row in
``providers.policy``; a throttle answer still standing after them fails open like any error.

The HTTP call is an injected ``HttpJson`` transport so the wrapper is pure and
testable offline; the live transport (``live_http_json``) is wired at the data
composition root. ``requests`` is a core dependency, but it is imported lazily
inside ``live_http_json`` so the pure wrapper carries no network import.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from time import sleep as _sleep
from typing import TYPE_CHECKING, Any

import pandas as pd

from facdrone_data.credentials import redact
from facdrone_data.providers.policy import POLICY, THROTTLE_STATUSES, Outcome, with_retries
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from requests import Response, Session

    from facdrone_data.providers.policy import ProviderPolicy
    from facdrone_data_types.ports import FetchRequest

FMP_BASE = "https://financialmodelingprep.com/stable"

log = logging.getLogger(__name__)

# (url, query params) -> parsed JSON (list/dict/str). May raise; the wrapper
# catches and fails open.
HttpJson = Callable[[str, Mapping[str, str]], Any]


def fmp_get(
    transport: HttpJson,
    endpoint: str,
    params: Mapping[str, str] | None = None,
    *,
    api_key: str,
    base: str = FMP_BASE,
) -> list[dict[str, Any]]:
    """GET an FMP endpoint, returning a list of records or ``[]`` on any error.

    A list payload is returned as-is; a dict carrying an
    ``Error`` or a bare string is logged and dropped; a plain dict is wrapped in a
    one-element list; anything else (or an exception) yields ``[]`` (fail-open)."""
    query = {"apikey": api_key, **(params or {})}
    try:
        data = transport(f"{base}{endpoint}", query)
    except Exception as exc:  # noqa: BLE001 — fail-open is the contract (R2.4)
        # Transport errors carry the request URL, key included: redact before logging (R4.3).
        log.warning("FMP request failed %s: %s", endpoint, redact(str(exc), (api_key,)))
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "Error" in str(data):
        log.warning("FMP error %s: %s", endpoint, data)
        return []
    if isinstance(data, str):
        log.warning("FMP returned string %s: %s", endpoint, data[:100])
        return []
    return [data] if isinstance(data, dict) else []


# Calendar days per intraday request so that bars stay below FMP's ~3,000-bar cap:
# 15-minute bars run 92 per full Globex day, so 25 days is ~2,300 bars; the others scale.
_INTRADAY_WINDOW_DAYS: dict[str, int] = {
    "1min": 1,
    "5min": 8,
    "15min": 25,
    "30min": 50,
    "1hour": 100,
}


def chunked_date_ranges(start: date, end: date, *, days: int) -> list[tuple[date, date]]:
    """Split ``[start, end]`` into ``days``-wide inclusive windows (used for the
    FMP 90-day news and economic-calendar chunking)."""
    if start > end:
        return []
    windows: list[tuple[date, date]] = []
    chunk = start
    while chunk <= end:
        chunk_end = min(chunk + timedelta(days=days), end)
        windows.append((chunk, chunk_end))
        chunk = chunk_end + timedelta(days=1)
    return windows


def live_http_json(
    *,
    policy: ProviderPolicy = POLICY[Provider.FMP],
    sleep: Callable[[float], None] = _sleep,
) -> HttpJson:
    """The live transport, with one session created on its first request. A throttle answer
    (429/503) is retried on the row's schedule; a network fault raises at once."""
    session: Session | None = None

    def _get(url: str, params: Mapping[str, str]) -> Any:
        nonlocal session
        if session is None:
            import requests  # lazy: the pure wrapper carries no network import

            session = requests.Session()
        opened = session

        def _attempt() -> tuple[Response, Outcome]:
            response = opened.get(url, params=params, timeout=policy.timeout_seconds)
            return response, "throttle" if response.status_code in THROTTLE_STATUSES else "done"

        response = with_retries(_attempt, policy, sleep)
        response.raise_for_status()
        return response.json()

    return _get


@dataclass(frozen=True)
class FmpPriceClient:
    """``MarketDataClient`` for the FMP price endpoints (daily / intraday / ETF).

    Returns the raw FMP records as a DataFrame; the per-family normalizer shapes
    them. A non-price FMP dataset returns empty → the refresher fails open and
    skips it (the full per-endpoint transport layer is wired the same way)."""

    transport: HttpJson
    api_key: str

    @property
    def provider(self) -> Provider:
        return Provider.FMP

    def fetch(self, req: FetchRequest) -> pd.DataFrame:
        params = {
            "symbol": req.provider_symbol,
            "from": req.start.isoformat(),
            "to": req.end.isoformat(),
        }
        if req.dataset == "intraday_prices":
            # FMP returns at most ~3,000 bars per intraday request, the most recent ones: a
            # multi-year request for a new series lands only its last weeks (measured
            # 2026-09-24: ESUSD 15min 2024-01-01..2026-09-23 returned 2026-08-10 onward). So
            # the range is split into windows that stay below the cap at the bar interval.
            frames = []
            for start, end in chunked_date_ranges(
                req.start, req.end, days=_INTRADAY_WINDOW_DAYS[req.interval]
            ):
                window = {**params, "from": start.isoformat(), "to": end.isoformat()}
                frames.append(
                    pd.DataFrame(
                        fmp_get(
                            self.transport,
                            f"/historical-chart/{req.interval}",
                            window,
                            api_key=self.api_key,
                        )
                    )
                )
            nonempty = [frame for frame in frames if not frame.empty]
            if not nonempty:
                return pd.DataFrame()
            joined = pd.concat(nonempty, ignore_index=True).drop_duplicates(subset=["date"])
            return joined.rename(columns={"date": "datetime"})
        if req.dataset in ("daily_prices", "etf_prices"):
            records = fmp_get(
                self.transport, "/historical-price-eod/full", params, api_key=self.api_key
            )
            return pd.DataFrame(records)
        return pd.DataFrame()  # not a price dataset → fail-open skip

    def datasets(self) -> tuple[str, ...]:
        return ("daily_prices", "intraday_prices", "etf_prices")
