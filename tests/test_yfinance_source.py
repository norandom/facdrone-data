# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""yfinance daily-price fallback transport (R4.2, R4.4; D8).

Proves payload -> fetch -> normalize end-to-end with NO network: a RECORDED native
yfinance frame is fed through an INJECTED transport, ``YFinanceDailyClient.fetch``
shapes it to the RAW columns ``DailyPricesNormalizer`` reads, and the REAL
normalizer is then run over that frame and asserted to yield exactly the
``daily_prices`` table columns.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from facdrone_data.acquisition.families.normalizers.prices import DailyPricesNormalizer
from facdrone_data.acquisition.schema import DAILY_PRICES
from facdrone_data.providers.yfinance_source import (
    YfDownload,
    YFinanceDailyClient,
    live_yfinance,
)
from facdrone_data_types.enums import AssetClass
from facdrone_data_types.ports import FetchRequest, MarketDataClient
from facdrone_data_types.symbols import CanonicalSymbol, Provider

_WHEAT = CanonicalSymbol(canonical_id="ZW=F", asset_class=AssetClass.AGRICULTURE, profit_ccy="USD")
_REQ = FetchRequest(
    provider_symbol="ZW=F", dataset="daily_prices", start=date(2024, 1, 1), end=date(2024, 1, 3)
)
_TABLE_COLUMNS = [c.name for c in DAILY_PRICES.columns]


def _native_yf_frame() -> pd.DataFrame:
    """A recorded native yfinance daily frame: capitalized OHLCV columns on a
    ``DatetimeIndex`` named ``Date`` (the shape ``yf.download`` returns)."""
    idx = pd.DatetimeIndex(pd.to_datetime(["2024-01-01", "2024-01-02"]), name="Date")
    return pd.DataFrame(
        {
            "Open": [600.0, 602.5],
            "High": [605.0, 606.0],
            "Low": [598.0, 600.0],
            "Close": [603.0, 604.5],
            "Adj Close": [603.0, 604.5],
            "Volume": [12000, 13500],
        },
        index=idx,
    )


def _fixed_transport(native: pd.DataFrame) -> YfDownload:
    def _t(symbol: str, start: date, end: date) -> pd.DataFrame:
        del symbol, start, end
        return native

    return _t


def test_client_conforms_to_the_market_data_client_port() -> None:
    client = YFinanceDailyClient(transport=_fixed_transport(_native_yf_frame()))
    assert isinstance(client, MarketDataClient)
    assert client.provider is Provider.YFINANCE
    assert client.datasets() == ("daily_prices",)


def test_fetch_returns_the_raw_lowercase_columns_the_normalizer_reads() -> None:
    client = YFinanceDailyClient(transport=_fixed_transport(_native_yf_frame()))
    raw = client.fetch(_REQ)
    # Exactly the lowercase fields DailyPricesNormalizer consumes.
    assert set(raw.columns) >= {"date", "open", "high", "low", "close", "volume"}
    assert "Adj Close" not in raw.columns and "adj close" not in raw.columns
    assert len(raw) == 2


def test_recorded_payload_maps_through_the_real_normalizer_to_daily_prices() -> None:
    """payload -> fetch -> REAL normalize -> exactly the daily_prices columns."""
    client = YFinanceDailyClient(transport=_fixed_transport(_native_yf_frame()))
    raw = client.fetch(_REQ)

    out = DailyPricesNormalizer().normalize(raw, symbol=_WHEAT, as_of=datetime(2024, 1, 4, 12, 0))

    assert list(out.columns) == _TABLE_COLUMNS
    assert len(out) == 2  # both bars survive (no null close)
    assert list(out["symbol"]) == ["ZW=F", "ZW=F"]  # re-keyed to the canonical (R4.3)
    assert list(out["date"]) == [date(2024, 1, 1), date(2024, 1, 2)]
    assert out["close"].to_list() == [603.0, 604.5]
    assert out["volume"].to_list() == [12000, 13500]
    assert str(out["volume"].dtype) == "int64"  # _int_volume coercion


