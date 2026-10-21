# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 3.3: the orchestration-free family refresher persists via the family's typed
strategies (R2.1, R2.4, R5.3, R7.1, R7.4, R8.2)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from typing import cast

import pandas as pd
import pytest
from sqlalchemy import Engine, select

from facdrone_data.acquisition.families.cursors import MinMaxRangeGapFill
from facdrone_data.acquisition.families.normalizers.oecd_etf import oecd_etf_families
from facdrone_data.acquisition.families.ports import FamilyPort, Normalizer
from facdrone_data.acquisition.families.refresher import (
    DownloadInputs,
    FamilyRefresher,
    ProviderOutcome,
)
from facdrone_data.acquisition.families.symbol_mapping import ProviderSymbols
from facdrone_data.acquisition.instrument_table import InstrumentTable
from facdrone_data.acquisition.merge import UpsertPanel
from facdrone_data.acquisition.schema import DAILY_PRICES, ETF_META
from facdrone_data.acquisition.symbol_registry import SymbolConfigError
from facdrone_data.acquisition.validation import KeepAll
from facdrone_data.providers.etf_source import EtfClient
from facdrone_data.quality.ensemble import EnsembleValidator
from facdrone_data.testing import API_KEY, EURUSD
from facdrone_data.testing.fakes import FakeMarketDataClient
from facdrone_data.testing.plugin import empty_tables
from facdrone_data_types.enums import AssetClass
from facdrone_data_types.ports import FetchRequest, SymbolRegistry
from facdrone_data_types.symbols import CanonicalSymbol, Provider

# The family mappers over an empty identity table: these tests inject their own mappers.
MAPPERS = ProviderSymbols(InstrumentTable(()))

_PRICE_COLS = ["symbol", "date", "open", "high", "low", "close", "volume"]
WHEAT = CanonicalSymbol(canonical_id="ZW=F", asset_class=AssetClass.COMMODITIES, profit_ccy="USD")


