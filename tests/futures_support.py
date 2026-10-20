# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Generic futures fixtures for the module tests: identity files and a synthetic contract table."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml
from sqlalchemy import Engine, select

from facdrone_data.acquisition.schema import FUTURES_CONTRACT_SPECS, INSTRUMENT_METADATA_VERSION

FIXTURES = Path(__file__).resolve().parent / "fixtures"
IDENTITY_PATHS = (FIXTURES / "identities" / "spot.yml", FIXTURES / "identities" / "contracts.yml")
FIRST = datetime(2026, 9, 23, 12, tzinfo=UTC)


@pytest.fixture
def seeded_frame() -> pd.DataFrame:
    """The synthetic contract table, as the catalog hands it to the loader."""
    rows = yaml.safe_load((FIXTURES / "futures_contract_specs.yml").read_text(encoding="utf-8"))
    return pd.DataFrame.from_records(rows)


@pytest.fixture
def versions_engine(pg_engine: Engine) -> Iterator[Engine]:
    tables = (FUTURES_CONTRACT_SPECS, INSTRUMENT_METADATA_VERSION)
    for table in tables:
        table.drop(pg_engine, checkfirst=True)
        table.create(pg_engine)
    yield pg_engine
    for table in reversed(tables):
        table.drop(pg_engine, checkfirst=True)


def history(engine: Engine) -> list[dict[str, Any]]:
    """The metadata history, ordered by symbol and knowledge time."""
    versions = INSTRUMENT_METADATA_VERSION
    with engine.connect() as conn:
        rows = conn.execute(select(versions).order_by(versions.c.venue_symbol, versions.c.known_at))
        return [dict(row) for row in rows.mappings()]