def test_null_close_bar_is_dropped_by_the_normalizer() -> None:
    native = _native_yf_frame()
    native.loc[native.index[1], "Close"] = None  # a partial/incomplete bar
    client = YFinanceDailyClient(transport=_fixed_transport(native))

    out = DailyPricesNormalizer().normalize(
        client.fetch(_REQ), symbol=_WHEAT, as_of=datetime(2024, 1, 4, 12, 0)
    )

    assert list(out.columns) == _TABLE_COLUMNS
    assert len(out) == 1  # the null-close row dropped (R6.2)
    assert list(out["date"]) == [date(2024, 1, 1)]


def test_zero_close_bar_is_dropped_like_a_null_one() -> None:
    """2026-09-07: the dollar index's Sunday stub session arrived with open and high
    filled and low and close at exactly zero. Zero is not null, so it passed the null
    filter, and the next day's return from it was infinite; that reached the consumer and
    the daemon answered 500 on every read request for an hour. A close of zero is not
    a price, it is a bar the vendor had not finished."""
    native = _native_yf_frame()
    native.loc[native.index[1], "Close"] = 0.0
    native.loc[native.index[1], "Low"] = 0.0
    client = YFinanceDailyClient(transport=_fixed_transport(native))

    out = DailyPricesNormalizer().normalize(
        client.fetch(_REQ), symbol=_WHEAT, as_of=datetime(2024, 1, 4, 12, 0)
    )

    assert len(out) == 1
    assert list(out["date"]) == [date(2024, 1, 1)]
    assert (out["close"] != 0).all()


def test_a_negative_close_is_kept_because_it_has_happened() -> None:
    """WTI crude settled at -37.63 on 2020-04-20 and that row is in the panel as the
    real price it was. The stub rule is exactly zero, not zero or less: a rule wide
    enough to drop that day would be altering the record to fit an assumption."""
    native = _native_yf_frame()
    native.loc[native.index[1], "Close"] = -37.63
    client = YFinanceDailyClient(transport=_fixed_transport(native))

    out = DailyPricesNormalizer().normalize(
        client.fetch(_REQ), symbol=_WHEAT, as_of=datetime(2024, 1, 4, 12, 0)
    )

    assert len(out) == 2
    assert out["close"].to_list()[1] == pytest.approx(-37.63)


def test_non_daily_dataset_returns_empty_fail_open() -> None:
    client = YFinanceDailyClient(transport=_fixed_transport(_native_yf_frame()))
    other = FetchRequest(
        provider_symbol="ZW=F",
        dataset="intraday_prices",
        start=date(2024, 1, 1),
        end=date(2024, 1, 3),
    )
    assert client.fetch(other).empty


def test_fetch_fails_open_on_transport_error() -> None:
    def _boom(symbol: str, start: date, end: date) -> pd.DataFrame:
        del symbol, start, end
        raise ConnectionError("network down")

    client = YFinanceDailyClient(transport=_boom)
    assert client.fetch(_REQ).empty  # no raise — fail-open per symbol


def test_empty_native_frame_yields_an_empty_fetch() -> None:
    client = YFinanceDailyClient(transport=_fixed_transport(pd.DataFrame()))
    assert client.fetch(_REQ).empty


def test_live_yfinance_factory_imports_without_the_sdk_installed() -> None:
    """The factory must BUILD without yfinance installed (the import is deferred into
    the returned transport); only CALLING the transport imports the SDK and then
    fails with ImportError if it is absent."""
    import importlib.util
    from datetime import date

    transport = live_yfinance()  # must NOT raise even when yfinance is absent
    assert callable(transport)
    if importlib.util.find_spec("yfinance") is None:
        import pytest

        with pytest.raises(ImportError):
            transport("EURUSD=X", date(2025, 1, 1), date(2025, 1, 2))
