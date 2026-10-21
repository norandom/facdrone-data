# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""FMP macro + news transports: six symbol-less/ETF macro families + stock news.

One ``MarketDataClient`` (``FmpMacroClient``) over the injected ``HttpJson``
transport (the same wrapper ``FmpPriceClient`` uses), serving six FMP datasets,
each shaped to exactly the raw columns its matching normalizer reads:

* ``fmp_economic_calendar`` — ``/economic-calendar`` over ``from``/``to`` in 90-day
  chunks (``chunked_date_ranges``). ``EconomicCalendarNormalizer`` reads the raw
  ``date`` (full datetime string it later splits into ``date``/``time``),
  ``country``, ``event``, ``actual``, ``estimate``, ``previous``, ``impact``
  verbatim.
* ``fmp_economic_indicators`` — ``/economic-indicators?name=<NAME>`` over
  ``from``/``to``. The indicator NAME is ``req.provider_symbol``;
  ``EconomicIndicatorNormalizer`` reads ``raw["name"]``, so ``fetch`` stamps the
  requested name onto every record.
* ``fmp_treasury_rates`` — ``/treasury-rates`` over ``from``/``to``.
  ``TreasuryRatesNormalizer`` reads ``date`` + the 12 maturity fields verbatim.
* ``fmp_sector_performance`` — ``/sector-performance-snapshot``. The endpoint is
  called per trading day with a ``date`` param, and that loop ``date`` is stamped
  onto each record (the snapshot rows carry ``sector``/``averageChange`` but not
  the date): the adapter fetches per weekday and stamps the loop date so
  ``SectorPerformanceNormalizer`` reads ``date``, ``sector`` and ``averageChange``.
* ``fmp_etf_holdings`` — ``/etf/holdings?symbol=&date=`` (the current snapshot).
  The snapshot date is stamped on each row; ``EtfHoldingsNormalizer`` reads
  ``date``, ``asset``/``name``, ``sharesNumber``/``shares``, ``weightPercentage``,
  ``marketValue`` and keys on the injected canonical ``symbol``.
  ``provider_symbol`` is the FMP ETF symbol.
* ``fmp_news`` — ``/news/stock`` over ``symbols``/``from``/``to`` in 90-day chunks.
  ``NewsNormalizer`` reads ``publishedDate``, ``title`` and ``publisher``/``site``
  verbatim.

The HTTP call is the injected ``HttpJson`` transport (offline-testable) and goes
through the shared ``fmp_get`` wrapper, which fails open to ``[]`` so one bad
family never aborts the download. ``fetch`` returns RAW FMP records as a
DataFrame; the per-family normalizer shapes them. A non-macro dataset returns
empty so the refresher fails open and skips it. Never logs the api key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pandas as pd

from facdrone_data.providers.fmp_endpoints import HttpJson, chunked_date_ranges, fmp_get
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from facdrone_data_types.ports import FetchRequest

# The economic calendar and stock news are requested in 90-day windows
# (``chunked_date_ranges``).
_CHUNK_DAYS = 90
# Each news window is capped at 200 headlines (``"limit": 200``).
_NEWS_LIMIT = 200


