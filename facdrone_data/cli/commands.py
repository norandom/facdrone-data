# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""What each command does, independent of the option parser (R3.1, R3.2).

Both entry points call these: the module's typer app and the trading core's ``facdrone
data`` group. Each returns the lines to print and the exit code, so the two print the same
summaries by construction (R3.4).

Exit codes: 0 everything selected ran or no finding; 3 some family refused or unavailable;
2 quality findings or a usage error; 1 any other error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, TypeVar

import pandas as pd
from sqlalchemy import select

from facdrone_data.acquisition.schema import DAILY_PRICES, HOURLY_PRICES, INTRADAY_PRICES
from facdrone_data.acquisition.status import status_for
from facdrone_data.cli.render import download_lines, quality_lines, resume_line, status_lines
from facdrone_data.quality.ensemble import EnsembleValidator
from facdrone_data.runner import resolve_download_window

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy import Engine

    from facdrone_data.acquisition.families.refresher import DownloadReport
    from facdrone_data.runner import DownloadRequest

# Any request the runner accepts: the module's command or a consumer's richer one.
RequestT = TypeVar("RequestT", bound="DownloadRequest")
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_FINDINGS = 2
EXIT_REFUSED = 3
# The stored price tables the quality ensemble reads (each keyed by symbol, with a close).
QUALITY_TABLES = {table.name: table for table in (DAILY_PRICES, HOURLY_PRICES, INTRADAY_PRICES)}


@dataclass(frozen=True)
class Outcome:
    """The lines a command prints and the code it exits with."""

    lines: tuple[str, ...]
    code: int


def download_window(
    explicit: tuple[date | None, date | None],
    resume: Callable[[], date | None],
    today: date,
) -> tuple[tuple[date, date], str | None]:
    """The window to run and, when it was resumed from the panel, the line saying so."""
    start, end = explicit
    resumed = start is None and end is None
    coverage = resume() if resumed else None
    window = resolve_download_window(
        explicit_from=start, explicit_to=end, coverage_end=coverage, today=today
    )
    return window, resume_line(*window) if resumed else None


def run_download(download: Callable[[RequestT], DownloadReport], request: RequestT) -> Outcome:
    """Run one download and render its report; refused families make the exit code 3."""
    report = download(request)
    return Outcome(tuple(download_lines(report)), EXIT_REFUSED if report.refused else EXIT_OK)


@dataclass(frozen=True)
class QualityQuery:
    """Which stored rows the quality ensemble reads."""

    table: str
    symbol: str
    window: tuple[date, date]
    asset_class: str = ""


def run_quality(engine: Engine, query: QualityQuery) -> Outcome:
    """The quality ensemble over stored rows; any flagged row makes the exit code 2."""
    table = QUALITY_TABLES.get(query.table)
    if table is None:
        known = ", ".join(sorted(QUALITY_TABLES))
        return Outcome((f"ERROR: unknown --family {query.table!r}; choose {known}",), EXIT_FINDINGS)
    time_column = next(
        table.c[name] for name in ("date", "datetime", "timestamp") if name in table.c
    )
    statement = (
        select(table)
        .where(table.c.symbol == query.symbol)
        .where(time_column.between(query.window[0], query.window[1]))
        .order_by(time_column)
    )
    frame = pd.read_sql(statement, engine)
    report = EnsembleValidator().validate(
        frame, symbol=query.symbol, dataset=query.table, asset_class=query.asset_class
    )
    return Outcome(tuple(quality_lines(report)), EXIT_FINDINGS if report.issues else EXIT_OK)


def run_status(engine: Engine, symbol: str) -> Outcome:
    """Per-family coverage for one symbol from the stored panel; contacts no provider."""
    return Outcome(tuple(status_lines(symbol, status_for(engine, symbol))), EXIT_OK)
