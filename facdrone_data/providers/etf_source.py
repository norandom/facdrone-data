# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""ETF prices + meta transport (R2.1, R2.3).

This module is the LIVE fetch layer for the two ETF-adjacent families served by
``Provider.ETF`` (which uses the FMP API key):

* ``etf_prices`` → :class:`EtfDailyNormalizer`. Fetched like the FMP daily price
  family but written to ``etf_prices``, and it treats inputs as NATIVE
  FMP/exchange tickers — ``req.provider_symbol`` IS the listed ETF ticker and is
  NEVER re-keyed to a Yahoo symbol. ``fetch`` calls the FMP
  ``/historical-price-eod/full`` endpoint (via the shared :func:`fmp_get`
  fail-open wrapper) and returns the raw OHLCV records as a frame carrying
  exactly the ``symbol``/``date``/``open``/``high``/``low``/``close``/``volume``
  columns the normalizer reads. The native ticker is stamped into the ``symbol``
  column so the frame normalizes whether or not a scope ``CanonicalSymbol`` is
  later supplied.

* ``etf_meta`` → :class:`EtfMetaNormalizer`. Syncs the STATIC ``ETF_UNIVERSE``
  into ``etf_meta``. There is no network call: the universe is the source of truth.
  ``fetch`` returns the static universe rows the normalizer reads — the columns
  ``(symbol, name, wkn, isin, category, notes)`` — filtered to the requested
  native ticker so each per-symbol refresher scope yields its own meta row. The
  universe is imported from the normalizer module (the single source of
  truth) so the two can never drift.

The price network call is the injected FMP :data:`HttpJson` transport, reused
from :mod:`facdrone_data.providers.fmp_endpoints` (the ``FmpPriceClient``
reference pattern), so the adapter is pure and offline-testable. The live
``requests``-based transport (``live_http_json`` in that module) is wired only at
the data composition root. The API key is held privately and never logged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from facdrone_data.acquisition.families.normalizers.oecd_etf import _ETF_META_COLUMNS, _ETF_UNIVERSE
from facdrone_data.providers.fmp_endpoints import HttpJson, fmp_get
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from facdrone_data_types.ports import FetchRequest

# ETF price endpoint: FMP daily EOD OHLCV.
_ETF_EOD_ENDPOINT = "/historical-price-eod/full"

# The raw OHLCV columns ``EtfDailyNormalizer`` reads (plus the native-ticker
# ``symbol`` it falls back to when no scope symbol is supplied).
_ETF_PRICE_COLUMNS = ("symbol", "date", "open", "high", "low", "close", "volume")


def live_etf() -> HttpJson:
    """The live FMP HTTP transport for the ETF feed (wired at the composition root).

    ETF prices are an FMP endpoint, so the live transport is the shared
    ``requests``-based :func:`live_http_json` — re-exported here as the ETF
    factory so the composition root has one ``live_<x>()`` per provider module.
    ``requests`` is imported lazily inside that factory, so this module imports
    cleanly with no network import at top level."""
    from facdrone_data.providers.fmp_endpoints import live_http_json

    return live_http_json()


@dataclass(frozen=True)
class EtfClient:
    """``MarketDataClient`` for the ETF ``etf_prices`` + ``etf_meta`` families.

    Prices come from FMP over the injected :data:`HttpJson` transport (the native
    ETF ticker is the FMP symbol, never re-keyed). Meta is the static
    ``ETF_UNIVERSE`` — no network — returned as the raw rows the normalizer reads.
    Any other dataset returns empty → the refresher fails open and skips it."""

    transport: HttpJson
    api_key: str

    @property
    def provider(self) -> Provider:
        return Provider.ETF

    def fetch(self, req: FetchRequest) -> pd.DataFrame:
        if req.dataset == "etf_prices":
            return self._fetch_prices(req)
        if req.dataset == "etf_meta":
            return self._fetch_meta(req)
        return pd.DataFrame()  # not an ETF dataset → fail-open skip

    def _fetch_prices(self, req: FetchRequest) -> pd.DataFrame:
        """FMP daily EOD OHLCV for one NATIVE ETF ticker → the raw price frame.

        ``req.provider_symbol`` is the listed ETF ticker (no YF remapping). The
        native ticker is stamped into ``symbol`` so the frame carries the column
        the normalizer falls back to when no scope symbol is supplied. An empty /
        failed payload yields the empty raw shape (fail-open)."""
        params = {
            "symbol": req.provider_symbol,
            "from": req.start.isoformat(),
            "to": req.end.isoformat(),
        }
        records = fmp_get(self.transport, _ETF_EOD_ENDPOINT, params, api_key=self.api_key)
        if not records:
            return pd.DataFrame(columns=list(_ETF_PRICE_COLUMNS))
        frame = pd.DataFrame(records)
        frame["symbol"] = req.provider_symbol  # native ETF ticker (NOT re-keyed to YF)
        return frame

    def _fetch_meta(self, req: FetchRequest) -> pd.DataFrame:
        """The static ``ETF_UNIVERSE`` row(s) for one native ticker → the raw meta
        frame (no network; the frame is built from the universe). Filtered to
        ``req.provider_symbol`` so each per-symbol scope yields its own row; an
        unknown ticker yields the empty raw shape."""
        rows = [row for row in _ETF_UNIVERSE if row[0] == req.provider_symbol]
        return pd.DataFrame(rows, columns=list(_ETF_META_COLUMNS))

    def datasets(self) -> tuple[str, ...]:
        return ("etf_prices", "etf_meta")
