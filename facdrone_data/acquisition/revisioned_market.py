# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Revisioned market-data readers for tasks 1.3 and 1.4.

Append-only price and FX observations preserve multiple knowable versions of the
same event across time. These helpers read the latest revision whose
``known_at <= as_of`` without changing any existing ``daily_prices`` consumer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.engine import Connection

from facdrone_data.acquisition.schema import FX_OBSERVATION, PRICE_OBSERVATION


def _require_aware(moment: datetime, name: str) -> None:
    if moment.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")


@dataclass(frozen=True)
class PriceObservation:
    symbol: str
    event_time: datetime
    known_at: datetime
    source_name: str
    revision_id: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None


@dataclass(frozen=True)
class FxObservation:
    quote_symbol: str
    base_currency: str
    quote_currency: str
    event_time: datetime
    known_at: datetime
    source_name: str
    revision_id: str
    rate: float


def _latest_price_stmt(symbols: Sequence[str], as_of: datetime) -> Select[tuple[object]]:
    ranked = (
        select(
            PRICE_OBSERVATION.c.symbol,
            PRICE_OBSERVATION.c.event_time,
            PRICE_OBSERVATION.c.known_at,
            PRICE_OBSERVATION.c.source_name,
            PRICE_OBSERVATION.c.revision_id,
            PRICE_OBSERVATION.c.open,
            PRICE_OBSERVATION.c.high,
            PRICE_OBSERVATION.c.low,
            PRICE_OBSERVATION.c.close,
            PRICE_OBSERVATION.c.volume,
            func.row_number()
            .over(
                partition_by=(PRICE_OBSERVATION.c.symbol, PRICE_OBSERVATION.c.event_time),
                order_by=(
                    PRICE_OBSERVATION.c.known_at.desc(),
                    PRICE_OBSERVATION.c.source_name.desc(),
                    PRICE_OBSERVATION.c.revision_id.desc(),
                ),
            )
            .label("rn"),
        )
        .where(
            PRICE_OBSERVATION.c.symbol.in_(list(symbols)),
            PRICE_OBSERVATION.c.known_at <= as_of,
        )
        .subquery()
    )
    return (
        select(ranked)
        .where(ranked.c.rn == 1)
        .order_by(ranked.c.symbol, ranked.c.event_time, ranked.c.known_at)
    )


def _latest_fx_stmt(quote_symbols: Sequence[str], as_of: datetime) -> Select[tuple[object]]:
    ranked = (
        select(
            FX_OBSERVATION.c.quote_symbol,
            FX_OBSERVATION.c.base_currency,
            FX_OBSERVATION.c.quote_currency,
            FX_OBSERVATION.c.event_time,
            FX_OBSERVATION.c.known_at,
            FX_OBSERVATION.c.source_name,
            FX_OBSERVATION.c.revision_id,
            FX_OBSERVATION.c.rate,
            func.row_number()
            .over(
                partition_by=(FX_OBSERVATION.c.quote_symbol, FX_OBSERVATION.c.event_time),
                order_by=(
                    FX_OBSERVATION.c.known_at.desc(),
                    FX_OBSERVATION.c.source_name.desc(),
                    FX_OBSERVATION.c.revision_id.desc(),
                ),
            )
            .label("rn"),
        )
        .where(
            FX_OBSERVATION.c.quote_symbol.in_(list(quote_symbols)),
            FX_OBSERVATION.c.known_at <= as_of,
        )
        .subquery()
    )
    return (
        select(ranked)
        .where(ranked.c.rn == 1)
        .order_by(ranked.c.quote_symbol, ranked.c.event_time, ranked.c.known_at)
    )


def load_latest_price_observations(
    conn: Connection,
    *,
    symbols: Sequence[str],
    as_of: datetime,
) -> tuple[PriceObservation, ...]:
    _require_aware(as_of, "as_of")
    if not symbols:
        return ()
    rows = conn.execute(_latest_price_stmt(symbols, as_of)).mappings()
    return tuple(
        PriceObservation(
            symbol=str(row["symbol"]),
            event_time=row["event_time"],
            known_at=row["known_at"],
            source_name=str(row["source_name"]),
            revision_id=str(row["revision_id"]),
            open=float(row["open"]) if row["open"] is not None else None,
            high=float(row["high"]) if row["high"] is not None else None,
            low=float(row["low"]) if row["low"] is not None else None,
            close=float(row["close"]) if row["close"] is not None else None,
            volume=int(row["volume"]) if row["volume"] is not None else None,
        )
        for row in rows
    )


def load_latest_fx_observations(
    conn: Connection,
    *,
    quote_symbols: Sequence[str],
    as_of: datetime,
) -> tuple[FxObservation, ...]:
    _require_aware(as_of, "as_of")
    if not quote_symbols:
        return ()
    rows = conn.execute(_latest_fx_stmt(quote_symbols, as_of)).mappings()
    return tuple(
        FxObservation(
            quote_symbol=str(row["quote_symbol"]),
            base_currency=str(row["base_currency"]),
            quote_currency=str(row["quote_currency"]),
            event_time=row["event_time"],
            known_at=row["known_at"],
            source_name=str(row["source_name"]),
            revision_id=str(row["revision_id"]),
            rate=float(row["rate"]),
        )
        for row in rows
    )
