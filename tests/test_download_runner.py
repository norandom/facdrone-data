# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The module's download runner on the database: resume and keyless runs (data-manager 1.6).

R3.3: an interrupted download resumes from each family's cursor without re-writing rows it
holds. R4.2: a missing key refuses that provider's families (naming the variable) and the
rest still run.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest
from sqlalchemy import Engine, func, select

from download_support import KNOWN_AT, PORTS, WINDOW, SyntheticClient, two_instrument_universe
from facdrone_data.acquisition.families.credentials import ProviderCredentials
from facdrone_data.acquisition.families.refresher import (
    DownloadReport,
    Refusal,
    RefusedFamily,
    passthrough_gate,
)
from facdrone_data.acquisition.schema import DAILY_PRICES
from facdrone_data.quality.ensemble import EnsembleValidator
from facdrone_data.runner import (
    DataRootDeps,
    DownloadCommand,
    DownloadRequest,
    compose_download_runner,
)
from facdrone_data_types.ports import MarketDataClient
from facdrone_data_types.symbols import Provider

_KEY = "synthetic-fmp-key"  # noqa: S105 — a test fixture, not a real key


def _runner(
    engine: Engine, credentials: ProviderCredentials, clients: dict[Provider, SyntheticClient]
) -> Callable[[DownloadRequest], DownloadReport]:
    universe = two_instrument_universe()
    deps = DataRootDeps(
        engine=engine,
        credentials=credentials,
        requirement=lambda: universe.requirement(engine, KNOWN_AT),
        clients=cast("dict[Provider, MarketDataClient]", clients),
        validator=EnsembleValidator(),
        installed=lambda name: True,
    )
    return compose_download_runner(
        deps, family_selector=lambda source: PORTS, gate=passthrough_gate
    )


def _rows(engine: Engine) -> dict[str, int]:
    query = select(DAILY_PRICES.c.symbol, func.count()).group_by(DAILY_PRICES.c.symbol)
    with engine.connect() as conn:
        return {str(symbol): int(count) for symbol, count in conn.execute(query).all()}


def test_an_interrupted_download_resumes_without_duplicates_and_advances_every_cursor(
    prices_engine: Engine,
) -> None:
    credentials = ProviderCredentials(fmp_api_key=_KEY, fred_api_key=None)
    command = DownloadCommand("all", *WINDOW)
    interrupted = {
        Provider.YFINANCE: SyntheticClient(Provider.YFINANCE),
        Provider.FMP: SyntheticClient(Provider.FMP, interrupt=KeyboardInterrupt()),
    }
    with pytest.raises(KeyboardInterrupt):
        _runner(prices_engine, credentials, interrupted)(command)
    assert _rows(prices_engine) == {"AAA": 5}  # the first family landed before the interrupt

    clients = {
        Provider.YFINANCE: SyntheticClient(Provider.YFINANCE),
        Provider.FMP: SyntheticClient(Provider.FMP),
    }
    report = _runner(prices_engine, credentials, clients)(command)

    assert _rows(prices_engine) == {"AAA": 5, "BBB": 5}  # nothing written twice
    landed = {row.writer: row for row in report.summaries if row.scope in {"AAA", "BBB"}}
    assert landed["free_prices"].fetched == 0  # held rows are not re-requested
    assert landed["keyed_prices"].cursor == WINDOW[1]
    assert report.refused == ()


def test_without_a_key_the_keyless_families_run_and_the_keyed_ones_are_refused(
    prices_engine: Engine,
) -> None:
    clients = {
        Provider.YFINANCE: SyntheticClient(Provider.YFINANCE),
        Provider.FMP: SyntheticClient(Provider.FMP),
    }
    report = _runner(prices_engine, ProviderCredentials(None, None), clients)(
        DownloadCommand("all", *WINDOW)
    )
    assert report.refused == (RefusedFamily("keyed_prices", Refusal.REFUSED, "FMP_API_KEY"),)
    assert _rows(prices_engine) == {"AAA": 5}
    assert {row.writer for row in report.summaries} == {"free_prices"}
    assert next(row for row in report.summaries if row.scope == "AAA").cursor == WINDOW[1]
