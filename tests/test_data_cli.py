# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The ``facdrone-data`` entry point (facdrone-data-manager 1.7; R3.1, R3.2).

Every command runs on the embedded database with fake provider clients, through the same
composition seam ``main`` uses, so the exit codes and lines are the shipped ones.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy import Engine
from typer.testing import CliRunner, Result

from facdrone_data.acquisition.families.credentials import ProviderCredentials
from facdrone_data.acquisition.families.refresher import passthrough_gate
from facdrone_data.cli.app import compose_data_cli
from facdrone_data.cli.compose import DataBackend, DataOptions, universe_resume
from facdrone_data.config import DataManagerConfig, InstrumentEntry
from facdrone_data.quality.ensemble import EnsembleValidator
from facdrone_data.runner import DataRootDeps, compose_download_runner, select_families
from facdrone_data.testing.fakes import FakeMarketDataClient
from facdrone_data.universe import DeclaredUniverse
from facdrone_data_types.enums import AssetClass
from facdrone_data_types.ports import MarketDataClient
from facdrone_data_types.symbols import Provider

TODAY = datetime(2025, 1, 11, 12, tzinfo=UTC)
WINDOW = ["--from", "2025-01-06", "--to", "2025-01-10"]
runner = CliRunner()


def _clock() -> datetime:
    return TODAY


def _universe() -> DeclaredUniverse:
    return DeclaredUniverse(
        DataManagerConfig(
            identity_files=(),
            instruments=(
                InstrumentEntry(
                    canonical="ZW=F", asset_class=AssetClass.COMMODITIES, profit_ccy="USD"
                ),
            ),
            conversion_currency="USD",
            default_source="yfinance",
        )
    )


def backend_over(
    engine: Engine, credentials: ProviderCredentials
) -> Callable[[DataOptions], DataBackend]:
    """The module's backend over fake clients: what ``live_backend`` binds, minus the network."""
    universe = _universe()
    clients = {provider: FakeMarketDataClient(provider) for provider in Provider}
    deps = DataRootDeps(
        engine=engine,
        credentials=credentials,
        requirement=lambda: universe.requirement(engine, _clock()),
        clients=cast("dict[Provider, MarketDataClient]", clients),
        validator=EnsembleValidator(),
        installed=lambda name: True,
    )
    backend = DataBackend(
        download=compose_download_runner(
            deps, family_selector=select_families, gate=passthrough_gate
        ),
        resume=universe_resume(universe, engine, _clock),
        engine=lambda: engine,
        default_source="yfinance",
    )
    return lambda options: backend


@pytest.fixture
def cli(prices_engine: Engine) -> Callable[..., Result]:
    app = compose_data_cli(backend_over(prices_engine, ProviderCredentials(None, None)), _clock)
    return lambda *args: runner.invoke(app, list(args))


def test_help_lists_the_five_commands() -> None:
    app = compose_data_cli(lambda options: cast("DataBackend", None), _clock)
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in ("download", "refetch", "update", "quality", "status"):
        assert command in result.output


def test_download_prints_one_line_per_family_and_the_totals(cli: Callable[..., Result]) -> None:
    result = cli("download", *WINDOW)
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert any(
        line.startswith("refresh_daily_prices ZW=F:") and "cursor=2025-01-10" in line
        for line in lines
    )
    assert any(line.startswith("families=") for line in lines)


def test_an_unranged_download_resumes_from_the_panel(cli: Callable[..., Result]) -> None:
    result = cli("download")
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0].startswith("resuming from the panel: ")


def test_a_missing_key_refuses_the_family_and_exits_3(cli: Callable[..., Result]) -> None:
    result = cli("download", "--source", "fred,yfinance", *WINDOW)
    assert result.exit_code == 3, result.output
    assert "reason=FRED_API_KEY" in result.output
    assert "refresh_daily_prices ZW=F:" in result.output


def test_refetch_and_update_run_the_explicit_window(cli: Callable[..., Result]) -> None:
    refetch = cli("refetch", *WINDOW, "--family", "daily_prices", "--source", "yfinance")
    assert refetch.exit_code == 0, refetch.output
    assert "inserted=5" in refetch.output
    update = cli("update", *WINDOW)
    assert update.exit_code == 0, update.output
    assert "families=" in update.output


def test_a_usage_error_exits_2(cli: Callable[..., Result]) -> None:
    result = cli("download", "--source", "nonsense", *WINDOW)
    assert result.exit_code == 2
    assert "unknown --source" in result.output


def test_quality_and_status_read_the_stored_rows(cli: Callable[..., Result]) -> None:
    cli("download", *WINDOW)
    quality = cli("quality", "--symbol", "ZW=F", *WINDOW)
    assert quality.exit_code in (0, 2), quality.output
    assert quality.output.splitlines()[-1].startswith("quality daily_prices ZW=F: score=")
    status = cli("status", "ZW=F")
    assert status.exit_code == 0, status.output
    assert "ZW=F refresh_daily_prices daily_prices: present start=2025-01-06" in status.output
    unknown = cli("quality", "--symbol", "ZW=F", *WINDOW, "--family", "nope")
    assert unknown.exit_code == 2


def test_an_error_exits_1_without_a_trace(prices_engine: Engine) -> None:
    def broken(options: DataOptions) -> DataBackend:
        raise RuntimeError("no database: postgresql://user:secret@host/db?apikey=abc")

    app = compose_data_cli(broken, _clock)
    result = runner.invoke(app, ["status", "ZW=F"])
    assert result.exit_code == 1
    assert "apikey=abc" not in result.output and "Traceback" not in result.output
