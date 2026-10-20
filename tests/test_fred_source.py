# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""FRED series transport (R2.1, R2.3).

Proves payload -> fetch -> normalize end-to-end with NO network: a RECORDED FRED
``pd.Series`` is fed through an INJECTED transport, ``FredSeriesClient.fetch`` shapes
it to the RAW ``series_id/date/value`` columns ``FredSeriesNormalizer`` reads, and
the REAL normalizer is then run over that frame and asserted to yield exactly the
``fred_series`` table columns.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from facdrone_data.acquisition.families.normalizers.fred import FredSeriesNormalizer
from facdrone_data.acquisition.schema import FRED_SERIES
from facdrone_data.providers.fred_source import (
    FredGetSeries,
    FredSeriesClient,
    live_fred,
)
from facdrone_data_types.ports import FetchRequest, MarketDataClient
from facdrone_data_types.symbols import Provider

_SERIES_ID = "VIXCLS"
_REQ = FetchRequest(
    provider_symbol=_SERIES_ID,
    dataset="fred_series",
    start=date(2024, 1, 1),
    end=date(2024, 1, 4),
)
_TABLE_COLUMNS = [c.name for c in FRED_SERIES.columns]


def _recorded_series() -> pd.Series[float]:
    """A recorded FRED daily series: float observations on a ``DatetimeIndex`` (the
    shape ``Fred.get_series`` returns), including a NaN gap (a market holiday) the
    normalizer must drop."""
    idx = pd.DatetimeIndex(pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-03"]))
    return pd.Series([13.2, float("nan"), 12.8], index=idx)


def _fixed_transport(series: pd.Series[float]) -> FredGetSeries:
    def _t(series_id: str, start: date, end: date) -> pd.Series[float]:
        del series_id, start, end
        return series

    return _t


def test_client_conforms_to_the_market_data_client_port() -> None:
    client = FredSeriesClient(transport=_fixed_transport(_recorded_series()))
    assert isinstance(client, MarketDataClient)
    assert client.provider is Provider.FRED
    assert client.datasets() == ("fred_series",)


def test_fetch_returns_the_raw_columns_the_normalizer_reads() -> None:
    client = FredSeriesClient(transport=_fixed_transport(_recorded_series()))
    raw = client.fetch(_REQ)
    # Exactly the raw fields FredSeriesNormalizer consumes.
    assert set(raw.columns) == {"series_id", "date", "value"}
    assert list(raw["series_id"]) == [_SERIES_ID] * 3  # repeated from provider_symbol
    assert len(raw) == 3  # raw is unfiltered; the normalizer drops the NaN row


def test_recorded_payload_maps_through_the_real_normalizer_to_fred_series() -> None:
    """payload -> fetch -> REAL normalize -> exactly the fred_series columns."""
    client = FredSeriesClient(transport=_fixed_transport(_recorded_series()))
    raw = client.fetch(_REQ)

    out = FredSeriesNormalizer().normalize(raw, symbol=None, as_of=datetime(2024, 1, 5, 12, 0))

    assert list(out.columns) == _TABLE_COLUMNS
    assert len(out) == 2  # the NaN observation dropped (``s.dropna()``)
    assert list(out["series_id"]) == [_SERIES_ID, _SERIES_ID]  # series-keyed
    assert list(out["date"]) == [date(2024, 1, 1), date(2024, 1, 3)]  # coerced to date
    assert out["value"].to_list() == [13.2, 12.8]


def test_non_fred_dataset_returns_empty_fail_open() -> None:
    client = FredSeriesClient(transport=_fixed_transport(_recorded_series()))
    other = FetchRequest(
        provider_symbol=_SERIES_ID,
        dataset="daily_prices",
        start=date(2024, 1, 1),
        end=date(2024, 1, 4),
    )
    assert client.fetch(other).empty


def test_fetch_fails_open_on_transport_error() -> None:
    def _boom(series_id: str, start: date, end: date) -> pd.Series[float]:
        del series_id, start, end
        raise ConnectionError("network down")

    client = FredSeriesClient(transport=_boom)
    assert client.fetch(_REQ).empty  # no raise — fail-open per series


def test_empty_series_yields_an_empty_fetch() -> None:
    empty = pd.Series(pd.array([], dtype="float64"))
    client = FredSeriesClient(transport=_fixed_transport(empty))
    raw = client.fetch(_REQ)
    assert raw.empty
    # The empty raw still threads through the normalizer to the table columns.
    out = FredSeriesNormalizer().normalize(raw, symbol=None, as_of=datetime(2024, 1, 5, 12, 0))
    assert list(out.columns) == _TABLE_COLUMNS
    assert out.empty


def test_live_fred_factory_imports_without_the_sdk_installed() -> None:
    """The factory must BUILD without fredapi installed (the import is deferred into
    the returned transport); only CALLING the transport imports the SDK and then
    fails with ImportError if it is absent."""
    import importlib.util
    from datetime import date

    transport = live_fred("fake-key")  # must NOT raise even when fredapi is absent
    assert callable(transport)
    if importlib.util.find_spec("fredapi") is None:
        import pytest

        with pytest.raises(ImportError):
            transport("VIXCLS", date(2025, 1, 1), date(2025, 1, 2))
