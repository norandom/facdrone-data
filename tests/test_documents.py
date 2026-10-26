# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The documents (facdrone-data-manager 3.5; R5.2, R8.1, R8.3, R8.4, R9.4).

The README quickstart runs as written on the embedded database, with the provider clients
replaced by the fakes the download tests use, so no request leaves the machine. Its ``db``
commands and the adoption path the README describes go through the console script's own
``main``; no test reaches a database other than the embedded server's. The worked
point-in-time examples execute, and the documents stay generic.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shlex
import sys
import tomllib
import types
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
import typer
from sqlalchemy import Engine, create_engine, inspect, text
from typer.testing import CliRunner

from facdrone_data import migrate
from facdrone_data.acquisition.schema import metadata
from facdrone_data.cli.app import compose_data_cli, main
from facdrone_data.cli.compose import live_backend
from facdrone_data.testing.fakes import FakeMarketDataClient
from facdrone_data_types.ports import MarketDataClient
from facdrone_data_types.symbols import Provider

MODULE = Path(__file__).resolve().parents[1]
README = MODULE / "README.md"
DOCUMENTS = (README, MODULE / "CHANGELOG.md", *sorted((MODULE / "docs").glob("*.md")))
TODAY = datetime(2025, 1, 11, 12, tzinfo=UTC)
FENCE = re.compile(r"```(bash|python)\n(.*?)```", re.DOTALL)
# A command in a code block (at the start of a line) or in running text (after a backtick).
COMMAND = re.compile(r"(?:^|`)facdrone-data ([a-z]+(?: [a-z]+)?)", re.MULTILINE)
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")
# Written in pieces so this file is not itself a hit for the literal scan.
FORBIDDEN = ("ft" + "mo", "/ho" + "me/", "man" + "date", "bro" + "ker")


def _clock() -> datetime:
    return TODAY


def _quickstart_blocks() -> list[tuple[str, str]]:
    text_ = README.read_text(encoding="utf-8")
    start = text_.index("## Quickstart")
    end = text_.index("\n## ", start + 1)
    return FENCE.findall(text_[start:end])


