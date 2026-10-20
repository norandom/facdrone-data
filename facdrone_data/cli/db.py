# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""``facdrone-data db``: the module's schema migrations (facdrone-data-manager 2.4).

Each command prints ``schema=<revision>``; ``check`` prints one line per difference and exits 1
when there is any; ``upgrade`` and ``stamp`` exit 1 when they refuse.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

import typer

from facdrone_data import migrate
from facdrone_data.cli.commands import EXIT_ERROR, Outcome

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy import Engine

    from facdrone_data.cli.compose import DataOptions


def _schema(revision: str | None) -> Outcome:
    return Outcome((f"schema={revision}",), 0)


def _refusing(action: Callable[[], Outcome]) -> Outcome:
    try:
        return action()
    except migrate.AdoptionRequired as refusal:
        return Outcome((f"ERROR: {refusal}",), EXIT_ERROR)


def _check(engine: Engine) -> Outcome:
    diff = migrate.check(engine)
    return Outcome(diff.lines() or ("schema matches",), 0 if diff.empty else EXIT_ERROR)


def db_commands(engine_for: Callable[[DataOptions], Engine]) -> typer.Typer:
    """The ``db`` group over the engine the run's options name."""
    app = typer.Typer(no_args_is_help=True, help="The module's schema migrations.")

    def emit(ctx: typer.Context, action: Callable[[Engine], Outcome]) -> None:
        outcome = action(engine_for(ctx.obj))
        for line in outcome.lines:
            typer.echo(line)
        if outcome.code:
            raise typer.Exit(outcome.code)

    @app.command("upgrade")
    def upgrade(ctx: typer.Context) -> None:
        """Create or migrate the module's tables to the newest revision."""
        emit(ctx, lambda e: _refusing(lambda: _schema(migrate.upgrade(e).revision)))

    @app.command("current")
    def current(ctx: typer.Context) -> None:
        """Print the module revision the database records."""
        emit(ctx, lambda e: _schema(migrate.current(e)))

    @app.command("check")
    def check(ctx: typer.Context) -> None:
        """Compare the database with the module schema; exit 1 on any difference."""
        emit(ctx, _check)

    @app.command("stamp")
    def stamp(
        ctx: typer.Context, revision: Annotated[str, typer.Argument(help="Revision id.")]
    ) -> None:
        """Record a revision without running it, only when ``check`` finds no difference."""
        emit(ctx, lambda e: _refusing(lambda: _schema(migrate.stamp(e, revision).revision)))

    return app