@dataclass(frozen=True)
class FmpMacroClient:
    """``MarketDataClient`` for the FMP macro + news endpoints.

    Serves ``fmp_economic_calendar``, ``fmp_economic_indicators``,
    ``fmp_treasury_rates``, ``fmp_sector_performance``, ``fmp_etf_holdings`` and
    ``fmp_news``; any other dataset returns an empty frame so the refresher fails
    open and skips it. Returns RAW FMP records as a DataFrame whose columns are
    exactly what the matching normalizer reads."""

    transport: HttpJson
    api_key: str

    @property
    def provider(self) -> Provider:
        return Provider.FMP

    def fetch(self, req: FetchRequest) -> pd.DataFrame:
        if req.dataset == "fmp_economic_calendar":
            return self._fetch_economic_calendar(req)
        if req.dataset == "fmp_economic_indicators":
            return self._fetch_economic_indicator(req)
        if req.dataset == "fmp_treasury_rates":
            return self._fetch_treasury_rates(req)
        if req.dataset == "fmp_sector_performance":
            return self._fetch_sector_performance(req)
        if req.dataset == "fmp_etf_holdings":
            return self._fetch_etf_holdings(req)
        if req.dataset == "fmp_news":
            return self._fetch_news(req)
        return pd.DataFrame()  # not a macro/news dataset → fail-open skip

    # ── economic calendar (90-day chunks) ────────────────────────────────────
    def _fetch_economic_calendar(self, req: FetchRequest) -> pd.DataFrame:
        records: list[dict[str, Any]] = []
        for chunk_start, chunk_end in chunked_date_ranges(req.start, req.end, days=_CHUNK_DAYS):
            records.extend(
                fmp_get(
                    self.transport,
                    "/economic-calendar",
                    {"from": chunk_start.isoformat(), "to": chunk_end.isoformat()},
                    api_key=self.api_key,
                )
            )
        return pd.DataFrame(records)

    # ── one named economic-indicator series ──────────────────────────────────
    def _fetch_economic_indicator(self, req: FetchRequest) -> pd.DataFrame:
        # ``req.provider_symbol`` is the indicator NAME (the family scope); the
        # normalizer reads ``raw["name"]``, so stamp it on every record.
        records = fmp_get(
            self.transport,
            "/economic-indicators",
            {
                "name": req.provider_symbol,
                "from": req.start.isoformat(),
                "to": req.end.isoformat(),
            },
            api_key=self.api_key,
        )
        frame = pd.DataFrame(records)
        frame["name"] = req.provider_symbol
        return frame

    # ── treasury yield curve ─────────────────────────────────────────────────
    def _fetch_treasury_rates(self, req: FetchRequest) -> pd.DataFrame:
        records = fmp_get(
            self.transport,
            "/treasury-rates",
            {"from": req.start.isoformat(), "to": req.end.isoformat()},
            api_key=self.api_key,
        )
        return pd.DataFrame(records)

    # ── sector performance (per trading-day snapshot, date stamped) ──────────
    def _fetch_sector_performance(self, req: FetchRequest) -> pd.DataFrame:
        # Calls ``/sector-performance-snapshot`` once per weekday with a ``date``
        # param and stamps that loop date onto each record (the snapshot rows carry
        # ``sector``/``averageChange`` but not the date the normalizer reads).
        frames: list[pd.DataFrame] = []
        day = req.start
        while day <= req.end:
            if day.weekday() >= 5:  # skip weekends
                day += timedelta(days=1)
                continue
            records = fmp_get(
                self.transport,
                "/sector-performance-snapshot",
                {"date": day.isoformat()},
                api_key=self.api_key,
            )
            if records:
                frame = pd.DataFrame(records)
                frame["date"] = day.isoformat()  # stamp the snapshot date
                frames.append(frame)
            day += timedelta(days=1)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    # ── ETF holdings (current snapshot, today's date stamped) ────────────────
    def _fetch_etf_holdings(self, req: FetchRequest) -> pd.DataFrame:
        # Fetches the *current* holdings snapshot and stamps its date on each
        # row; the bounded window's end is the snapshot date this adapter requests.
        snapshot = req.end.isoformat()
        records = fmp_get(
            self.transport,
            "/etf/holdings",
            {"symbol": req.provider_symbol, "date": snapshot},
            api_key=self.api_key,
        )
        frame = pd.DataFrame(records)
        frame["date"] = snapshot  # stamp the snapshot date
        return frame

    # ── stock news (90-day chunks) ───────────────────────────────────────────
    def _fetch_news(self, req: FetchRequest) -> pd.DataFrame:
        records: list[dict[str, Any]] = []
        for chunk_start, chunk_end in chunked_date_ranges(req.start, req.end, days=_CHUNK_DAYS):
            records.extend(
                fmp_get(
                    self.transport,
                    "/news/stock",
                    {
                        "symbols": req.provider_symbol,
                        "from": chunk_start.isoformat(),
                        "to": chunk_end.isoformat(),
                        "limit": str(_NEWS_LIMIT),
                    },
                    api_key=self.api_key,
                )
            )
        return pd.DataFrame(records)

    def datasets(self) -> tuple[str, ...]:
        return (
            "fmp_economic_calendar",
            "fmp_economic_indicators",
            "fmp_treasury_rates",
            "fmp_sector_performance",
            "fmp_etf_holdings",
            "fmp_news",
        )


def live_fmp_macro(api_key: str) -> FmpMacroClient:
    """The live client over the FMP transport (``requests`` loads inside ``live_http_json``)."""
    from facdrone_data.providers.fmp_endpoints import live_http_json

    return FmpMacroClient(transport=live_http_json(), api_key=api_key)