class _PricesNormalizer:
    """Trivial normalizer: project the fake's raw frame onto daily_prices columns,
    re-keyed to the canonical symbol."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        del as_of
        out = raw[_PRICE_COLS].copy()
        if symbol is not None:
            out["symbol"] = symbol.canonical_id
        return out


class _FakeRegistry:
    """Maps EURUSD→FMP 'EURUSD'; raises for any other symbol (drives R5.3)."""

    def to_provider(self, sym: CanonicalSymbol, provider: Provider) -> str:
        del provider
        if sym.canonical_id == "EURUSD":
            return "EURUSD"
        raise SymbolConfigError(f"no mapping for {sym.canonical_id!r}")

    def to_canonical(self, provider_symbol: str, provider: Provider) -> CanonicalSymbol:
        raise NotImplementedError

    def all(self) -> tuple[CanonicalSymbol, ...]:
        return (EURUSD,)

    def validate(self) -> None:
        return None


def _port() -> FamilyPort:
    return FamilyPort(
        writer="refresh_fmp_daily",
        target_table=DAILY_PRICES,
        natural_key=("symbol", "date"),
        provider=Provider.FMP,
        incremental=MinMaxRangeGapFill(),
        merge_strategy=UpsertPanel(),
        normalizer=cast(Normalizer, _PricesNormalizer()),  # structural Normalizer
        availability_column="date",
        quality_policy_default="keep_all",
        symbol_mapper=lambda _mappers, symbol: "EURUSD" if symbol == "EURUSD" else None,
    )


def _inputs(engine: Engine, universe: tuple[CanonicalSymbol, ...]) -> DownloadInputs:
    return DownloadInputs(
        universe=universe,
        families=(_port(),),
        want=(date(2024, 1, 1), date(2024, 1, 5)),
        engine=engine,
        clients={Provider.FMP: FakeMarketDataClient(provider=Provider.FMP)},
        registry=cast(SymbolRegistry, _FakeRegistry()),
        symbols=MAPPERS,
        validator=cast(EnsembleValidator, None),  # passthrough gate ignores it
        policy=KeepAll(),
        as_of=datetime(2024, 1, 6, 12, 0),
    )


def test_refresher_persists_rows_via_the_family_strategies(prices_engine: Engine) -> None:
    report = FamilyRefresher().run(_inputs(prices_engine, (EURUSD,)))
    stored = pd.read_sql(select(DAILY_PRICES).order_by(DAILY_PRICES.c.date), prices_engine)
    assert len(stored) == 5  # 2024-01-01 .. 2024-01-05 persisted
    assert set(stored["symbol"]) == {"EURUSD"}  # re-keyed to canonical
    summary = report.summaries[0]
    assert summary.writer == "refresh_fmp_daily"
    assert summary.fetched == 5 and summary.inserted == 5 and summary.updated == 0


def test_refresher_second_run_is_idempotent(prices_engine: Engine) -> None:
    FamilyRefresher().run(_inputs(prices_engine, (EURUSD,)))
    report = FamilyRefresher().run(_inputs(prices_engine, (EURUSD,)))
    # Fully covered → cursor yields no gap → no fetch, no writes (R7.1, R7.4).
    summary = report.summaries[0]
    assert summary.fetched == 0 and summary.inserted == 0 and summary.updated == 0
    assert summary.skipped == 1
    assert len(pd.read_sql(select(DAILY_PRICES), prices_engine)) == 5


@pytest.mark.parametrize("refetch", [False, True])
def test_refresher_refetch_requests_the_whole_stored_window(
    prices_engine: Engine, refetch: bool
) -> None:
    inputs = _inputs(prices_engine, (EURUSD,))
    refresher = FamilyRefresher()
    refresher.run(inputs)
    client = cast(FakeMarketDataClient, inputs.clients[Provider.FMP])
    client.calls.clear()

    summary = refresher.run(replace(inputs, refetch=refetch)).summaries[0]

    expected = [FetchRequest("EURUSD", "daily_prices", *inputs.want)] if refetch else []
    assert client.calls == expected
    assert summary.fetched == (5 if refetch else 0)
    assert summary.provider_outcome == (
        ProviderOutcome.PROVIDER_VERIFIED if refetch else ProviderOutcome.ALREADY_LANDED
    )
    assert len(pd.read_sql(select(DAILY_PRICES), prices_engine)) == 5


def test_refresher_skips_and_records_an_unmapped_symbol(prices_engine: Engine) -> None:
    report = FamilyRefresher().run(_inputs(prices_engine, (EURUSD, WHEAT)))
    by_scope = {s.scope: s for s in report.summaries}
    assert by_scope["ZW=F"].skipped == 1 and by_scope["ZW=F"].fetched == 0  # R5.3
    assert by_scope["EURUSD"].inserted == 5  # the mapped symbol still ran


def test_refresher_fails_open_on_empty_payload(prices_engine: Engine) -> None:
    inputs = _inputs(prices_engine, (EURUSD,))
    empty_client = FakeMarketDataClient(provider=Provider.FMP)
    empty_client.fetch = lambda req: pd.DataFrame(columns=_PRICE_COLS)  # type: ignore[method-assign]
    inputs = DownloadInputs(
        universe=inputs.universe,
        families=inputs.families,
        want=inputs.want,
        engine=inputs.engine,
        clients={Provider.FMP: empty_client},
        registry=inputs.registry,
        symbols=inputs.symbols,
        validator=inputs.validator,
        policy=inputs.policy,
        as_of=inputs.as_of,
    )
    report = FamilyRefresher().run(inputs)  # must not raise
    assert report.summaries[0].fetched == 0
    assert len(pd.read_sql(select(DAILY_PRICES), prices_engine)) == 0


def test_refresher_fails_open_when_a_transport_raises(prices_engine: Engine) -> None:
    """A provider/transport fault (HTTP error, missing optional SDK) must not abort
    the run — the family is recorded with nothing fetched (R2.4)."""
    inputs = _inputs(prices_engine, (EURUSD,))

    def _boom(req: object) -> pd.DataFrame:
        raise ModuleNotFoundError("No module named 'cot_reports'")

    raising = FakeMarketDataClient(provider=Provider.FMP)
    raising.fetch = _boom  # type: ignore[method-assign]
    inputs = DownloadInputs(
        universe=inputs.universe,
        families=inputs.families,
        want=inputs.want,
        engine=inputs.engine,
        clients={Provider.FMP: raising},
        registry=inputs.registry,
        symbols=inputs.symbols,
        validator=inputs.validator,
        policy=inputs.policy,
        as_of=inputs.as_of,
    )
    report = FamilyRefresher().run(inputs)  # must not raise
    assert report.summaries[0].fetched == 0
    assert len(pd.read_sql(select(DAILY_PRICES), prices_engine)) == 0


def _no_network(url: str, params: object) -> object:
    raise AssertionError("etf_meta is the static universe; it must not reach the network")


def test_a_family_without_a_date_axis_lands_with_no_cursor(pg_engine: Engine) -> None:
    """Replays the nightly refresh failure of 2026-10-15 to 2026-10-20: ``upsert_etf_meta``
    stamps availability on ``symbol``, and the run cursor parsed its first scope "SPY" as a
    date (``DateParseError``), aborting the refresh before ``refresh_etf_daily`` ran."""
    meta = next(port for port in oecd_etf_families() if port.writer == "upsert_etf_meta")
    client = EtfClient(transport=_no_network, api_key=API_KEY)
    inputs = replace(
        _inputs(pg_engine, ()),
        families=(replace(meta, scopes=("SPY", "TLT")),),
        clients={Provider.ETF: client},
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
