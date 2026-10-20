# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""OECD Japan CPI transport (R2.1, R2.3).

Proves payload -> fetch -> normalize end-to-end with NO network: a RECORDED OECD
SDMX-CSV body is fed through an INJECTED ``Callable[[], str]`` transport,
``OecdCpiClient.fetch`` parses it into the RAW ``(series_id, date, value)`` frame
``OecdJapanCpiNormalizer`` reads, and the REAL normalizer is then run over that frame
and asserted to yield exactly the ``fred_series`` table columns under the sentinel
``OECD_JPN_CPI`` series id (with blank / non-parseable SDMX rows skipped).
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from facdrone_data.acquisition.families.normalizers.oecd_etf import OecdJapanCpiNormalizer
from facdrone_data.acquisition.schema import FRED_SERIES
from facdrone_data.providers.oecd_source import (
    OecdCpiClient,
    OecdCsvFetch,
    live_oecd,
    parse_oecd_sdmx_csv,
)
from facdrone_data_types.ports import FetchRequest, MarketDataClient
from facdrone_data_types.symbols import Provider

_REQ = FetchRequest(
    provider_symbol="OECD_JPN_CPI",  # sentinel series id — not used for any mapping
    dataset="fred_series",
    start=date(2023, 1, 1),
    end=date(2024, 1, 1),
)
_TABLE_COLUMNS = [c.name for c in FRED_SERIES.columns]

# A recorded OECD SDMX-CSV body: the COICOP2018 Japan CPI response carries many SDMX
# dimension columns; only ``TIME_PERIOD`` and ``OBS_VALUE`` are read by the parser. Two
# good monthly rows plus rows the parser must skip: a blank ``OBS_VALUE``, a blank
# ``TIME_PERIOD``, and a non-parseable ``OBS_VALUE``.
_RECORDED_CSV = (
    "STRUCTURE,STRUCTURE_ID,REF_AREA,FREQ,TIME_PERIOD,OBS_VALUE,UNIT_MEASURE\n"
    "DATAFLOW,DF_PRICES_C2018_ALL,JPN,M,2023-11,107.3,IX\n"
    "DATAFLOW,DF_PRICES_C2018_ALL,JPN,M,2023-12,107.9,IX\n"
    "DATAFLOW,DF_PRICES_C2018_ALL,JPN,M,2024-01,,IX\n"
    "DATAFLOW,DF_PRICES_C2018_ALL,JPN,M,,108.4,IX\n"
    "DATAFLOW,DF_PRICES_C2018_ALL,JPN,M,2024-02,n/a,IX\n"
)


def _fixed_transport(payload: str = _RECORDED_CSV) -> OecdCsvFetch:
    def _t() -> str:
        return payload

    return _t


def test_client_conforms_to_the_market_data_client_port() -> None:
    client = OecdCpiClient(transport=_fixed_transport())
    assert isinstance(client, MarketDataClient)
    assert client.provider is Provider.OECD
    assert client.datasets() == ("fred_series",)


def test_fetch_parses_csv_into_the_raw_series_id_date_value_shape() -> None:
    client = OecdCpiClient(transport=_fixed_transport())
    raw = client.fetch(_REQ)

    # Raw is exactly the shape the normalizer reads; the three bad SDMX rows are dropped.
    assert list(raw.columns) == ["series_id", "date", "value"]
    assert len(raw) == 2
    assert set(raw["series_id"]) == {"OECD_JPN_CPI"}  # sentinel series id
    # ``TIME_PERIOD`` YYYY-MM is anchored to the first of the month.
    assert list(raw["date"]) == [date(2023, 11, 1), date(2023, 12, 1)]
    assert list(raw["value"]) == [107.3, 107.9]


def test_recorded_payload_maps_through_the_real_normalizer_to_fred_series() -> None:
    """payload -> fetch -> REAL normalize -> exactly the fred_series columns."""
    client = OecdCpiClient(transport=_fixed_transport())
    raw = client.fetch(_REQ)

    out = OecdJapanCpiNormalizer().normalize(raw, symbol=None, as_of=datetime(2024, 2, 1, 12, 0))

    assert list(out.columns) == _TABLE_COLUMNS
    assert len(out) == 2
    assert set(out["series_id"]) == {"OECD_JPN_CPI"}  # sentinel — series-keyed family
    assert list(out["date"]) == [date(2023, 11, 1), date(2023, 12, 1)]
    assert list(out["value"]) == [107.3, 107.9]


