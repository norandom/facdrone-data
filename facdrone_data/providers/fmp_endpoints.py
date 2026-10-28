# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""FMP request wrapper + per-endpoint fetchers (R2.2, R2.4).

``fmp_records`` prepends the ``apikey`` param, normalizes list/dict/string/error payloads,
and tells an answer from a failure: FMP's records (``[]`` when it answered with none), or
``FmpRequestError`` for a transport or HTTP fault and an error payload. ``fmp_get``, which
every family client calls, **fails open** over it: any error returns ``[]`` so one bad
family never aborts the download (R2.4). The price client reads ``fmp_records`` so the
refresher can tell "no rows for the window" (``ProviderAnsweredEmpty``) from an outage.
The live transport takes its timeout and its throttle retries from FMP's row in
``providers.policy``; a throttle answer still standing after them is an error like any other.

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
from facdrone_data_types.ports import ProviderAnsweredEmpty
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from requests import Response, Session

    from facdrone_data.providers.policy import ProviderPolicy
    from facdrone_data_types.ports import FetchRequest

FMP_BASE = "https://financialmodelingprep.com/stable"

log = logging.getLogger(__name__)

# (url, query params) -> parsed JSON (list/dict/str). May raise; ``fmp_records`` turns
# the fault into ``FmpRequestError`` and ``fmp_get`` fails open on it.
HttpJson = Callable[[str, Mapping[str, str]], Any]


class FmpRequestError(Exception):
    """FMP did not answer: a transport or HTTP fault, or an error payload. The message
    is redacted (R4.3) and carries no chained exception, whose text names the key."""


def _failed(message: str) -> FmpRequestError:
    """The failure, logged once where it happened (the callers fail open silently)."""
    log.warning("%s", message)
    return FmpRequestError(message)


def fmp_records(
    transport: HttpJson,
    endpoint: str,
    params: Mapping[str, str] | None = None,
    *,
    api_key: str,
    base: str = FMP_BASE,
) -> list[dict[str, Any]]:
    """GET an FMP endpoint: its records, ``[]`` when FMP answered with none, or
    ``FmpRequestError`` when it did not answer.

    A list payload is returned as-is; a plain dict is wrapped in a one-element list; a
    dict carrying an ``Error`` or a bare string is an error; anything else yields ``[]``."""
    query = {"apikey": api_key, **(params or {})}
    try:
        data = transport(f"{base}{endpoint}", query)
    except Exception as exc:  # noqa: BLE001 — every transport fault becomes the typed error
        # Transport errors carry the request URL, key included: redact before raising (R4.3).
        raise _failed(f"FMP request failed {endpoint}: {redact(str(exc), (api_key,))}") from None
    if isinstance(data, dict) and "Error" in str(data):
        raise _failed(f"FMP error {endpoint}: {data}")
    if isinstance(data, str):
        raise _failed(f"FMP returned string {endpoint}: {data[:100]}")
    if isinstance(data, list):
        return data
    return [data] if isinstance(data, dict) else []


def fmp_get(
    transport: HttpJson,
    endpoint: str,
    params: Mapping[str, str] | None = None,
    *,
    api_key: str,
    base: str = FMP_BASE,
) -> list[dict[str, Any]]:
    """``fmp_records``, failing open: a failed request is logged and yields ``[]`` (R2.4)."""
    try:
        return fmp_records(transport, endpoint, params, api_key=api_key, base=base)
    except FmpRequestError:
        return []


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
    them. When no rows came back it says why: ``FmpRequestError`` when a request
    failed, ``ProviderAnsweredEmpty`` when FMP answered every request with none. A
    non-price FMP dataset returns empty → the refresher fails open and skips it (the
    full per-endpoint transport layer is wired the same way)."""

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
            # A failed window does not discard the others' bars (R2.4).
            windows = chunked_date_ranges(
                req.start, req.end, days=_INTRADAY_WINDOW_DAYS[req.interval]
            )
            records, error = self._windows(f"/historical-chart/{req.interval}", params, windows)
            joined = _answered(records, error).drop_duplicates(subset=["date"])
            return joined.rename(columns={"date": "datetime"})
        if req.dataset in ("daily_prices", "etf_prices"):
            records = fmp_records(
                self.transport, "/historical-price-eod/full", params, api_key=self.api_key
            )
            return _answered(records, None)
        return pd.DataFrame()  # not a price dataset → fail-open skip

    def _windows(
        self, endpoint: str, params: Mapping[str, str], windows: list[tuple[date, date]]
    ) -> tuple[list[dict[str, Any]], FmpRequestError | None]:
        """Every window's records, and the last failure among them (logged where it
        happened, not raised here)."""
        records: list[dict[str, Any]] = []
        error: FmpRequestError | None = None
        for start, end in windows:
            window = {**params, "from": start.isoformat(), "to": end.isoformat()}
            try:
                records += fmp_records(self.transport, endpoint, window, api_key=self.api_key)
            except FmpRequestError as failed:
                error = failed
        return records, error

    def datasets(self) -> tuple[str, ...]:
        return ("daily_prices", "intraday_prices", "etf_prices")


def _answered(records: list[dict[str, Any]], error: FmpRequestError | None) -> pd.DataFrame:
    """The records as a frame; with none, the failure if a request failed, else the answer
    that FMP has no rows for the window."""
    if records:
        return pd.DataFrame(records)
    if error is not None:
        raise error
    raise ProviderAnsweredEmpty("FMP answered with no rows for the window")
