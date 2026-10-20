# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Interfacer: validate the profile's continuous-futures construction declarations (R3.4)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime

# Re-export the task 2.4 API; domain profiles must not import a pipelines module.
from facdrone_data_types.series_declarations import Adjustment as Adjustment
from facdrone_data_types.series_declarations import Construction as Construction
from facdrone_data_types.series_declarations import RollGaps as RollGaps
from facdrone_data_types.series_declarations import SeriesDeclaration as SeriesDeclaration


class SeriesDeclarationError(ValueError):
    """A series' construction cannot be stated from its profile declaration."""


class UndeclaredSeriesError(SeriesDeclarationError):
    """Requested scoring series have no construction declaration."""


def _knowledge_date(raw: object) -> date:
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    raise ValueError(f"known_at must be an ISO date, got {raw!r}")


def load_series_declarations(block: Mapping[str, object]) -> Mapping[str, SeriesDeclaration]:
    """Bind an ``instruments.series`` section without IO, defaults or price adjustment."""
    if not isinstance(block, Mapping):
        raise SeriesDeclarationError("instruments.series must be a mapping")
    required = {"construction", "adjustment", "roll_gaps", "known_at"}
    declarations: dict[str, SeriesDeclaration] = {}
    for series, entry in block.items():
        if not isinstance(series, str) or not series.strip():
            raise SeriesDeclarationError(f"invalid series name {series!r}")
        if not isinstance(entry, Mapping):
            raise SeriesDeclarationError(f"{series}: declaration must be a mapping")
        if missing := required - entry.keys():
            raise SeriesDeclarationError(f"{series}: missing keys {sorted(missing)}")
        if unknown := entry.keys() - required - {"score_on"}:
            raise SeriesDeclarationError(f"{series}: unknown keys {sorted(map(str, unknown))}")
        try:
            declarations[series] = SeriesDeclaration(
                series=series,
                construction=Construction(entry["construction"]),
                adjustment=Adjustment(entry["adjustment"]),
                roll_gaps=RollGaps(entry["roll_gaps"]),
                known_at=_knowledge_date(entry["known_at"]),
                score_on=_score_on(entry.get("score_on")),
            )
        except (TypeError, ValueError) as error:
            raise SeriesDeclarationError(f"{series}: {error}") from error
    return declarations


def _score_on(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("score_on must name a canonical series")
    return value


def require_declared(series: Iterable[str], declarations: Mapping[str, SeriesDeclaration]) -> None:
    """Refuse admission with every missing name, for a futures consumer to call at composition."""
    if missing := sorted(set(series) - declarations.keys()):
        raise UndeclaredSeriesError(f"undeclared futures series: {', '.join(missing)}")