def test_parser_skips_blank_and_non_parseable_rows() -> None:
    """The standalone parser drops blank TIME_PERIOD / OBS_VALUE and non-numeric values,
    keeping only the two good observations."""
    parsed = parse_oecd_sdmx_csv(_RECORDED_CSV)
    assert len(parsed) == 2
    assert list(parsed["date"]) == [date(2023, 11, 1), date(2023, 12, 1)]


def test_empty_csv_yields_empty_raw_that_still_normalizes() -> None:
    """A header-only SDMX body parses to zero rows; the empty raw still threads through
    the normalizer to the fred_series columns (fail-open shape preserved)."""
    client = OecdCpiClient(transport=_fixed_transport("TIME_PERIOD,OBS_VALUE\n"))
    raw = client.fetch(_REQ)
    assert raw.empty

    out = OecdJapanCpiNormalizer().normalize(raw, symbol=None, as_of=datetime(2024, 2, 1, 12, 0))
    assert list(out.columns) == _TABLE_COLUMNS
    assert out.empty


def test_non_fred_series_dataset_returns_empty_fail_open() -> None:
    client = OecdCpiClient(transport=_fixed_transport())
    other = FetchRequest("OECD_JPN_CPI", "daily_prices", date(2024, 1, 1), date(2024, 1, 4))
    assert client.fetch(other).empty


def test_fetch_fails_open_on_transport_error() -> None:
    """A transport that raises is caught; fetch returns the empty raw shape,
    and that empty frame still normalizes to the fred_series columns."""

    def _boom() -> str:
        raise ConnectionError("OECD 403 / network down")

    client = OecdCpiClient(transport=_boom)
    raw = client.fetch(_REQ)
    assert list(raw.columns) == ["series_id", "date", "value"]
    assert raw.empty

    out = OecdJapanCpiNormalizer().normalize(raw, symbol=None, as_of=datetime(2024, 2, 1, 12, 0))
    assert list(out.columns) == _TABLE_COLUMNS
    assert out.empty


def test_fetch_fails_open_on_parse_error() -> None:
    """A transport returning a non-string (parse blows up) fails open to the empty raw
    shape rather than raising out of fetch."""

    def _bad() -> str:
        return None  # type: ignore[return-value]  # deliberately wrong to force a parse error

    client = OecdCpiClient(transport=_bad)
    raw = client.fetch(_REQ)
    assert list(raw.columns) == ["series_id", "date", "value"]
    assert raw.empty


def test_normalizer_drops_a_duplicate_observation_on_the_natural_key() -> None:
    """A doubled (series_id, date) observation in the SDMX body collapses to one row
    through the normalizer's dedup (R6.2) — idempotency at the persistence boundary."""
    doubled = "TIME_PERIOD,OBS_VALUE\n2023-11,107.3\n2023-11,107.3\n2023-12,107.9\n"
    client = OecdCpiClient(transport=_fixed_transport(doubled))
    out = OecdJapanCpiNormalizer().normalize(
        client.fetch(_REQ), symbol=None, as_of=datetime(2024, 2, 1, 12, 0)
    )
    assert list(out.columns) == _TABLE_COLUMNS
    assert len(out) == 2
    assert list(out["date"]) == [date(2023, 11, 1), date(2023, 12, 1)]


def test_live_oecd_factory_builds_a_callable_without_network() -> None:
    """Constructing the live factory must not touch the network; ``urllib.request`` is
    in the stdlib so the factory always returns a callable (it only hits the network
    when actually invoked, which this test does not do)."""
    transport = live_oecd()
    assert callable(transport)


def test_live_oecd_does_not_leak_a_key_into_the_repr() -> None:
    """OECD is keyless; the client holds only the transport closure and exposes no
    secret in its repr."""
    client = OecdCpiClient(transport=_fixed_transport())
    assert "apikey" not in repr(client).lower()
    assert "Mozilla" not in repr(client)


def test_raw_frame_is_a_dataframe() -> None:
    client = OecdCpiClient(transport=_fixed_transport())
    assert isinstance(client.fetch(_REQ), pd.DataFrame)
