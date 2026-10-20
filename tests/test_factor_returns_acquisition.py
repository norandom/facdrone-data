# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""factor_returns acquisition: long normalizer + the Ken French / AQR clients.

The live downloads (Dartmouth FF3/momentum ZIPs, the AQR Data Library files) are
injected transports, so these run fully offline against synthetic payloads. They
prove: the generic normalizer (date coercion, NaN drop, dedup, units passed
through), the FF3 percent→decimal re-projection, momentum parsing, AQR routing +
window, and registry/port wiring.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime

import pandas as pd
import pytest

from facdrone_data.acquisition.families.normalizers.factor_returns import (
    FactorReturnsNormalizer,
    factor_returns_families,
)
from facdrone_data.acquisition.families.registry import download_families
from facdrone_data.providers.factor_returns_source import (
    EUROPE_FF3,
    EUROPE_MOMENTUM,
    US_FF3,
    US_MOMENTUM,
    AqrFactorReturnsClient,
    KenFrenchFactorReturnsClient,
)
from facdrone_data_types.ports import FetchRequest
from facdrone_data_types.symbols import Provider

_AS_OF = datetime(2026, 6, 21, 12, 0, 0)


def _zip_csv(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("data.csv", text)
    return buf.getvalue()


def _aqr_xlsx(usa_rows: list[tuple[str, float]]) -> bytes:
    """Synthetic AQR daily workbook mirroring the live layout: a disclaimer
    preamble, an ``EQUITIES`` band, a ``DATE | AUS | USA`` header, then
    ``MM/DD/YYYY`` dates with decimal USA returns (AUS left blank). Exercises the
    real XLSX / USA-column path of ``_parse_aqr_daily``."""
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "QMJ Factors"
    for _ in range(16):
        sheet.append(["AQR Capital Management — disclaimer preamble"])
    sheet.append([None, "EQUITIES"])
    sheet.append(["DATE", "AUS", "USA"])  # USA deliberately not adjacent to DATE
    for day, ret in usa_rows:
        sheet.append([day, None, ret])
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


_FF3_CSV = """This file was created ...

,Mkt-RF,SMB,HML,RF
20170103,  1.13,  0.20, -0.30,  0.01
20170104, -0.42,  0.10,  0.05,  0.01

Annual Factors: January-December
2017, 21.00, 1.0, 2.0, 0.8
"""

_MOM_CSV = """This file was created ...

,Mom
20170103,  0.56
20170104, -99.99
"""


def _req(factor_id: str, start: date, end: date) -> FetchRequest:
    return FetchRequest(factor_id, "factor_returns", start, end)


# ── Normalizer ──────────────────────────────────────────────────────


def test_normalizer_long_path_drops_nan_and_dedups() -> None:
    raw = pd.DataFrame(
        [
            {"factor_id": "MOM", "date": "2017-01-03", "ret": 0.0056},
            {"factor_id": "MOM", "date": "2017-01-04", "ret": None},  # dropped, not 0
            {"factor_id": "MOM", "date": "2017-01-03", "ret": 0.0099},  # dup → last wins
        ]
    )
    out = FactorReturnsNormalizer().normalize(raw, symbol=None, as_of=_AS_OF)
    assert list(out.columns) == ["factor_id", "date", "ret", "_updated_at"]
    assert len(out) == 1
    assert out.iloc[0]["factor_id"] == "MOM"
    assert out.iloc[0]["date"] == date(2017, 1, 3)
    assert out.iloc[0]["ret"] == 0.0099
    assert out.iloc[0]["_updated_at"] == _AS_OF


def test_normalizer_empty_returns_table_shaped_frame() -> None:
    out = FactorReturnsNormalizer().normalize(pd.DataFrame(), symbol=None, as_of=_AS_OF)
    assert out.empty
    assert "factor_id" in out.columns and "ret" in out.columns


# ── Ken French client: FF3 re-projection + momentum ─────────────────


def _kenfrench_client() -> KenFrenchFactorReturnsClient:
    return KenFrenchFactorReturnsClient(
        {
            US_FF3: lambda: _zip_csv(_FF3_CSV),
            US_MOMENTUM: lambda: _zip_csv(_MOM_CSV),
        }
    )


def test_kenfrench_reprojects_mkt_as_decimal_excess_return() -> None:
    out = _kenfrench_client().fetch(_req("MKT", date(2017, 1, 1), date(2017, 12, 31)))
    assert list(out["factor_id"].unique()) == ["MKT"]
    assert out.iloc[0]["ret"] == pytest.approx(0.0113)  # 1.13% → decimal; Mkt-RF (excess)
    assert out.iloc[1]["ret"] == pytest.approx(-0.0042)
    assert "2017" not in out["date"].astype(str).tolist()  # annual block excluded


def test_kenfrench_momentum_nulls_missing_sentinel_before_decimal() -> None:
    out = _kenfrench_client().fetch(_req("MOM", date(2017, 1, 1), date(2017, 12, 31)))
    assert out.iloc[0]["ret"] == pytest.approx(0.0056)
    # -99.99 is the Ken French missing-data sentinel (percent units): nulled BEFORE the /100
    # divide, so it never becomes a real ~-99.99% return. The normalizer then
    # drops the NaN day rather than persisting a fake crash.
    assert pd.isna(out.iloc[1]["ret"])


def test_kenfrench_ff3_nulls_missing_sentinel_before_decimal() -> None:
    csv = (
        "This file was created ...\n\n"
        ",Mkt-RF,SMB,HML,RF\n"
        "20170103,  1.13,  0.20, -0.30,  0.01\n"
        "20170104,-99.99,-99.99,-99.99,-99.99\n"  # CRSP missing-day sentinel row
    )
    client = KenFrenchFactorReturnsClient(
        {US_FF3: lambda: _zip_csv(csv), US_MOMENTUM: lambda: _zip_csv(_MOM_CSV)}
    )
    out = client.fetch(_req("MKT", date(2017, 1, 1), date(2017, 12, 31)))
    assert out.iloc[0]["ret"] == pytest.approx(0.0113)
    assert pd.isna(out.iloc[1]["ret"])  # sentinel nulled, not converted to -0.9999


def test_kenfrench_windows_inclusively() -> None:
    # The client returns the source's raw YYYYMMDD date token; the normalizer
    # coerces it to a real date downstream (mirrors FamaFrenchNormalizer).
    out = _kenfrench_client().fetch(_req("MKT", date(2017, 1, 4), date(2017, 1, 4)))
    assert list(out["date"]) == ["20170104"]


def test_kenfrench_wrong_dataset_is_empty() -> None:
    bad = FetchRequest("MKT", "fama_french_factors", date(2017, 1, 1), date(2017, 12, 31))
    assert _kenfrench_client().fetch(bad).empty


def test_an_unconfigured_archive_degrades_one_factor_not_the_client() -> None:
    """A client wired for the US archives only must return MOM_EU empty — and
    keep serving MKT — rather than raising and taking the download down."""
    client = _kenfrench_client()
    assert client.fetch(_req("MOM_EU", date(2017, 1, 1), date(2017, 12, 31))).empty
    assert not client.fetch(_req("MKT", date(2017, 1, 1), date(2017, 12, 31))).empty


# ── Ken French Europe: same shape, different column and archive ─────

# Verbatim shape of Europe_3_Factors_Daily.csv and Europe_MOM_Factor_Daily.csv
# (whitespace-padded fields, the WML momentum header, no trailing annual block).
_EU_FF3_CSV = """This file was created using the 202606 Bloomberg database.

Missing data are indicated by -99.99.

,Mkt-RF,SMB,HML,RF
19900702    ,0.99    ,0.06   ,-0.56    ,0.03
20260630    ,0.63   ,-0.07   ,-0.53    ,0.01
"""

_EU_MOM_CSV = """This file was created using the 202606 Bloomberg database.

Missing data are indicated by -99.99.

,WML
19901101    ,0.29
20260630    ,1.47
"""


def _europe_client() -> KenFrenchFactorReturnsClient:
    return KenFrenchFactorReturnsClient(
        {
            EUROPE_FF3: lambda: _zip_csv(_EU_FF3_CSV),
            EUROPE_MOMENTUM: lambda: _zip_csv(_EU_MOM_CSV),
        }
    )


def test_europe_ff3_columns_are_read_through_the_padded_fields() -> None:
    """Ken French pads regional fields with spaces (``19900702    ,0.99``); the
    column reader must still land on the right column per factor id."""
    client = _europe_client()
    window = (date(1990, 1, 1), date(2026, 12, 31))
    mkt = client.fetch(_req("MKT_EU", *window))
    smb = client.fetch(_req("SMB_EU", *window))
    hml = client.fetch(_req("HML_EU", *window))
    assert list(mkt["factor_id"].unique()) == ["MKT_EU"]
    assert mkt.iloc[0]["ret"] == pytest.approx(0.0099)  # Mkt-RF, not the RF column
    assert smb.iloc[0]["ret"] == pytest.approx(0.0006)
    assert hml.iloc[0]["ret"] == pytest.approx(-0.0056)
    assert list(mkt["date"]) == ["19900702", "20260630"]


def test_europe_momentum_reads_the_wml_column() -> None:
    """The regional momentum file names its column WML, not Mom — a US-shaped
    reader searching for "Mom" finds nothing here."""
    out = _europe_client().fetch(_req("MOM_EU", date(1990, 1, 1), date(2026, 12, 31)))
    assert list(out["factor_id"].unique()) == ["MOM_EU"]
    assert out.iloc[0]["ret"] == pytest.approx(0.0029)
    assert out.iloc[1]["ret"] == pytest.approx(0.0147)


def test_europe_and_us_factor_ids_stay_separate_series() -> None:
    """MKT and MKT_EU are different series in the same table; a client holding
    both archives must never serve one for the other."""
    client = KenFrenchFactorReturnsClient(
        {
            US_FF3: lambda: _zip_csv(_FF3_CSV),
            US_MOMENTUM: lambda: _zip_csv(_MOM_CSV),
            EUROPE_FF3: lambda: _zip_csv(_EU_FF3_CSV),
            EUROPE_MOMENTUM: lambda: _zip_csv(_EU_MOM_CSV),
        }
    )
    us = client.fetch(_req("MKT", date(2017, 1, 1), date(2017, 12, 31)))
    eu = client.fetch(_req("MKT_EU", date(1990, 1, 1), date(2026, 12, 31)))
    assert list(us["date"]) == ["20170103", "20170104"]
    assert list(eu["date"]) == ["19900702", "20260630"]


# ── AQR client ──────────────────────────────────────────────────────


def test_aqr_parses_usa_decimal_from_xlsx_and_routes_on_factor_id() -> None:
    payloads = {
        "QMJ": _aqr_xlsx([("01/03/2017", 0.0008), ("01/04/2017", 0.0011)]),
        "BAB": _aqr_xlsx([("01/03/2017", -0.0003)]),
    }
    client = AqrFactorReturnsClient(transport=lambda fid: payloads[fid])
    qmj = client.fetch(_req("QMJ", date(2017, 1, 1), date(2017, 12, 31)))
    assert list(qmj["factor_id"].unique()) == ["QMJ"]
    assert qmj.iloc[0]["ret"] == pytest.approx(0.0008)  # USA column, already decimal
    assert qmj.iloc[1]["ret"] == pytest.approx(0.0011)
    bab = client.fetch(_req("BAB", date(2017, 1, 1), date(2017, 12, 31)))
    assert bab.iloc[0]["ret"] == pytest.approx(-0.0003)


def test_aqr_windows_inclusively_on_parsed_dates() -> None:
    payload = _aqr_xlsx([("01/03/2017", 0.0008), ("01/04/2017", 0.0011)])
    client = AqrFactorReturnsClient(transport=lambda fid: payload)
    out = client.fetch(_req("QMJ", date(2017, 1, 4), date(2017, 1, 4)))
    assert len(out) == 1
    assert out.iloc[0]["ret"] == pytest.approx(0.0011)


def test_aqr_unknown_factor_and_bad_payload_fail_open() -> None:
    client = AqrFactorReturnsClient(transport=lambda fid: b"not an xlsx workbook at all")
    # Unknown factor id → empty (routed out before any transport read).
    assert client.fetch(_req("ZZZ", date(2017, 1, 1), date(2017, 12, 31))).empty
    # Non-XLSX payload → openpyxl raises → fail-open empty.
    assert client.fetch(_req("QMJ", date(2017, 1, 1), date(2017, 12, 31))).empty


def test_aqr_xlsx_without_usa_column_fails_open() -> None:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "QMJ Factors"
    for _ in range(3):
        sheet.append(["preamble"])
    sheet.append(["DATE", "AUS", "CAN"])  # no USA column
    sheet.append(["01/03/2017", 0.1, 0.2])
    buf = io.BytesIO()
    workbook.save(buf)
    client = AqrFactorReturnsClient(transport=lambda fid: buf.getvalue())
    assert client.fetch(_req("QMJ", date(2017, 1, 1), date(2017, 12, 31))).empty


# ── Port wiring ─────────────────────────────────────────────────────


def test_ports_are_factor_keyed_and_registered() -> None:
    ports = {p.writer: p for p in factor_returns_families()}
    assert set(ports) == {"refresh_factor_returns_kenfrench", "refresh_factor_returns_aqr"}
    for port in ports.values():
        assert port.natural_key == ("factor_id", "date")
        assert port.scopes  # factor ids, iterated by the refresher
    assert ports["refresh_factor_returns_kenfrench"].provider is Provider.KEN_FRENCH
    assert ports["refresh_factor_returns_aqr"].provider is Provider.AQR
    registered = {p.writer for p in download_families().families()}
    assert set(ports) <= registered
