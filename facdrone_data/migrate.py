# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The module's migration chain: upgrade, current, check and stamp (facdrone-data-manager 2.4).

The chain lives in :mod:`facdrone_data.migrations` and records its revision in its own version
table, so a consumer's own Alembic chain (and its ``alembic_version``) shares the database
without either reading the other's. No ``env.py`` is involved: the functions drive Alembic's
migration context on the engine they are given.

A database that already holds module tables but no version table was built some other way;
``upgrade`` refuses it, and adoption is explicit: ``check`` must report no difference before
``stamp`` records the baseline.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from facdrone_data.acquisition.schema import metadata

if TYPE_CHECKING:
    from sqlalchemy import Column, Connection, Engine, Inspector, Table
    from sqlalchemy.engine.interfaces import ReflectedColumn

VERSION_TABLE = "facdrone_data_alembic_version"
BASELINE = "fd0001_baseline"
_CHAIN = Path(__file__).resolve().parent / "migrations"


class AdoptionRequired(RuntimeError):
    """Module tables are present without the module's version table."""


@dataclass(frozen=True)
class MigrationResult:
    revision: str | None


@dataclass(frozen=True)
class SchemaDiff:
    """The live database against the module's declared tables."""

    missing_tables: tuple[str, ...] = ()
    missing_columns: tuple[str, ...] = ()
    unexpected_columns: tuple[str, ...] = ()
    differing_columns: tuple[str, ...] = ()  # type family or nullability
    missing_indexes: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not any(
            (
                self.missing_tables,
                self.missing_columns,
                self.unexpected_columns,
                self.differing_columns,
                self.missing_indexes,
            )
        )

    def lines(self) -> tuple[str, ...]:
        return tuple(
            f"{kind}={item}"
            for kind, items in (
                ("missing_table", self.missing_tables),
                ("missing_column", self.missing_columns),
                ("unexpected_column", self.unexpected_columns),
                ("differing_column", self.differing_columns),
                ("missing_index", self.missing_indexes),
            )
            for item in items
        )


def _script() -> ScriptDirectory:
    return ScriptDirectory(str(_CHAIN))


def _context(conn: Connection, **opts: object) -> MigrationContext:
    return MigrationContext.configure(conn, opts={"version_table": VERSION_TABLE, **opts})


def current(engine: Engine) -> str | None:
    """The module revision the database records, or ``None``."""
    with engine.connect() as conn:
        return _context(conn).get_current_revision()


def upgrade(engine: Engine, revision: str = "head") -> MigrationResult:
    """Apply the module's revisions up to ``revision``; refuse an unadopted database."""
    script = _script()
    with engine.begin() as conn:
        live = set(inspect(conn).get_table_names())
        if VERSION_TABLE not in live and live & set(metadata.tables):
            raise AdoptionRequired(
                "module tables exist without the module's version table: run "
                f"`facdrone-data db check`, then `facdrone-data db stamp {BASELINE}`"
            )
        # Alembic's own upgrade step, as ``alembic upgrade`` binds it inside an env.py.
        context = _context(
            conn, script=script, fn=lambda rev, _ctx: script._upgrade_revs(revision, rev)
        )
        with Operations.context(context):
            context.run_migrations()
    return MigrationResult(current(engine))


def stamp(engine: Engine, revision: str) -> MigrationResult:
    """Record ``revision`` without running it; only a database that matches the schema."""
    diff = check(engine)
    if not diff.empty:
        raise AdoptionRequired(
            "the database differs from the module schema: " + "; ".join(diff.lines())
        )
    with engine.begin() as conn:
        _context(conn).stamp(_script(), revision)
    return MigrationResult(current(engine))


def _family(type_: object) -> str:
    """A coarse type family, so the server's spelling of a declared type compares equal."""
    name = str(type_).lower()
    for family, words in (
        ("json", ("json",)),
        ("boolean", ("bool",)),
        ("datetime", ("timestamp", "datetime")),
        ("date", ("date",)),
        ("numeric", ("double", "float", "numeric", "real")),
        ("integer", ("int",)),
    ):
        if any(word in name for word in words):
            return family
    return "text" if any(word in name for word in ("char", "text", "string")) else name


def _differs(declared: Column[object], reflected: ReflectedColumn) -> bool:
    return _family(declared.type) != _family(reflected["type"]) or bool(declared.nullable) != bool(
        reflected["nullable"]
    )


def _column_diff(table: Table, found: Inspector) -> tuple[list[str], list[str], list[str]]:
    columns = {c["name"]: c for c in found.get_columns(table.name)}
    absent = [f"{table.name}.{c.name}" for c in table.columns if c.name not in columns]
    extra = [f"{table.name}.{c}" for c in sorted(columns) if c not in table.c]
    differing = [
        f"{table.name}.{c.name}"
        for c in table.columns
        if c.name in columns and _differs(c, columns[c.name])
    ]
    return absent, extra, differing


def check(engine: Engine) -> SchemaDiff:
    """Compare the live tables with the module's declaration: tables, columns, indexes."""
    found = inspect(engine)
    live = set(found.get_table_names())
    missing = [name for name in sorted(metadata.tables) if name not in live]
    absent: list[str] = []
    extra: list[str] = []
    differing: list[str] = []
    indexes: list[str] = []
    for name in sorted(set(metadata.tables) & live):
        table = metadata.tables[name]
        for bucket, items in zip(
            (absent, extra, differing), _column_diff(table, found), strict=True
        ):
            bucket.extend(items)
        present = {i["name"] for i in found.get_indexes(name)}
        indexes += [f"{name}.{i.name}" for i in table.indexes if i.name not in present]
    return SchemaDiff(*map(tuple, (missing, absent, extra, differing, indexes)))
