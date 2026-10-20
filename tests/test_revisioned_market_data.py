# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Tasks 1.3/1.4: revisioned market-data storage and PIT readers."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

from pytest import fixture
from sqlalchemy import Engine, insert

from facdrone_data.acquisition.revisioned_market import (
    load_latest_fx_observations,
    load_latest_price_observations,
)
from facdrone_data.acquisition.schema import FX_OBSERVATION, PRICE_OBSERVATION


class TestRevisionedMarketData:
    def test_latest_knowable_price_revision_is_selected_by_as_of(
        self, revisioned_market_engine: Engine
    ) -> None:
        day1 = datetime(2026, 1, 2, 0, 0, tzinfo=UTC)
        day2 = datetime(2026, 1, 3, 0, 0, tzinfo=UTC)
        with revisioned_market_engine.begin() as conn:
            conn.execute(
                insert(PRICE_OBSERVATION),
                [
                    {
                        "symbol": "GC=F",
                        "event_time": day1,
                        "known_at": datetime(2026, 1, 2, 7, 0, tzinfo=UTC),
                        "source_name": "yfinance",
                        "revision_id": "gc-r1",
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.5,
                        "volume": 10,
                    },
                    {
                        "symbol": "GC=F",
                        "event_time": day1,
                        "known_at": datetime(2026, 1, 2, 10, 0, tzinfo=UTC),
                        "source_name": "yfinance",
                        "revision_id": "gc-r2",
                        "open": 100.0,
                        "high": 102.0,
                        "low": 99.0,
                        "close": 101.5,
                        "volume": 11,
                    },
                    {
                        "symbol": "GC=F",
                        "event_time": day1,
                        "known_at": datetime(2026, 1, 3, 10, 0, tzinfo=UTC),
                        "source_name": "yfinance",
                        "revision_id": "gc-r3",
                        "open": 100.0,
                        "high": 103.0,
                        "low": 99.0,
                        "close": 102.5,
                        "volume": 12,
                    },
                    {
                        "symbol": "GC=F",
                        "event_time": day2,
                        "known_at": datetime(2026, 1, 3, 8, 0, tzinfo=UTC),
                        "source_name": "yfinance",
                        "revision_id": "gc-r4",
                        "open": 103.0,
                        "high": 104.0,
                        "low": 102.0,
                        "close": 103.5,
                        "volume": 13,
                    },
                ],
            )

        with revisioned_market_engine.connect() as conn:
            rows = load_latest_price_observations(
                conn,
                symbols=("GC=F",),
                as_of=datetime(2026, 1, 3, 9, 0, tzinfo=UTC),
            )

        assert [
            (row.symbol, row.event_time, row.known_at, row.revision_id, row.close) for row in rows
        ] == [
            ("GC=F", day1, datetime(2026, 1, 2, 10, 0, tzinfo=UTC), "gc-r2", 101.5),
            ("GC=F", day2, datetime(2026, 1, 3, 8, 0, tzinfo=UTC), "gc-r4", 103.5),
        ]

    def test_latest_knowable_fx_revision_is_selected_by_as_of(
        self, revisioned_market_engine: Engine
    ) -> None:
        event_time = datetime(2026, 1, 2, 0, 0, tzinfo=UTC)
        with revisioned_market_engine.begin() as conn:
            conn.execute(
                insert(FX_OBSERVATION),
                [
                    {
                        "quote_symbol": "EURUSD=X",
                        "base_currency": "EUR",
                        "quote_currency": "USD",
                        "event_time": event_time,
                        "known_at": datetime(2026, 1, 2, 18, 0, tzinfo=UTC),
                        "source_name": "yfinance",
                        "revision_id": "eurusd-r1",
                        "rate": 1.08,
                    },
                    {
                        "quote_symbol": "EURUSD=X",
                        "base_currency": "EUR",
                        "quote_currency": "USD",
                        "event_time": event_time,
                        "known_at": datetime(2026, 1, 2, 20, 0, tzinfo=UTC),
                        "source_name": "yfinance",
                        "revision_id": "eurusd-r2",
                        "rate": 1.09,
                    },
                    {
                        "quote_symbol": "EURUSD=X",
                        "base_currency": "EUR",
                        "quote_currency": "USD",
                        "event_time": event_time,
                        "known_at": datetime(2026, 1, 3, 8, 0, tzinfo=UTC),
                        "source_name": "yfinance",
                        "revision_id": "eurusd-r3",
                        "rate": 1.10,
                    },
                ],
            )

        with revisioned_market_engine.connect() as conn:
            rows = load_latest_fx_observations(
                conn,
                quote_symbols=("EURUSD=X",),
                as_of=datetime(2026, 1, 2, 23, 0, tzinfo=UTC),
            )

        assert [
            (
                row.quote_symbol,
                row.base_currency,
                row.quote_currency,
                row.event_time,
                row.known_at,
                row.revision_id,
                row.rate,
            )
            for row in rows
        ] == [
            (
                "EURUSD=X",
                "EUR",
                "USD",
                event_time,
                datetime(2026, 1, 2, 20, 0, tzinfo=UTC),
                "eurusd-r2",
                1.09,
            )
        ]


@fixture
def revisioned_market_engine(pg_engine: Engine) -> Iterator[Engine]:
    for table in (PRICE_OBSERVATION, FX_OBSERVATION):
        table.drop(pg_engine, checkfirst=True)
        table.create(pg_engine)
    try:
        yield pg_engine
    finally:
        for table in (FX_OBSERVATION, PRICE_OBSERVATION):
            table.drop(pg_engine, checkfirst=True)
