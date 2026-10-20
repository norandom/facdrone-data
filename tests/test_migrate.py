# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The module's own migration chain and its adoption rules (facdrone-data-manager 2.2, 2.4)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, inspect, text
from typer.testing import CliRunner

from facdrone_data import migrate
from facdrone_data.acquisition.schema import metadata
from facdrone_data.cli.compose import DataOptions
from facdrone_data.cli.db import db_commands


@pytest.fixture
def empty(pg_engine: Engine) -> Iterator[Engine]:
    def reset() -> None:
        with pg_engine.begin() as conn:
            conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))

    reset()
    yield pg_engine
    reset()


def _tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def test_upgrade_creates_every_table_and_records_the_baseline(empty: Engine) -> None:
    assert migrate.upgrade(empty).revision == migrate.BASELINE
    assert _tables(empty) == {*metadata.tables, migrate.VERSION_TABLE}
    assert migrate.current(empty) == migrate.BASELINE
    assert migrate.check(empty).empty
    with empty.connect() as conn:
        seeded = conn.execute(text("SELECT factor_id FROM factor_definitions")).scalars()
        assert sorted(seeded) == ["HML_EU", "MKT_EU", "MOM_EU", "SMB_EU"]
    assert migrate.upgrade(empty).revision == migrate.BASELINE  # at head: a no-op


def test_upgrade_refuses_module_tables_without_the_version_table(empty: Engine) -> None:
    metadata.tables["symbols"].create(empty)
    with pytest.raises(migrate.AdoptionRequired, match="db check.*db stamp fd0001_baseline"):
        migrate.upgrade(empty)
    assert migrate.VERSION_TABLE not in _tables(empty)


def test_check_names_each_difference_and_stamp_refuses_it(empty: Engine) -> None:
    metadata.create_all(empty)
    with empty.begin() as conn:
        conn.execute(text("ALTER TABLE contract_specs RENAME COLUMN venue_symbol TO legacy_symbol"))
        conn.execute(text("DROP TABLE fred_series"))
    diff = migrate.check(empty)
    assert diff.missing_tables == ("fred_series",)
    assert diff.missing_columns == ("contract_specs.venue_symbol",)
    assert diff.unexpected_columns == ("contract_specs.legacy_symbol",)
    with pytest.raises(migrate.AdoptionRequired, match="missing_table=fred_series"):
        migrate.stamp(empty, migrate.BASELINE)
    assert migrate.current(empty) is None


def test_stamp_adopts_a_database_whose_tables_match(empty: Engine) -> None:
    metadata.create_all(empty)
    assert migrate.check(empty).empty
    assert migrate.stamp(empty, migrate.BASELINE).revision == migrate.BASELINE
    assert migrate.upgrade(empty).revision == migrate.BASELINE


def test_db_commands_print_the_revision_and_exit_1_on_a_difference(empty: Engine) -> None:
    app = db_commands(lambda _options: empty)
    runner = CliRunner()
    upgraded = runner.invoke(app, ["upgrade"], obj=DataOptions())
    assert (upgraded.exit_code, upgraded.output) == (0, "schema=fd0001_baseline\n")
    assert runner.invoke(app, ["current"], obj=DataOptions()).output == "schema=fd0001_baseline\n"
    assert runner.invoke(app, ["check"], obj=DataOptions()).output == "schema matches\n"
    with empty.begin() as conn:
        conn.execute(text("DROP INDEX ix_fx_observation_ccy_event_known"))
    checked = runner.invoke(app, ["check"], obj=DataOptions())
    assert checked.exit_code == 1
    assert checked.output == "missing_index=fx_observation.ix_fx_observation_ccy_event_known\n"
    refused = runner.invoke(app, ["stamp", "fd0001_baseline"], obj=DataOptions())
    assert refused.exit_code == 1 and refused.output.startswith("ERROR: the database differs")
