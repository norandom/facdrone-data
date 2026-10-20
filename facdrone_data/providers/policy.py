# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The retrieval policy of every provider, one row per ``Provider`` member (R6.4).

A row holds the request timeout, the waits before each retry by outcome class (throttle,
transient fault, other status), an optional request-count ceiling and the publication delay
the provider states. Adapters read their row instead of literals of their own;
``docs/providers.md`` prints the rows (a test holds it equal). A consumer's own publication
rules (the trading system's ``freshness:`` block) stay authoritative for its families.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from types import MappingProxyType
from typing import Literal
from zoneinfo import ZoneInfo

from facdrone_data_types.symbols import Provider

TIMEOUT_SECONDS = 30  # the HTTP read timeout most provider requests carry
NEW_YORK = ZoneInfo("America/New_York")  # the timezone US index sources stamp in
THROTTLE_STATUSES = frozenset({429, 503})
Outcome = Literal["done", "throttle", "fault", "other"]


@dataclass(frozen=True)
class Retry:
    """The wait before each retry; the count is the limit. The index is the number of the
    failed attempt, counted across outcome classes."""

    waits: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if any(wait < 0 for wait in self.waits):
            raise ValueError("a retry wait is never negative")


@dataclass(frozen=True)
class Publication:
    """``delay`` after the date a row describes; ``None`` where no fixed delay is stated."""

    delay: timedelta | None
    stated: str


@dataclass(frozen=True)
class ProviderPolicy:
    """``timeout_seconds`` is ``None`` where a client library makes the request and the module
    passes no timeout; ``requests_per_minute`` ``None`` counts no requests."""

    timeout_seconds: float | None
    publication: Publication
    throttle: Retry = Retry()
    fault: Retry = Retry()
    other: Retry = Retry()
    requests_per_minute: int | None = None


def with_retries[T](
    attempt: Callable[[], tuple[T, Outcome]], row: ProviderPolicy, sleep: Callable[[float], None]
) -> T:
    """Repeat ``attempt`` until done or its outcome's retries are spent; the last result."""
    number = 0
    while True:
        result, outcome = attempt()
        if outcome == "done":
            return result
        waits = {"throttle": row.throttle, "fault": row.fault, "other": row.other}[outcome].waits
        if number >= len(waits):
            return result
        if waits[number]:
            sleep(waits[number])
        number += 1


def fetch_url(provider: Provider, url: str, headers: Mapping[str, str] | None = None) -> bytes:
    """GET a fixed https ``url`` (a module constant, never user input) with the row's timeout."""
    import urllib.request  # lazy: the adapters carry no network import

    request = urllib.request.Request(url, headers=dict(headers or {}))  # noqa: S310
    # nosemgrep: dynamic-urllib-use-detected
    with urllib.request.urlopen(request, timeout=POLICY[provider].timeout_seconds) as response:  # noqa: S310
        payload: bytes = response.read()
    return payload


_LIBRARY = Publication(None, "monthly library updates")
# FMP Ultimate (operator 2026-10-17): its per-minute ceiling is not known, so requests are not
# counted; a throttle answer is retried instead. The ETF family is FMP's API under one key.
_FMP = ProviderPolicy(
    TIMEOUT_SECONDS,
    Publication(
        None,
        "per dataset, daily bars after the venue close, intraday bars when the bar closes, "
        "statements at their filing date",
    ),
    throttle=Retry((1.0, 2.0, 4.0)),
)
POLICY: Mapping[Provider, ProviderPolicy] = MappingProxyType(
    {
        Provider.FMP: _FMP,
        Provider.ETF: _FMP,
        Provider.YFINANCE: ProviderPolicy(None, Publication(None, "daily bars after the close")),
        Provider.FRED: ProviderPolicy(
            None, Publication(timedelta(days=1), "the next US business day, late morning Central")
        ),
        Provider.OECD: ProviderPolicy(
            TIMEOUT_SECONDS, Publication(None, "monthly releases on the OECD calendar")
        ),
        Provider.CFTC: ProviderPolicy(
            None, Publication(timedelta(days=3), "Tuesday positions released Friday 15:30 ET")
        ),
        Provider.KEN_FRENCH: ProviderPolicy(TIMEOUT_SECONDS, _LIBRARY),
        Provider.AQR: ProviderPolicy(120, _LIBRARY),  # the daily workbooks are about 30 MB
        # Per hour file; other statuses (an empty 200, 403, 500, ...) keep three attempts.
        Provider.DUKASCOPY: ProviderPolicy(
            TIMEOUT_SECONDS,
            Publication(None, "hour files after their UTC hour"),
            throttle=Retry((1.0, 2.0, 4.0)),
            fault=Retry((1.0, 2.0)),
            other=Retry((0.0, 0.0)),
        ),
    }
)
if set(POLICY) != set(Provider):
    raise RuntimeError(f"providers without a policy row: {sorted(set(Provider) - set(POLICY))}")
