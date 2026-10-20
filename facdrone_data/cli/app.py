# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The ``facdrone-data`` command line (R3.1, R3.2): one typer app over :mod:`commands`.

``compose_data_cli`` is the seam: it takes the backend factory and the clock, so the
module's ``main`` binds the declared universe and a test binds fakes, and every command
prints through the same renderer.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from facdrone_data.cli.commands import (
    EXIT_ERROR,
    EXIT_FINDINGS,
    Outcome,
    QualityQuery,
    download_window,
    run_download,
    run_quality,
    run_status,
)
from facdrone_data.cli.compose import DataBackend, DataOptions, live_backend
from facdrone_data.cli.db import db_commands
from facdrone_data.credentials import database_url, process_environment, redact
from facdrone_data.runner import DownloadCommand

if TYPE_CHECKING:
    from collections.abc import Callable

_DATE = "Date (YYYY-MM-DD)."


def _emit(outcome: Outcome) -> None:
    for line in outcome.lines:
        typer.echo(line)
    if outcome.code:
        raise typer.Exit(outcome.code)


def _guarded(action: Callable[[], Outcome]) -> None:
    """Run a command: usage errors exit 2, any other failure exits 1 with redacted text."""
    try:
        outcome = action()
    except ValueError as error:
        outcome = Outcome((f"ERROR: {redact(str(error), ())}",), EXIT_FINDINGS)
    except Exception as error:  # noqa: BLE001 — a command reports, it never dumps a trace
        outcome = Outcome((f"ERROR: {type(error).__name__}: {redact(str(error), ())}",), EXIT_ERROR)
    _emit(outcome)


def _day(text: str | None) -> date | None:
    return None if text is None else date.fromisoformat(text)


def compose_data_cli(
    backend: Callable[[DataOptions], DataBackend], clock: Callable[[], datetime]
) -> typer.Typer:
    """The five data commands over one backend factory and one clock."""
    app = typer.Typer(no_args_is_help=True, help="A point-in-time market-data store.")

    @app.callback()
    def global_options(
        ctx: typer.Context,
        config: Annotated[Path | None, typer.Option("--config", help="Configuration file.")] = None,
        dsn: Annotated[str | None, typer.Option("--dsn", help="PostgreSQL URL.")] = None,
        credentials: Annotated[
            Path | None, typer.Option("--credentials", help="Credential file.")
        ] = None,
    ) -> None:
        ctx.obj = DataOptions(config=config, dsn=dsn, credentials=credentials)

    _download_commands(app, backend, clock)
    _window_commands(app, backend)
    _panel_commands(app, backend)
    return app


def _download_commands(
    app: typer.Typer, backend: Callable[[DataOptions], DataBackend], clock: Callable[[], datetime]
) -> None:
    @app.command("download")
    def download(
        ctx: typer.Context,
        source: Annotated[str | None, typer.Option("--source", help="Family selector.")] = None,
        from_date: Annotated[str | None, typer.Option("--from", help=_DATE)] = None,
        to_date: Annotated[str | None, typer.Option("--to", help=_DATE)] = None,
        exclude: Annotated[list[str] | None, typer.Option("--exclude-family")] = None,
    ) -> None:
        """Resume from the panel, or download the explicit window, for the configured universe."""

        def _run() -> Outcome:
            bound = backend(ctx.obj)
            window, resumed = download_window(
                (_day(from_date), _day(to_date)), bound.resume, clock().date()
            )
            cmd = DownloadCommand(source or bound.default_source, *window, tuple(exclude or ()))
            outcome = run_download(bound.download, cmd)
            return Outcome((resumed, *outcome.lines) if resumed else outcome.lines, outcome.code)

        _guarded(_run)


def _window_commands(app: typer.Typer, backend: Callable[[DataOptions], DataBackend]) -> None:
    @app.command("refetch")
    def refetch(
        ctx: typer.Context,
        from_date: Annotated[str, typer.Option("--from", help=_DATE)],
        to_date: Annotated[str, typer.Option("--to", help=_DATE)],
        family: Annotated[list[str], typer.Option("--family", help="Dataset or writer.")],
        source: Annotated[str, typer.Option("--source", help="Family selector.")] = "all",
    ) -> None:
        """Re-request the whole explicit window for the named families."""
        _guarded(
            lambda: run_download(
                backend(ctx.obj).download,
                DownloadCommand(
                    source,
                    date.fromisoformat(from_date),
                    date.fromisoformat(to_date),
                    families=tuple(family),
                    refetch=True,
                ),
            )
        )

    @app.command("update")
    def update(
        ctx: typer.Context,
        from_date: Annotated[str, typer.Option("--from", help=_DATE)],
        to_date: Annotated[str, typer.Option("--to", help=_DATE)],
        source: Annotated[str | None, typer.Option("--source", help="Family selector.")] = None,
    ) -> None:
        """Download an explicit window through the same runner, without resuming."""

        def _run() -> Outcome:
            bound = backend(ctx.obj)
            window = (date.fromisoformat(from_date), date.fromisoformat(to_date))
            return run_download(
                bound.download, DownloadCommand(source or bound.default_source, *window)
            )

        _guarded(_run)


def _panel_commands(app: typer.Typer, backend: Callable[[DataOptions], DataBackend]) -> None:
    @app.command("quality")
    def quality(
        ctx: typer.Context,
        symbol: Annotated[str, typer.Option("--symbol", help="Canonical symbol.")],
        from_date: Annotated[str, typer.Option("--from", help=_DATE)],
        to_date: Annotated[str, typer.Option("--to", help=_DATE)],
        family: Annotated[
            str, typer.Option("--family", help="Stored price table.")
        ] = "daily_prices",
    ) -> None:
        """Run the quality ensemble over stored rows; exit 2 when it flags any row."""
        window = (date.fromisoformat(from_date), date.fromisoformat(to_date))
        query = QualityQuery(family, symbol, window)
        _guarded(lambda: run_quality(backend(ctx.obj).engine(), query))

    @app.command("status")
    def status(
        ctx: typer.Context,
        symbol: Annotated[str, typer.Argument(help="Canonical symbol id.")],
    ) -> None:
        """Per-family coverage for a symbol from the stored panel; no provider is contacted."""
        _guarded(lambda: run_status(backend(ctx.obj).engine(), symbol))


def main() -> None:
    """Console script ``facdrone-data``: the declared universe from the configuration file."""
    environ = process_environment()

    def clock() -> datetime:
        return datetime.now(UTC)

    from sqlalchemy import create_engine

    app = compose_data_cli(lambda options: live_backend(options, environ, clock), clock)
    app.add_typer(
        db_commands(lambda options: create_engine(database_url(options.dsn, environ))), name="db"
    )
    app()
