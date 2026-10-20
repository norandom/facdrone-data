# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""OECD Japan CPI transport (R2.1, R2.3).

This module is the LIVE fetch layer for the OECD Japan CPI feed, which lands in the
``fred_series`` family under the **sentinel** series id ``OECD_JPN_CPI`` (one series,
not a universe). It downloads the OECD SDMX-REST COICOP2018 monthly
Japan CPI as SDMX-CSV, which is upserted into ``fred_series`` alongside the FRED series.

``OecdJapanCpiNormalizer`` reads a frame carrying ``series_id``, ``date`` and ``value``
columns (``symbol`` is ``None`` for this series-keyed family). So ``fetch`` returns
exactly those columns: it asks the injected transport for the raw SDMX-CSV *text*, then
parses it with ``parse_oecd_sdmx_csv``: anchor
``TIME_PERIOD`` ``YYYY-MM`` to the first of the month, float-coerce ``OBS_VALUE``, and
skip blank / non-parseable / out-of-shape rows (safe numeric).

The network call is an injected ``OecdCsvFetch`` transport (the SDMX-CSV body), so the
adapter is offline-testable; ``live_oecd`` GETs through ``policy.fetch_url``. Keyless.
"""

from __future__ import annotations

import csv
import io
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from facdrone_data.providers.policy import fetch_url
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from facdrone_data_types.ports import FetchRequest

log = logging.getLogger(__name__)

# () -> the OECD SDMX-CSV payload as text. May raise; the adapter catches and fails
# open (an empty frame skips the series).
OecdCsvFetch = Callable[[], str]

# The sentinel series id under which
# OECD Japan CPI is stored in ``fred_series`` (one series, not a symbol universe).
_OECD_JPN_CPI_SERIES_ID = "OECD_JPN_CPI"

# OECD SDMX-REST endpoint + headers. The browser ``User-Agent`` is
# load-bearing: the OECD endpoint 403s the default urllib UA. The ``Accept`` header asks
# for SDMX-CSV explicitly so the response is the CSV body ``parse_oecd_sdmx_csv`` reads.
_OECD_JPN_CPI_URL = (
    "https://sdmx.oecd.org/public/rest/data/"
    "OECD.SDD.TPS,DSD_PRICES_COICOP2018@DF_PRICES_C2018_ALL,1.0/"
    "JPN.M.N.CPI.IX._T.N._Z"
)
_OECD_SDMX_ACCEPT = "application/vnd.sdmx.data+csv;version=1.0.0"
_OECD_SDMX_USER_AGENT = "Mozilla/5.0 (facdrone data ingestion)"

# The raw columns ``OecdJapanCpiNormalizer`` reads.
_RAW_COLUMNS = ("series_id", "date", "value")


def parse_oecd_sdmx_csv(raw: str) -> pd.DataFrame:
    """Parse an OECD SDMX-CSV payload into the RAW ``(series_id, date, value)`` frame the
    normalizer reads.

    Anchors ``TIME_PERIOD`` ``YYYY-MM`` to the first of the month so monthly CPI rows
    coexist cleanly with the rest of ``fred_series`` (FRED's monthly convention is
    date = first of month). Skips rows with blank, non-parseable, or out-of-shape
    ``TIME_PERIOD`` / ``OBS_VALUE`` fields; ``value`` is float-coerced safely."""
    reader = csv.DictReader(io.StringIO(raw))
    rows: list[dict[str, object]] = []
    for record in reader:
        period = (record.get("TIME_PERIOD") or "").strip()
        raw_val = (record.get("OBS_VALUE") or "").strip()
        if not period or not raw_val:
            continue
        try:
            date_val = pd.to_datetime(period + "-01", format="%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        try:
            value = float(raw_val)
        except ValueError:
            continue
        rows.append(
            {
                "series_id": _OECD_JPN_CPI_SERIES_ID,
                "date": date_val,
                "value": value,
            }
        )
    return pd.DataFrame(rows, columns=list(_RAW_COLUMNS))


def live_oecd() -> OecdCsvFetch:
    """The live transport (wired only at the composition root): a GET with a browser
    ``User-Agent`` (the OECD endpoint 403s urllib's) and the SDMX ``Accept`` header, decoded
    as UTF-8. Keyless. ``start`` is the earliest period requested (year and month)."""

    def _get(start: str = "2010-01-01") -> str:
        url = f"{_OECD_JPN_CPI_URL}?startPeriod={start[:7]}"  # fixed https OECD endpoint
        headers = {"Accept": _OECD_SDMX_ACCEPT, "User-Agent": _OECD_SDMX_USER_AGENT}
        return fetch_url(Provider.OECD, url, headers).decode("utf-8", errors="replace")

    return _get


@dataclass(frozen=True)
class OecdCpiClient:
    """``MarketDataClient`` for the OECD Japan CPI feed (R2.1).

    The family lands in ``fred_series`` under the sentinel series id ``OECD_JPN_CPI``, so
    it is keyless and series-keyed: ``req.provider_symbol`` is the sentinel series id and
    is not used for any per-symbol mapping. ``fetch`` asks the injected transport for the
    SDMX-CSV text and parses it into the RAW ``series_id/date/value`` frame
    ``OecdJapanCpiNormalizer`` reads. A transport error fails open (empty frame). Any
    non-``fred_series`` dataset returns empty → the refresher fails open and skips it."""

    transport: OecdCsvFetch

    @property
    def provider(self) -> Provider:
        return Provider.OECD

    def fetch(self, req: FetchRequest) -> pd.DataFrame:
        if req.dataset != "fred_series":
            return pd.DataFrame()  # OECD serves only the (sentinel) fred_series feed
        try:
            raw_csv = self.transport()
        except Exception as exc:  # noqa: BLE001 — fail-open
            log.warning("OECD Japan CPI fetch failed: %s", exc)
            return pd.DataFrame(columns=list(_RAW_COLUMNS))
        try:
            return parse_oecd_sdmx_csv(raw_csv)
        except Exception as exc:  # noqa: BLE001 — parse error fails open
            log.warning("OECD Japan CPI parse failed: %s", exc)
            return pd.DataFrame(columns=list(_RAW_COLUMNS))

    def datasets(self) -> tuple[str, ...]:
        return ("fred_series",)
