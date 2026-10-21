# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""A family whose availability column is not a date lands with no run cursor.

Replays the nightly refresh failure of 2026-10-15 to 2026-10-20: ``upsert_etf_meta`` stamps
availability on ``symbol``, and the run cursor parsed its first scope "SPY" as a date
(``DateParseError``), aborting the refresh before ``refresh_etf_daily`` ran.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from typing import cast

import pandas as pd
from sqlalchemy import Engine, select

from facdrone_data.acquisition.families.normalizers.oecd_etf import oecd_etf_families
from facdrone_data.acquisition.families.refresher import DownloadInputs, FamilyRefresher
from facdrone_data.acquisition.families.symbol_mapping import ProviderSymbols
from facdrone_data.acquisition.instrument_table import InstrumentTable
from facdrone_data.acquisition.schema import ETF_META
from facdrone_data.acquisition.validation import KeepAll
from facdrone_data.providers.etf_source import EtfClient
from facdrone_data.quality.ensemble import EnsembleValidator
from facdrone_data.testing import API_KEY
from facdrone_data.testing.plugin import empty_tables
from facdrone_data_types.ports import SymbolRegistry
from facdrone_data_types.symbols import Provider


def _no_network(url: str, params: object) -> object:
    raise AssertionError("etf_meta is the static universe; it must not reach the network")


def test_a_family_without_a_date_axis_lands_with_no_cursor(pg_engine: Engine) -> None:
    meta = next(port for port in oecd_etf_families() if port.writer == "upsert_etf_meta")
    inputs = DownloadInputs(
        universe=(),
        families=(replace(meta, scopes=("SPY", "TLT")),),
        want=(date(2024, 1, 1), date(2024, 1, 5)),
        engine=pg_engine,
        clients={Provider.ETF: EtfClient(transport=_no_network, api_key=API_KEY)},
        registry=cast(SymbolRegistry, None),  # the static universe needs no symbol mapping
        symbols=ProviderSymbols(InstrumentTable(())),
        validator=cast(EnsembleValidator, None),  # passthrough gate ignores it
        policy=KeepAll(),
        as_of=datetime(2024, 1, 6, 12, 0),
    )
    empty_tables(pg_engine, ETF_META)
    try:
        report = FamilyRefresher().run(inputs)  # raised DateParseError on "SPY"
        stored = set(pd.read_sql(select(ETF_META.c.symbol), pg_engine)["symbol"])
    finally:
        empty_tables(pg_engine, ETF_META)
    assert [(row.scope, row.inserted, row.cursor) for row in report.summaries] == [
        ("SPY", 1, None),
        ("TLT", 1, None),
    ]
    assert stored == {"SPY", "TLT"}
