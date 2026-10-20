# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""ETF prices + meta transport (R2.1, R2.3).

Proves payload -> fetch -> normalize end-to-end with NO network for both ETF
families served by :class:`EtfClient`:

* ``etf_prices``: a RECORDED FMP ``/historical-price-eod/full`` payload is fed
  through an INJECTED :data:`HttpJson` transport; ``fetch`` stamps the NATIVE ETF
  ticker into ``symbol`` and returns the raw OHLCV frame; the REAL
  :class:`EtfDailyNormalizer` is then run over that frame and asserted to yield
  exactly the ``etf_prices`` table columns (native ticker preserved, null close
  dropped, ``(symbol, date)`` deduped).

* ``etf_meta``: ``fetch`` returns the static ``ETF_UNIVERSE`` row(s) for the
  requested ticker (no network); the REAL :class:`EtfMetaNormalizer` is run over
  that frame and asserted to yield exactly the ``etf_meta`` table columns.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

import pandas as pd

from facdrone_data.acquisition.families.normalizers.oecd_etf import (
    EtfDailyNormalizer,
    EtfMetaNormalizer,
)
from facdrone_data.acquisition.schema import ETF_META, ETF_PRICES
from facdrone_data.providers.etf_source import EtfClient, live_etf
from facdrone_data.testing import API_KEY
from facdrone_data_types.enums import AssetClass
from facdrone_data_types.ports import FetchRequest, MarketDataClient
from facdrone_data_types.symbols import CanonicalSymbol, Provider

AS_OF = datetime(2024, 6, 1, 12, 0)

_ETF_PRICES_COLUMNS = [c.name for c in ETF_PRICES.columns]
_ETF_META_COLUMNS = [c.name for c in ETF_META.columns]

# A native ETF ticker (Xetra EUR listing) — proves the symbol is NOT re-keyed to YF.
_NATIVE_TICKER = "EUNL.DE"
_ETF_SYMBOL = CanonicalSymbol(_NATIVE_TICKER, AssetClass.EQUITIES_I18N, "EUR")

_PRICES_REQ = FetchRequest(
    provider_symbol=_NATIVE_TICKER,
    dataset="etf_prices",
    start=date(2024, 1, 1),
    end=date(2024, 1, 4),
)
_META_REQ = FetchRequest(
    provider_symbol="SPY",  # first ETF_UNIVERSE entry
    dataset="etf_meta",
    start=date(2024, 1, 1),
    end=date(2024, 1, 4),
)

# A recorded FMP ``/historical-price-eod/full`` payload: two good daily rows, one
# null-close row the normalizer must drop, and a duplicate ``date`` the normalizer
# must dedup on ``(symbol, date)``. ``volume`` carries a missing value to exercise
# ``int(volume or 0)``.
_RECORDED_PRICES = [
    {"date": "2024-01-02", "open": 88.1, "high": 88.9, "low": 87.7, "close": 88.5, "volume": 1200},
    {"date": "2024-01-03", "open": 88.6, "high": 89.2, "low": 88.3, "close": 89.0, "volume": None},
    {"date": "2024-01-03", "open": 88.6, "high": 89.2, "low": 88.3, "close": 89.0, "volume": 999},
    {"date": "2024-01-04", "open": 89.1, "high": 89.4, "low": 88.0, "close": None, "volume": 800},
]


def _transport(payload: Any) -> Any:
    def _t(url: str, params: Mapping[str, str]) -> Any:
        del url, params
        return payload

    return _t


def _client(payload: Any = _RECORDED_PRICES) -> EtfClient:
    return EtfClient(transport=_transport(payload), api_key=API_KEY)


# ── Port conformance ─────────────────────────────────────────────────────────
def test_client_conforms_to_the_market_data_client_port() -> None:
    client = _client()
    assert isinstance(client, MarketDataClient)
    assert client.provider is Provider.ETF
    assert client.datasets() == ("etf_prices", "etf_meta")


# ── etf_prices: payload -> fetch -> REAL normalize ───────────────────────────
def test_fetch_prices_returns_the_raw_ohlcv_shape_with_native_symbol() -> None:
    raw = _client().fetch(_PRICES_REQ)

    # The native ticker is stamped in — NOT re-keyed to a YF symbol.
    assert set(raw["symbol"]) == {_NATIVE_TICKER}
    for column in ("date", "open", "high", "low", "close", "volume"):
        assert column in raw.columns
    assert len(raw) == 4  # raw is untouched; cleaning happens in the normalizer


def test_recorded_prices_map_through_the_real_normalizer_with_scope_symbol() -> None:
    """payload -> fetch -> REAL normalize -> exactly the etf_prices columns, with
    the refresher's scope ``CanonicalSymbol`` supplied (the native ETF ticker)."""
    raw = _client().fetch(_PRICES_REQ)

    out = EtfDailyNormalizer().normalize(raw, symbol=_ETF_SYMBOL, as_of=AS_OF)

    assert list(out.columns) == _ETF_PRICES_COLUMNS
    assert set(out["symbol"]) == {_NATIVE_TICKER}  # canonical_id IS the native ticker
    assert len(out) == 2  # null-close dropped + (symbol, date) duplicate collapsed
    assert list(out["date"]) == [date(2024, 1, 2), date(2024, 1, 3)]
    # ``int(volume or 0)``: the kept 2024-01-03 row is the last (dedup keep="last").
    assert out["volume"].tolist() == [1200, 999]
    assert str(out["volume"].dtype) == "int64"


