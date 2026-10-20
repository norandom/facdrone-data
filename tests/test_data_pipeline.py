# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 11.2: the wired data pipeline (Req 8.2, 8.3, 4.6).

Structural assertions (no DB) prove `kedro pipeline describe data` shows the
acquire→concat→validate→stamp→project→merge graph with the injection slots as
free inputs. The end-to-end test runs the graph against the deterministic fakes
+ the scratch PostgreSQL database (skips where no scratch DSN is configured).
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
from kedro.io import DataCatalog, MemoryDataset
from kedro.runner import SequentialRunner
from sqlalchemy import Engine, select

from facdrone_data.acquisition import create_pipeline
from facdrone_data.acquisition.acquisition import AcquireDeps
from facdrone_data.acquisition.nodes import project_to_table_node
from facdrone_data.acquisition.schema import DAILY_PRICES
from facdrone_data.acquisition.symbol_registry import InMemorySymbolRegistry, SymbolMapping
from facdrone_data.acquisition.validation import KeepAll
from facdrone_data.quality.ensemble import EnsembleValidator
from facdrone_data.testing import EURUSD
from facdrone_data.testing.fakes import FakeMarketDataClient
from facdrone_data_types.symbols import Provider

_NODE_NAMES = {
    "data.acquire",
    "data.concat",
    "data.validate_detect",
    "data.apply_policy",
    "data.stamp",
    "data.project",
    "data.merge",
}
_INJECTION_SLOTS = {
    "acquire_deps",
    "panel_engine",
    "daily_prices_table",
    "quality_validator",
    "quality_policy",
    "symbol",
}
_PARAM_SLOTS = {
    "params:dataset",
    "params:want_range",
    "params:availability_column",
    "params:event_column",
}


def test_pipeline_describes_the_acquire_to_merge_graph() -> None:
    pipe = create_pipeline()
    assert {n.name for n in pipe.nodes} == _NODE_NAMES


def test_free_inputs_are_exactly_the_injection_slots_panel_and_params() -> None:
    # Pin the full composition-root contract: a leaked or missing free input fails.
    assert create_pipeline().inputs() == _INJECTION_SLOTS | {"daily_prices"} | _PARAM_SLOTS


def test_project_node_drops_columns_absent_from_the_table() -> None:
    frame = pd.DataFrame(
        {
            "symbol": ["EURUSD"],
            "date": [date(2024, 1, 1)],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [100],
            "event_date": [date(2024, 1, 1)],
            "knowledge_date": [date(2024, 1, 1)],
        }
    )
    out = project_to_table_node(frame, DAILY_PRICES)
    assert list(out.columns) == ["symbol", "date", "open", "high", "low", "close", "volume"]
    assert {"symbol", "date"} <= set(out.columns)  # the PK survives projection


def test_project_node_keeps_a_table_column_that_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    # A table WITH filing_date keeps it (e.g. FMP fundamentals).
    from facdrone_data.acquisition.schema import metadata

    table = metadata.tables["fmp_income_statement"]
    frame = pd.DataFrame(
        {"symbol": ["X"], "date": [date(2024, 1, 1)], "filing_date": [date(2024, 2, 1)]}
    )
    out = project_to_table_node(frame, table)
    assert "filing_date" in out.columns


# ── end-to-end against fakes + scratch PostgreSQL ──────────────────


def _registry() -> InMemorySymbolRegistry:
    return InMemorySymbolRegistry([SymbolMapping(EURUSD, {Provider.YFINANCE: "EURUSD=X"})])


def _mem(value: object) -> MemoryDataset:
    # copy_mode="assign": inject the live Engine/client/Table by reference (a
    # deepcopy would fail to pickle them) — this is the composition-root seam.
    return MemoryDataset(data=value, copy_mode="assign")


