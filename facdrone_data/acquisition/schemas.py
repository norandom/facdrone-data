# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Per-dataset declared schemas + a fetch-validation gate (task 5.1; R1.3, R1.4).

Each provider data family is a distinct NAMED dataset with its own declared
schema (columns, event-date and knowledge-date fields). The acquisition node
rejects a fetched frame that violates its declared schema with a loud failure at
the boundary, rather than silently dropping a ``None``/404 response.

pipelines tier: this binds the acquisition node's IO to a declarative contract;
it carries no quant math.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


class SchemaViolationError(Exception):
    """Raised when a fetched frame violates its dataset's declared schema."""


@dataclass(frozen=True)
class DatasetSchema:
    """The declared shape of one provider data family (R1.3, R1.4)."""

    name: str
    required_columns: frozenset[str]
    event_date_column: str
    knowledge_date_column: str | None


# The declarative registry of dataset families. New providers/families add an
# entry here, not a bespoke download module (R1.2).
DATASET_SCHEMAS: dict[str, DatasetSchema] = {
    "daily_prices": DatasetSchema(
        name="daily_prices",
        required_columns=frozenset(
            {
                "symbol",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "event_date",
                "knowledge_date",
            }
        ),
        event_date_column="event_date",
        knowledge_date_column="knowledge_date",
    ),
}


def validate_frame(frame: pd.DataFrame, schema: DatasetSchema) -> pd.DataFrame:
    """Return ``frame`` unchanged if it conforms to ``schema``; else raise.

    The check is loud by design (R1.3): missing columns, or a non-date-like
    event-date OR knowledge-date column, fail the acquisition rather than
    silently storing a malformed panel. knowledge_date is checked because it is
    the field the point-in-time view filters on (R3.6) — a malformed value must
    fail at the boundary, not mis-filter downstream."""
    missing = schema.required_columns - set(frame.columns)
    if missing:
        raise SchemaViolationError(
            f"dataset {schema.name!r} frame missing columns: {sorted(missing)}"
        )
    _require_date_like(frame, schema.event_date_column, schema.name)
    if schema.knowledge_date_column is not None:
        _require_date_like(frame, schema.knowledge_date_column, schema.name)
    return frame


def _require_date_like(frame: pd.DataFrame, column: str, dataset_name: str) -> None:
    try:
        parsed = pd.to_datetime(frame[column])
    except (ValueError, TypeError) as exc:
        raise SchemaViolationError(
            f"dataset {dataset_name!r} column {column!r} is not date-like: {exc}"
        ) from exc
    # pd.to_datetime coerces None to NaT without raising, so an all-/partly-null
    # date column would slip through; reject it (esp. for the PIT field, R3.6).
    if len(parsed) and bool(parsed.isna().any()):
        raise SchemaViolationError(
            f"dataset {dataset_name!r} column {column!r} has null/unparseable dates"
        )
