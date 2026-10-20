# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Interfacer: PostgreSQL persistence of catalog-backed futures metadata revisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import pandas as pd
from pydantic import TypeAdapter
from sqlalchemy import Engine, Select, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection

# Re-exported: the download root reads the catalog through this module (sentrux fan-out).
from facdrone_data.acquisition.futures_specs import (
    load_futures_contract_specs as load_futures_contract_specs,
)
from facdrone_data.acquisition.instrument_table import InstrumentTable as InstrumentTable
from facdrone_data.acquisition.schema import FUTURES_CONTRACT_SPECS, INSTRUMENT_METADATA_VERSION
from facdrone_data_types.instruments import FuturesContract

_SOURCE = "futures_contract_specs"
_CONTRACT = TypeAdapter(FuturesContract)


@dataclass(frozen=True)
class FuturesSpecVersion:
    contract: FuturesContract
    known_at: datetime
    revision_id: str


def _require_aware(moment: datetime) -> None:
    if moment.utcoffset() is None:
        raise ValueError("knowledge date must be timezone-aware")


def _latest_version(symbol: str) -> Select[Any]:
    versions = INSTRUMENT_METADATA_VERSION
    return (
        select(versions)
        .where(versions.c.venue_symbol == symbol, versions.c.source_name == _SOURCE)
        .order_by(versions.c.known_at.desc(), versions.c.revision_id.desc())
        .limit(1)
    )


def load_futures_spec_version(
    conn: Connection, symbol: str, as_of: datetime
) -> FuturesSpecVersion | None:
    """Return the last revision known at the inclusive cutoff; never fall back to today's mirror."""
    _require_aware(as_of)
    row = (
        conn.execute(_latest_version(symbol).where(INSTRUMENT_METADATA_VERSION.c.known_at <= as_of))
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    return FuturesSpecVersion(
        _CONTRACT.validate_python(row["futures_payload"]), row["known_at"], row["revision_id"]
    )


def _append_version(conn: Connection, values: dict[str, Any]) -> bool:
    """Serialize callers outside this function; changes require a strictly later knowledge date."""
    previous = conn.execute(_latest_version(values["venue_symbol"])).mappings().one_or_none()
    revision = 1
    if previous is not None:
        if values["known_at"] < previous["known_at"]:
            raise ValueError(f"{values['venue_symbol']}: knowledge date precedes latest revision")
        if previous["futures_payload"] == values["futures_payload"]:
            return False
        if values["known_at"] == previous["known_at"]:
            raise ValueError(
                f"{values['venue_symbol']}: conflicting payload at the same knowledge date"
            )
        revision = int(previous["revision_id"]) + 1
    values["revision_id"] = str(revision)
    conn.execute(INSTRUMENT_METADATA_VERSION.insert(), values)
    return True


def _metadata_values(
    contract: FuturesContract, identities: InstrumentTable, known_at: datetime
) -> dict[str, Any]:
    identity = identities.identity(contract.parent_series)
    if "futures" not in identity.kinds or identity.settlement_ccy != contract.currency:
        raise ValueError(f"{contract.symbol}: futures identity/currency disagrees with catalog")
    return {
        "venue_symbol": contract.symbol,
        "yf_symbol": contract.parent_series,
        "asset_class": identity.asset_class.value,
        "group": contract.exchange,
        "aliases_json": [],
        "currency_profit": contract.currency,
        "currency_margin": contract.currency,
        "tick_size": contract.tick_size,
        "tick_value": contract.tick_value,
        "known_at": known_at,
        "source_name": _SOURCE,
        "futures_payload": asdict(contract),
    }


def mirror_futures_contract_specs(
    engine: Engine, frame: pd.DataFrame, identities: InstrumentTable, known_at: datetime
) -> int:
    """Validate the catalog, atomically mirror its rows, and count appended revisions.

    Versions are insert-only; only the current-spec projection is updated. Catalog omission
    is not a deletion. First observation starts at revision 1, even over a pre-existing mirror.
    """
    _require_aware(known_at)
    contracts = load_futures_contract_specs(frame)
    values = [_metadata_values(row, identities, known_at) for row in contracts.rows]
    appended = 0
    with engine.begin() as conn:
        # ponytail: serialize this small catalog; use per-symbol locks if contention matters.
        conn.execute(text("LOCK TABLE futures_contract_specs IN SHARE ROW EXCLUSIVE MODE"))
        for version in values:
            appended += _append_version(conn, version)
            current = dict(version["futures_payload"])
            current["parent_symbol"] = current.pop("parent_series")
            stmt = insert(FUTURES_CONTRACT_SPECS).values(current)
            conn.execute(stmt.on_conflict_do_update(index_elements=["symbol"], set_=current))
    return appended