def _catalog(
    engine: Engine, panel: pd.DataFrame, *, client: FakeMarketDataClient | None = None
) -> tuple[FakeMarketDataClient, DataCatalog]:
    client = client or FakeMarketDataClient(provider=Provider.YFINANCE)
    catalog = DataCatalog()
    catalog["daily_prices"] = _mem(panel)
    catalog["acquire_deps"] = _mem(AcquireDeps(client=client, registry=_registry()))
    catalog["panel_engine"] = _mem(engine)
    catalog["daily_prices_table"] = _mem(DAILY_PRICES)
    catalog["quality_validator"] = _mem(EnsembleValidator())
    catalog["quality_policy"] = _mem(KeepAll())
    catalog["symbol"] = _mem(EURUSD)  # one symbol per run (a domain object, injected)
    catalog["params:dataset"] = _mem("daily_prices")
    catalog["params:want_range"] = _mem((date(2024, 1, 1), date(2024, 1, 5)))
    catalog["params:availability_column"] = _mem("knowledge_date")
    catalog["params:event_column"] = _mem("event_date")
    return client, catalog


def _stored(engine: Engine) -> pd.DataFrame:
    return pd.read_sql(select(DAILY_PRICES).order_by(DAILY_PRICES.c.date), engine)


def test_pipeline_runs_end_to_end_against_fakes(pg_engine: Engine) -> None:
    DAILY_PRICES.drop(pg_engine, checkfirst=True)
    DAILY_PRICES.create(pg_engine)
    try:
        client, catalog = _catalog(pg_engine, pd.DataFrame())  # empty panel → fetch all
        SequentialRunner().run(create_pipeline(), catalog)
        stored = _stored(pg_engine)
        assert len(stored) == 5  # 2024-01-01..05 acquired, validated, merged
        assert client.fetched_days > 0  # it actually fetched from the provider
        assert set(stored["symbol"]) == {"EURUSD"}  # canonical-keyed, not "EURUSD=X"
        assert list(stored.columns) == ["symbol", "date", "open", "high", "low", "close", "volume"]
    finally:
        DAILY_PRICES.drop(pg_engine, checkfirst=True)


def test_second_run_over_the_persisted_panel_fetches_nothing(pg_engine: Engine) -> None:
    DAILY_PRICES.drop(pg_engine, checkfirst=True)
    DAILY_PRICES.create(pg_engine)
    try:
        _c1, catalog1 = _catalog(pg_engine, pd.DataFrame())
        SequentialRunner().run(create_pipeline(), catalog1)
        first = _stored(pg_engine)
        # re-run with the persisted panel as the coverage source: coverage is full,
        # so the provider is NOT contacted (proves the skip, not just ON CONFLICT).
        client2, catalog2 = _catalog(pg_engine, first)
        SequentialRunner().run(create_pipeline(), catalog2)
        assert client2.calls == []  # zero downloads on the second run (R2.1)
        pd.testing.assert_frame_equal(first, _stored(pg_engine))  # byte-identical
    finally:
        DAILY_PRICES.drop(pg_engine, checkfirst=True)


def test_conflicting_second_run_preserves_history(pg_engine: Engine) -> None:
    DAILY_PRICES.drop(pg_engine, checkfirst=True)
    DAILY_PRICES.create(pg_engine)
    try:
        _c1, catalog1 = _catalog(pg_engine, pd.DataFrame())
        SequentialRunner().run(create_pipeline(), catalog1)
        original = _stored(pg_engine)["close"].to_list()
        # re-fetch the SAME dates with different values (empty coverage panel +
        # a revised client) → ON CONFLICT DO NOTHING keeps the original rows.
        revised = FakeMarketDataClient(provider=Provider.YFINANCE, price_offset=900.0)
        _c2, catalog2 = _catalog(pg_engine, pd.DataFrame(), client=revised)
        SequentialRunner().run(create_pipeline(), catalog2)
        assert _stored(pg_engine)["close"].to_list() == original  # history not overwritten
    finally:
        DAILY_PRICES.drop(pg_engine, checkfirst=True)
