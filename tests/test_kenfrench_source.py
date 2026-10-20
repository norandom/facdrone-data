# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Kenneth French daily Fama-French transport (R2.1, R2.3).

Proves payload -> fetch -> normalize end-to-end with NO network: a RECORDED zip
archive (built in-memory from a recorded CSV body) is fed through an INJECTED
``Callable[[], bytes]`` transport, ``KenFrenchClient.fetch`` unzips + parses the
daily block into the raw ``Date``/``Mkt-RF``/``SMB``/``HML``/``RF`` columns the
``FamaFrenchNormalizer`` reads, and the REAL normalizer is then run over that frame
and asserted to yield exactly the ``fama_french_factors`` table columns — with the
percent->decimal divide and the ``-99.99``/``-999`` sentinel->NULL applied.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime

import pandas as pd

from facdrone_data.acquisition.families.normalizers.fama_french import FamaFrenchNormalizer
from facdrone_data.acquisition.schema import FAMA_FRENCH_FACTORS
from facdrone_data.providers.kenfrench_source import (
    KenFrenchClient,
    KenFrenchZip,
    live_kenfrench,
)
from facdrone_data_types.ports import FetchRequest, MarketDataClient
from facdrone_data_types.symbols import Provider

_TABLE_COLUMNS = [c.name for c in FAMA_FRENCH_FACTORS.columns]

_REQ = FetchRequest(
    provider_symbol="",  # keyless global series — no provider symbol
    dataset="fama_french_factors",
    start=date(2024, 1, 1),
    end=date(2024, 1, 31),
)

# A recorded Dartmouth daily F-F CSV body: a multi-line preamble, the header row,
# four daily rows (one carrying a ``-99.99`` sentinel), a blank separator, then the
# annual block the parser must stop before. Values are in *percent* units.
_RECORDED_CSV = (
    "This file was created by CMPT_ME_BEME_RETS using the 202312 CRSP database.\n"
    "The 1-month TBill return is from Ibbotson and Associates Inc.\n"
    "\n"
    ",Mkt-RF,SMB,HML,RF\n"
    "20240102, 0.50,-0.20, 0.10, 0.021\n"
    "20240103,-0.30, 0.15,-0.05, 0.021\n"
    "20240104, 1.20, 0.40,-99.99, 0.021\n"
    "20240105, 0.00,-0.10, 0.30, 0.021\n"
    "\n"
    "  Annual Factors: January-December\n"
    ",Mkt-RF,SMB,HML,RF\n"
    "2023, 21.50, 7.30,-9.40, 5.030\n"
)


def _recorded_zip_bytes() -> bytes:
    """The recorded CSV packed into a single-member zip — the shape the live
    ``urllib`` transport returns from the Dartmouth endpoint."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("F-F_Research_Data_Factors_daily.CSV", _RECORDED_CSV)
    return buffer.getvalue()


def _fixed_transport(payload: bytes) -> KenFrenchZip:
    def _t() -> bytes:
        return payload

    return _t


def test_client_conforms_to_the_market_data_client_port() -> None:
    client = KenFrenchClient(transport=_fixed_transport(_recorded_zip_bytes()))
    assert isinstance(client, MarketDataClient)
    assert client.provider is Provider.KEN_FRENCH
    assert client.datasets() == ("fama_french_factors",)


def test_fetch_returns_the_raw_header_columns_the_normalizer_reads() -> None:
    client = KenFrenchClient(transport=_fixed_transport(_recorded_zip_bytes()))
    raw = client.fetch(_REQ)
    # Exactly the raw Ken French header fields FamaFrenchNormalizer consumes.
    assert list(raw.columns) == ["Date", "Mkt-RF", "SMB", "HML", "RF"]
    # The four daily rows only — the annual block past the blank line is dropped.
    assert list(raw["Date"]) == ["20240102", "20240103", "20240104", "20240105"]
    # Values remain raw percent tokens; the normalizer owns the divide + sentinel.
    assert "-99.99" in list(raw["HML"])


def test_recorded_payload_maps_through_the_real_normalizer_to_fama_french() -> None:
    """payload -> fetch -> REAL normalize -> exactly the fama_french_factors columns."""
    client = KenFrenchClient(transport=_fixed_transport(_recorded_zip_bytes()))
    raw = client.fetch(_REQ)

    out = FamaFrenchNormalizer().normalize(raw, symbol=None, as_of=datetime(2024, 2, 1, 12, 0))

    assert list(out.columns) == _TABLE_COLUMNS
    assert len(out) == 4
    # Date coerced from the %Y%m%d token to datetime.date.
    assert list(out["date"]) == [
        date(2024, 1, 2),
        date(2024, 1, 3),
        date(2024, 1, 4),
        date(2024, 1, 5),
    ]
    # Percent -> decimal divide (``v / 100``).
    assert out["mkt_rf"].to_list() == [0.005, -0.003, 0.012, 0.0]
    assert out["rf"].to_list() == [0.00021, 0.00021, 0.00021, 0.00021]
    # The -99.99 sentinel -> NULL, checked BEFORE the divide.
    assert pd.isna(out["hml"].iloc[2])
    assert out["hml"].iloc[0] == 0.001
    # Injected-clock stamp, never wall-clock.
    assert (out["_updated_at"] == datetime(2024, 2, 1, 12, 0)).all()


def test_fetch_windows_on_the_request_date_range() -> None:
    client = KenFrenchClient(transport=_fixed_transport(_recorded_zip_bytes()))
    req = FetchRequest(
        provider_symbol="",
        dataset="fama_french_factors",
        start=date(2024, 1, 3),
        end=date(2024, 1, 4),
    )
    raw = client.fetch(req)
    assert list(raw["Date"]) == ["20240103", "20240104"]


def test_non_kenfrench_dataset_returns_empty_fail_open() -> None:
    client = KenFrenchClient(transport=_fixed_transport(_recorded_zip_bytes()))
    other = FetchRequest(
        provider_symbol="",
        dataset="daily_prices",
        start=date(2024, 1, 1),
        end=date(2024, 1, 31),
    )
    assert client.fetch(other).empty


def test_fetch_fails_open_on_transport_error() -> None:
    def _boom() -> bytes:
        raise ConnectionError("network down")

    client = KenFrenchClient(transport=_boom)
    raw = client.fetch(_REQ)
    assert raw.empty
    # The empty raw still threads through the normalizer to the table columns.
    out = FamaFrenchNormalizer().normalize(raw, symbol=None, as_of=datetime(2024, 2, 1, 12, 0))
    assert list(out.columns) == _TABLE_COLUMNS
    assert out.empty


def test_fetch_fails_open_on_corrupt_zip() -> None:
    client = KenFrenchClient(transport=_fixed_transport(b"not a zip archive"))
    raw = client.fetch(_REQ)
    assert raw.empty
    assert list(raw.columns) == ["Date", "Mkt-RF", "SMB", "HML", "RF"]


def test_empty_zip_member_yields_empty_fetch() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("empty.CSV", "no header here\nrandom,line\n")
    client = KenFrenchClient(transport=_fixed_transport(buffer.getvalue()))
    raw = client.fetch(_REQ)
    assert raw.empty
    out = FamaFrenchNormalizer().normalize(raw, symbol=None, as_of=datetime(2024, 2, 1, 12, 0))
    assert list(out.columns) == _TABLE_COLUMNS
    assert out.empty


def test_live_kenfrench_factory_imports_without_network() -> None:
    """Constructing the live factory must not perform any network IO; it only
    returns a callable that would lazily import ``urllib.request`` when invoked."""
    transport = live_kenfrench()
    assert callable(transport)
