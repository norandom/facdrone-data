# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Pytest plugin: an embedded PostgreSQL per test process, migrated by the module's chain.

Unless ``FACDRONE_TEST_PG_DSN`` names a database, each test process (each xdist worker) starts
its own server from the ``pgembed`` wheel, and ``pg_engine`` is that server brought to the
module's head before every test that uses it. An exported DSN is used as found, never migrated:
point it at a throwaway database only, because the tests empty and drop tables. No container
is started.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, Table, create_engine, inspect, text

from facdrone_data import migrate
from facdrone_data.acquisition.schema import DAILY_PRICES, metadata
from facdrone_data.testing.pg_server import EmbeddedPostgres, start

PG_DSN_ENV = "FACDRONE_TEST_PG_DSN"
_SERVER = pytest.StashKey[EmbeddedPostgres]()


def pytest_configure(config: pytest.Config) -> None:
    exported = os.environ.get(PG_DSN_ENV, "").strip()
    controller = not hasattr(config, "workerinput") and bool(config.getoption("numprocesses", 0))
    if exported or os.geteuid() == 0 or controller:
        return  # an exported database; PostgreSQL refuses root; the xdist workers start theirs
    config.stash[_SERVER] = start()


def pytest_unconfigure(config: pytest.Config) -> None:
    server = config.stash.get(_SERVER, None)
    if server is not None:
        server.stop()


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """The ``pg_engine`` tests share one database: under xdist they run on one worker."""
    group = pytest.mark.xdist_group("pg_engine")
    for item in items:
        if "pg_engine" in getattr(item, "fixturenames", ()):
            item.add_marker(group)


def rebuild(engine: Engine) -> None:
    """An empty ``public`` schema brought to the module's head."""
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    migrate.upgrade(engine)


def _migrated(engine: Engine) -> bool:
    live = set(inspect(engine).get_table_names())
    return migrate.VERSION_TABLE in live and set(metadata.tables) <= live


@pytest.fixture(scope="session")
def pg_engine(pytestconfig: pytest.Config) -> Iterator[Engine]:
    """This process's embedded server, or the exported throwaway database."""
    server = pytestconfig.stash.get(_SERVER, None)
    dsn = server.dsn if server is not None else os.environ.get(PG_DSN_ENV, "").strip()
    if not dsn:
        pytest.skip(f"{PG_DSN_ENV} unset and no embedded server (root shell?)")
    engine = create_engine(dsn)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def module_schema(request: pytest.FixtureRequest) -> None:
    """Before each ``pg_engine`` test on the embedded server: the module's migrated schema,
    rebuilt when an earlier test dropped it, so no test depends on the one before it."""
    if "pg_engine" not in request.fixturenames or request.config.stash.get(_SERVER, None) is None:
        return
    engine: Engine = request.getfixturevalue("pg_engine")
    if not _migrated(engine):
        rebuild(engine)


def empty_tables(engine: Engine, *tables: Table) -> None:
    """Empty ``tables`` as freshly created ones would be (identities restarted)."""
    for table in tables:
        table.create(engine, checkfirst=True)
    names = ", ".join(f'"{table.name}"' for table in tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY"))


@pytest.fixture
def prices_engine(pg_engine: Engine) -> Iterator[Engine]:
    """An empty ``daily_prices`` table, emptied before and after the test."""
    empty_tables(pg_engine, DAILY_PRICES)
    try:
        yield pg_engine
    finally:
        empty_tables(pg_engine, DAILY_PRICES)
