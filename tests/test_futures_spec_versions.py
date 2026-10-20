# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""R4.5: catalog mirrors retain append-only, point-in-time futures economics."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import Engine, select

from facdrone_data.acquisition.futures_spec_versions import (
    load_futures_spec_version,
    mirror_futures_contract_specs,
)
from facdrone_data.acquisition.futures_specs import load_futures_contract_specs
from facdrone_data.acquisition.instrument_table import InstrumentTable
from facdrone_data.acquisition.schema import FUTURES_CONTRACT_SPECS
from futures_support import FIRST, IDENTITY_PATHS
from futures_support import history as _history
from futures_support import seeded_frame as seeded_frame
from futures_support import versions_engine as versions_engine

SECOND = FIRST + timedelta(days=1)


def test_first_mirror_versions_every_catalog_row_and_unchanged_is_idempotent(
    versions_engine: Engine, seeded_frame: pd.DataFrame
) -> None:
    identities = InstrumentTable(IDENTITY_PATHS)
    contracts = load_futures_contract_specs(seeded_frame).rows
    assert mirror_futures_contract_specs(versions_engine, seeded_frame, identities, FIRST) == len(
        contracts
    )
    before = _history(versions_engine)
    assert len(before) == len(contracts)
    assert {row["revision_id"] for row in before} == {"1"}
    assert {row["known_at"] for row in before} == {FIRST}
    assert all(row["volume_min"] is None and row["contract_size"] is None for row in before)
    with versions_engine.connect() as conn:
        mirror = {
            row["symbol"]: dict(row)
            for row in conn.execute(select(FUTURES_CONTRACT_SPECS)).mappings()
        }
        for contract in contracts:
            expected = asdict(contract)
            expected["parent_symbol"] = expected.pop("parent_series")
            assert mirror[contract.symbol] == expected
            version = load_futures_spec_version(conn, contract.symbol, FIRST)
            assert version is not None and version.contract == contract
            assert version.revision_id == "1" and version.known_at == FIRST
        assert load_futures_spec_version(conn, "MES", FIRST - timedelta(microseconds=1)) is None
        assert load_futures_spec_version(conn, "UNKNOWN", FIRST) is None
    assert mirror_futures_contract_specs(versions_engine, seeded_frame, identities, FIRST) == 0
    assert mirror_futures_contract_specs(versions_engine, seeded_frame, identities, SECOND) == 0
    assert _history(versions_engine) == before


@pytest.mark.parametrize(
    "changes",
    [
        {"commission_per_side": 0.35},
        {"multiplier": 10.0, "tick_value": 2.5},
        {"tick_size": 0.5, "tick_value": 2.5},
        {"fees_per_side": 1.25, "initial_margin": 1000.0},
        {"session_close": "16:00"},
    ],
)
def test_changed_payload_appends_with_knowledge_date_and_preserves_history(
    versions_engine: Engine, seeded_frame: pd.DataFrame, changes: dict[str, float | str]
) -> None:
    identities = InstrumentTable(IDENTITY_PATHS)
    mirror_futures_contract_specs(versions_engine, seeded_frame, identities, FIRST)
    before = _history(versions_engine)
    changed = seeded_frame.copy()
    for field, value in changes.items():
        changed.loc[changed["symbol"] == "MES", field] = value
    assert mirror_futures_contract_specs(versions_engine, changed, identities, SECOND) == 1
    history = _history(versions_engine)
    assert [row for row in history if row["known_at"] == FIRST] == before
    assert len(history) == len(before) + 1
    with versions_engine.connect() as conn:
        earlier = load_futures_spec_version(conn, "MES", SECOND - timedelta(microseconds=1))
        later = load_futures_spec_version(conn, "MES", SECOND)
        assert earlier is not None and later is not None
        assert earlier.revision_id == "1" and earlier.known_at == FIRST
        assert later.revision_id == "2" and later.known_at == SECOND
        for field, value in changes.items():
            assert getattr(earlier.contract, field) != value
            assert getattr(later.contract, field) == value
        current = (
            conn.execute(
                select(FUTURES_CONTRACT_SPECS).where(FUTURES_CONTRACT_SPECS.c.symbol == "MES")
            )
            .mappings()
            .one()
        )
        assert all(current[field] == value for field, value in changes.items())
    assert mirror_futures_contract_specs(versions_engine, changed, identities, SECOND) == 0


@pytest.mark.parametrize("at", [FIRST, FIRST - timedelta(days=1)])
def test_conflicting_or_backdated_write_rolls_back_the_entire_catalog(
    versions_engine: Engine, seeded_frame: pd.DataFrame, at: datetime
) -> None:
    identities = InstrumentTable(IDENTITY_PATHS)
    mirror_futures_contract_specs(versions_engine, seeded_frame, identities, FIRST)
    before = _history(versions_engine)
    changed = seeded_frame.copy()
    changed.loc[changed["symbol"] == "MES", "commission_per_side"] = 0.35
    new_contract = seeded_frame.loc[seeded_frame["symbol"] == "ES"].assign(symbol="NEW")
    changed = pd.concat([new_contract, changed], ignore_index=True)
    with pytest.raises(ValueError, match="knowledge date"):
        mirror_futures_contract_specs(versions_engine, changed, identities, at)
    assert _history(versions_engine) == before
    with versions_engine.connect() as conn:
        assert (
            conn.execute(
                select(FUTURES_CONTRACT_SPECS).where(FUTURES_CONTRACT_SPECS.c.symbol == "NEW")
            ).first()
            is None
        )
        assert (
            conn.execute(
                select(FUTURES_CONTRACT_SPECS.c.commission_per_side).where(
                    FUTURES_CONTRACT_SPECS.c.symbol == "MES"
                )
            ).scalar_one()
            == 0.25
        )


def test_naive_knowledge_date_is_refused_before_database_access(seeded_frame: pd.DataFrame) -> None:
    from sqlalchemy import create_engine

    engine = create_engine("postgresql+psycopg://unused")
    with pytest.raises(ValueError, match="timezone-aware"):
        mirror_futures_contract_specs(
            engine, seeded_frame, InstrumentTable(IDENTITY_PATHS), datetime(2026, 9, 23)
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        load_futures_spec_version(None, "MES", datetime(2026, 9, 23))  # type: ignore[arg-type]