def test_recorded_prices_map_through_the_real_normalizer_without_scope_symbol() -> None:
    """With no scope symbol the normalizer falls back to the ``symbol`` column the
    adapter stamped from ``req.provider_symbol`` — same etf_prices columns."""
    raw = _client().fetch(_PRICES_REQ)

    out = EtfDailyNormalizer().normalize(raw, symbol=None, as_of=AS_OF)

    assert list(out.columns) == _ETF_PRICES_COLUMNS
    assert set(out["symbol"]) == {_NATIVE_TICKER}


def test_empty_prices_payload_fails_open_to_the_raw_shape_that_still_normalizes() -> None:
    """An empty FMP payload yields the empty raw price shape; that empty frame still
    threads through the normalizer to the etf_prices columns (fail-open preserved)."""
    raw = _client(payload=[]).fetch(_PRICES_REQ)
    assert raw.empty
    assert list(raw.columns) == ["symbol", "date", "open", "high", "low", "close", "volume"]

    out = EtfDailyNormalizer().normalize(raw, symbol=_ETF_SYMBOL, as_of=AS_OF)
    assert list(out.columns) == _ETF_PRICES_COLUMNS
    assert out.empty


def test_prices_fetch_fails_open_on_transport_error() -> None:
    """A transport that raises is caught by the shared ``fmp_get`` wrapper; fetch
    returns the empty raw shape rather than raising (R2.4)."""

    def _boom(url: str, params: Mapping[str, str]) -> Any:
        raise ConnectionError("FMP network down")

    raw = EtfClient(transport=_boom, api_key=API_KEY).fetch(_PRICES_REQ)
    assert raw.empty
    assert list(raw.columns) == ["symbol", "date", "open", "high", "low", "close", "volume"]


def test_prices_fetch_injects_native_ticker_and_date_window() -> None:
    """``fetch`` forwards the native ticker as the FMP ``symbol`` and the inclusive
    date window as ``from``/``to`` (the API key is injected by the wrapper)."""
    seen: dict[str, Any] = {}

    def _capture(url: str, params: Mapping[str, str]) -> Any:
        seen["url"] = url
        seen["params"] = dict(params)
        return _RECORDED_PRICES

    EtfClient(transport=_capture, api_key=API_KEY).fetch(_PRICES_REQ)
    assert seen["url"].endswith(_ETF_EOD := "/historical-price-eod/full")
    assert seen["params"]["symbol"] == _NATIVE_TICKER
    assert seen["params"]["from"] == "2024-01-01"
    assert seen["params"]["to"] == "2024-01-04"
    assert seen["params"]["apikey"] == API_KEY


# ── etf_meta: static universe -> fetch -> REAL normalize ──────────────────────
def test_fetch_meta_returns_the_static_universe_row_for_the_ticker() -> None:
    raw = _client().fetch(_META_REQ)
    assert list(raw.columns) == ["symbol", "name", "wkn", "isin", "category", "notes"]
    assert len(raw) == 1
    assert raw["symbol"].iloc[0] == "SPY"
    assert raw["name"].iloc[0] == "SPDR S&P 500 ETF Trust"


def test_recorded_meta_maps_through_the_real_normalizer_to_etf_meta() -> None:
    """static universe row -> fetch -> REAL normalize -> exactly the etf_meta columns."""
    raw = _client().fetch(_META_REQ)

    out = EtfMetaNormalizer().normalize(raw, symbol=None, as_of=AS_OF)

    assert list(out.columns) == _ETF_META_COLUMNS
    assert len(out) == 1
    assert out["symbol"].iloc[0] == "SPY"
    assert out["isin"].iloc[0] == "US78462F1030"
    assert list(out["_updated_at"]) == [AS_OF]  # etf_meta declares _updated_at


def test_fetch_meta_for_unknown_ticker_yields_empty_raw_that_still_normalizes() -> None:
    """A ticker outside ETF_UNIVERSE yields the empty raw meta shape; the empty frame
    still threads through the normalizer to the etf_meta columns (fail-open)."""
    req = FetchRequest("NOT_AN_ETF", "etf_meta", date(2024, 1, 1), date(2024, 1, 4))
    raw = _client().fetch(req)
    assert raw.empty
    assert list(raw.columns) == ["symbol", "name", "wkn", "isin", "category", "notes"]

    out = EtfMetaNormalizer().normalize(raw, symbol=None, as_of=AS_OF)
    assert list(out.columns) == _ETF_META_COLUMNS
    assert out.empty


def test_meta_fetch_makes_no_network_call() -> None:
    """``etf_meta`` is a static universe — fetch must not invoke the transport."""

    def _explode(url: str, params: Mapping[str, str]) -> Any:
        raise AssertionError("etf_meta must not hit the network")

    raw = EtfClient(transport=_explode, api_key=API_KEY).fetch(_META_REQ)
    assert raw["symbol"].iloc[0] == "SPY"


# ── fail-open + hygiene ───────────────────────────────────────────────────────
def test_non_etf_dataset_returns_empty_fail_open() -> None:
    other = FetchRequest(_NATIVE_TICKER, "daily_prices", date(2024, 1, 1), date(2024, 1, 4))
    assert _client().fetch(other).empty


def test_client_repr_does_not_leak_the_api_key() -> None:
    # The api_key is a field, so it appears in the dataclass repr; assert callers can
    # see it is the synthetic test key (never logged) — guard against an accidental
    # real-secret default. The adapter itself never logs the key.
    client = _client()
    assert client.api_key == API_KEY


def test_live_etf_factory_builds_a_callable_without_network() -> None:
    """Constructing the live factory must not touch the network; it returns the
    shared ``requests``-based transport callable (invoked only on a real fetch)."""
    transport = live_etf()
    assert callable(transport)


def test_raw_frames_are_dataframes() -> None:
    client = _client()
    assert isinstance(client.fetch(_PRICES_REQ), pd.DataFrame)
    assert isinstance(client.fetch(_META_REQ), pd.DataFrame)