@pytest.fixture
def quickstart_database(pg_engine: Engine) -> Iterator[tuple[str, str]]:
    """A new empty database on the embedded server: its name and its URL."""
    name = f"quickstart_{uuid.uuid4().hex[:8]}"
    admin = create_engine(pg_engine.url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield name, pg_engine.url.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _readme() -> str:
    return README.read_text(encoding="utf-8")


def _entry_point(arguments: list[str]) -> tuple[int, str]:
    """``facdrone-data <arguments>`` through the console script's ``main``, in process."""
    printed = io.StringIO()
    with pytest.MonkeyPatch.context() as patch, contextlib.redirect_stdout(printed):
        patch.setattr(sys, "argv", ["facdrone-data", *arguments])
        with pytest.raises(SystemExit) as exited:
            main()
    code = exited.value.code
    return (code if isinstance(code, int) else 1), printed.getvalue()


def _fake_clients(_credentials: object) -> dict[Provider, MarketDataClient]:
    return cast(
        "dict[Provider, MarketDataClient]",
        {provider: FakeMarketDataClient(provider) for provider in Provider},
    )


class _Quickstart:
    """Runs the README quickstart blocks in order and records what they print."""

    def __init__(self, dsn: str, monkeypatch: pytest.MonkeyPatch, app: typer.Typer) -> None:
        self.dsn = dsn
        self.monkeypatch = monkeypatch
        self.app = app
        self.commands: list[str] = []
        self.printed = ""

    def scrub(self, output: str) -> str:
        return output.replace(self.dsn, "<dsn>")

    def createdb(self, arguments: list[str]) -> None:
        assert arguments, "createdb names the database"

    def export(self, arguments: list[str]) -> None:
        variable, _, value = arguments[0].partition("=")
        self.monkeypatch.setenv(variable, self.dsn if variable == "FACDRONE_DATA_DSN" else value)

    def facdrone_data(self, arguments: list[str]) -> None:
        if arguments[:1] == ["db"]:  # the db group is wired by main, as the console script runs
            code, output = _entry_point(arguments)
        else:  # the download commands on the fake clients and the fixed clock
            result = CliRunner().invoke(self.app, arguments)
            code, output = result.exit_code, result.output
        assert code == 0, self.scrub(output)
        self.printed += output

    def command(self, line: str) -> None:
        words = shlex.split(line, comments=True)
        handlers = {
            "createdb": self.createdb,
            "export": self.export,
            "facdrone-data": self.facdrone_data,
        }
        if words and words[0] not in handlers:
            pytest.fail(f"the quickstart runs a command the test does not know: {words[0]}")
        if words:
            self.commands.append(words[0])
            handlers[words[0]](words[1:])

    def shell(self, body: str) -> None:
        for line in body.splitlines():
            self.command(line.strip())


def _run_python_block(body: str, capsys: pytest.CaptureFixture[str]) -> str:
    exec(compile(body, "<readme>", "exec"), {"__name__": "__readme__"})  # noqa: S102
    return capsys.readouterr().out


def test_the_quickstart_runs_as_written(
    quickstart_database: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _name, dsn = quickstart_database
    monkeypatch.chdir(MODULE)
    for variable in ("FRED_API_KEY", "FMP_API_KEY", "FACDRONE_DATA_CREDENTIALS_FILE"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr("facdrone_data.providers.live_clients.live_clients", _fake_clients)
    app = compose_data_cli(lambda options: live_backend(options, dict(os.environ), _clock), _clock)

    run = _Quickstart(dsn, monkeypatch, app)
    queried = ""
    for language, body in _quickstart_blocks():
        if language == "python":
            queried += _run_python_block(body, capsys)
        else:
            run.shell(body)

    assert run.commands[:1] == ["createdb"]
    # `db upgrade` and `db current` print the revision, recorded in the store's own table.
    assert run.printed.count(f"schema={migrate.BASELINE}\n") == 2, run.scrub(run.printed)
    assert f"`{migrate.VERSION_TABLE}`" in _readme()
    engine = create_engine(dsn)
    try:
        assert set(inspect(engine).get_table_names()) >= {*metadata.tables, migrate.VERSION_TABLE}
        with engine.connect() as connection:
            recorded = connection.execute(
                text("SELECT version_num FROM facdrone_data_alembic_version")
            )
            assert recorded.scalars().all() == [migrate.BASELINE]
    finally:
        engine.dispose()
    totals = [line for line in run.printed.splitlines() if line.startswith("families=")]
    assert totals, run.scrub(run.printed)
    inserted = re.search(r"inserted=(\d+)", totals[0])
    assert inserted is not None and int(inserted.group(1)) > 0
    # The query block reads as of 2025-01-08: the window's first days are in, the 9th is out.
    assert "2025-01-06" in queried and "2025-01-08" in queried, run.scrub(queried)
    assert "2025-01-09" not in queried and "2025-01-10" not in queried


def _commands(help_text: str) -> set[str]:
    """The command names of a typer help screen's Commands panel.

    Typer forces a styled terminal when ``GITHUB_ACTIONS`` is set (read at import), so the
    hosted runner's help text carries ANSI escapes; they are stripped before the panel is read.
    """
    panel = ANSI_ESCAPE.sub("", help_text).split("Commands", 1)[1]
    return set(re.findall(r"^\W+([a-z]+)\s", panel, re.MULTILINE))


def test_the_readme_names_only_commands_the_command_line_has() -> None:
    code, top = _entry_point(["--help"])
    assert code == 0
    _, db = _entry_point(["db", "--help"])
    named = set(COMMAND.findall(_readme()))
    assert {"download", "status", "db upgrade", "db current", "db check", "db stamp"} <= named
    for command in named:
        group, _, sub = command.partition(" ")
        assert group in _commands(top), command
        assert not sub or sub in _commands(db), command


def test_the_readme_adoption_path_runs(
    quickstart_database: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Store tables without the version table: refused, then `db check` and `db stamp`."""
    readme = _readme()
    for written in ("`facdrone-data db check`", f"`facdrone-data db stamp {migrate.BASELINE}`"):
        assert written in readme
    _name, dsn = quickstart_database
    monkeypatch.setenv("FACDRONE_DATA_DSN", dsn)
    engine = create_engine(dsn)
    try:
        metadata.create_all(engine)
    finally:
        engine.dispose()

    code, refused = _entry_point(["db", "upgrade"])
    assert code == 1 and "db check" in refused and f"db stamp {migrate.BASELINE}" in refused
    assert _entry_point(["db", "check"]) == (0, "schema matches\n")
    adopted = (0, f"schema={migrate.BASELINE}\n")
    assert _entry_point(["db", "stamp", migrate.BASELINE]) == adopted
    assert _entry_point(["db", "current"]) == adopted


def test_the_readme_states_the_schema_and_the_testing_extra() -> None:
    readme = _readme()
    stated = re.search(r"The schema has (\d+) tables", readme)
    assert stated is not None and int(stated.group(1)) == len(metadata.tables)
    for table in ("contract_specs", "instrument_metadata_version"):
        assert "venue_symbol" in metadata.tables[table].primary_key.columns
    plugin = 'pytest_plugins = ["facdrone_data.testing.plugin"]'
    assert plugin in readme
    assert plugin in (MODULE / "tests" / "conftest.py").read_text(encoding="utf-8")
    adds = re.search(r"^\| `testing` \| (.*?) \|", readme, re.MULTILINE)
    assert adds is not None
    project = tomllib.loads((MODULE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    extra = {
        re.split(r"[<>=~ \[]", item)[0] for item in project["optional-dependencies"]["testing"]
    }
    assert set(re.findall(r"`([\w-]+)`", adds.group(1))) == extra


def test_the_family_guide_names_store_tables() -> None:
    guide = (MODULE / "docs" / "adding_a_family.md").read_text(encoding="utf-8")
    review_rule = guide[guide.index("Review rule") : guide.index("## 2.")]
    named = re.findall(r"`([a-z_]+)`", review_rule.split("repository):", 1)[1])
    assert named and set(named) <= set(metadata.tables), set(named) - set(metadata.tables)


def test_the_point_in_time_examples_run() -> None:
    document = (MODULE / "docs" / "point_in_time.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```python\n(.*?)```", document, re.DOTALL)
    assert len(blocks) == 4, "one worked example per family kind"
    for number, block in enumerate(blocks, start=1):
        module = types.ModuleType(f"example{number}")
        sys.modules[module.__name__] = module  # a dataclass resolves its module by name
        try:
            with contextlib.redirect_stdout(None):
                exec(compile(block, f"<example {number}>", "exec"), module.__dict__)  # noqa: S102
        finally:
            del sys.modules[module.__name__]


def test_the_documents_exist_and_link_to_each_other() -> None:
    readme = README.read_text(encoding="utf-8")
    for target in re.findall(r"`((?:docs/)?[\w./-]+\.md)`", readme):
        assert (MODULE / target).exists(), target
    for expected in ("point_in_time", "adding_a_family", "credentials", "versioning"):
        assert (MODULE / "docs" / f"{expected}.md").exists()
    assert "Apache-2.0" in readme and "not published to PyPI" in readme


@pytest.mark.parametrize("document", DOCUMENTS, ids=lambda path: path.name)
def test_the_documents_are_generic(document: Path) -> None:
    body = document.read_text(encoding="utf-8").lower()
    assert [word for word in FORBIDDEN if word in body] == []
