# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Plain-text summaries both entry points print (R3.2, R3.4).

The per-family line, the ``families=`` totals line (the scheduled refresh greps it), the
uncovered and untradeable lists and one line per refused or unavailable family. Pure: a
report in, lines out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from facdrone_data.acquisition.families.refresher import Refusal

if TYPE_CHECKING:
    from datetime import date

    from facdrone_data.acquisition.families.refresher import DownloadReport, RefusedFamily
    from facdrone_data.acquisition.status import DatasetStatus
    from facdrone_data.quality.ensemble import QualityReport


def resume_line(start: date, end: date) -> str:
    """The line an unranged download prints before it runs."""
    return f"resuming from the panel: {start} .. {end}"


def download_lines(report: DownloadReport) -> list[str]:
    """One line per family scope, the totals, then coverage gaps and refusals."""
    lines = [
        f"{row.writer} {row.scope}: fetched={row.fetched} skipped={row.skipped} "
        f"inserted={row.inserted} updated={row.updated} quarantined={row.quarantined} "
        f"provider_outcome={row.provider_outcome.value} cursor={row.cursor or '-'}"
        for row in report.summaries
    ]
    rows = report.summaries
    lines.append(
        f"families={len(rows)} fetched={sum(r.fetched for r in rows)} "
        f"inserted={sum(r.inserted for r in rows)} updated={sum(r.updated for r in rows)} "
        f"quarantined={sum(r.quarantined for r in rows)}"
    )
    lines.extend(f"uncovered_symbol={symbol}" for symbol in report.coverage.uncovered_symbols)
    lines.extend(
        f"untradeable_instrument={item.detail}" for item in report.coverage.untradeable_instruments
    )
    lines.extend(_refusal_line(item) for item in report.refused)
    return lines


def _refusal_line(item: RefusedFamily) -> str:
    if item.refusal is Refusal.UNAVAILABLE:
        return f"unavailable family={item.writer} extra={item.needs}"
    return f"refused family={item.writer} reason={item.needs}"


def quality_lines(report: QualityReport) -> list[str]:
    """One line per flagged row, then the score line."""
    lines = [
        f"finding {report.dataset} {report.symbol} {issue.label}: "
        f"consensus={issue.consensus} detectors={','.join(issue.detectors)}"
        for issue in report.issues
    ]
    lines.append(
        f"quality {report.dataset} {report.symbol}: score={report.quality_score} "
        f"findings={len(report.issues)}"
    )
    return lines


def status_lines(symbol: str, rows: tuple[DatasetStatus, ...]) -> list[str]:
    """One line per registered family: coverage span and gap count, absent families shown."""
    return [
        f"{symbol} {row.family} {row.table}: "
        + (
            f"present start={row.start or '-'} end={row.end or '-'} gaps={len(row.gaps)}"
            if row.present
            else "absent"
        )
        for row in rows
    ]
