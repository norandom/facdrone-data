# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The documents (facdrone-data-manager 3.5; R5.2, R8.1, R8.3, R8.4, R9.4).

The README quickstart runs as written on the embedded database, with the provider clients
replaced by the fakes the download tests use, so no request leaves the machine. The worked
point-in-time examples execute, and the documents stay generic.
"""

from __future__ import annotations

import contextlib
import os
import re
import shlex
import sys
import types
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest
import typer
from sqlalchemy import Engine, create_engine, text
from typer.testing import CliRunner

from facdrone_data.cli.app import compose_data_cli
from facdrone_data.cli.compose import live_backend
from facdrone_data.testing.fakes import FakeMarketDataClient
from facdrone_data_types.ports import MarketDataClient
from facdrone_data_types.symbols import Provider

MODULE = Path(__file__).resolve().parents[1]
README = MODULE / "README.md"
DOCUMENTS = (README, MODULE / "CHANGELOG.md", *sorted((MODULE / "docs").glob("*.md")))
TODAY = datetime(2025, 1, 11, 12, tzinfo=UTC)
FENCE = re.compile(r"```(bash|python)\n(.*?)```", re.DOTALL)
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
        result = CliRunner().invoke(self.app, arguments)
        assert result.exit_code == 0, self.scrub(result.output)
        self.printed += result.output

    def command(self, line: str) -> None:
        words = shlex.split(line)
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
    totals = [line for line in run.printed.splitlines() if line.startswith("families=")]
    assert totals, run.scrub(run.printed)
    inserted = re.search(r"inserted=(\d+)", totals[0])
    assert inserted is not None and int(inserted.group(1)) > 0
    # The query block reads as of 2025-01-08: the window's first days are in, the 9th is out.
    assert "2025-01-06" in queried and "2025-01-08" in queried, run.scrub(queried)
    assert "2025-01-09" not in queried and "2025-01-10" not in queried


def test_the_readme_names_only_commands_the_command_line_has() -> None:
    app = compose_data_cli(lambda options: live_backend(options, {}, _clock), _clock)
    help_text = CliRunner().invoke(app, ["--help"]).output
    for command in ("download", "refetch", "update", "quality", "status"):
        assert command in help_text
    if "upgrade" not in help_text:  # until the module's migration command lands
        assert "does not exist yet" in README.read_text(encoding="utf-8")


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
