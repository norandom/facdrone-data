# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The fundamentals store is append-only across restatements (equity-factors 1.2; R3.2).

``fmp_cash_flow``'s primary key INCLUDES ``filing_date``: a restatement of the
same fiscal period is a NEW row, both vintages readable, and re-writing the
same vintage is idempotent (UpsertPanel revises in place on the full key).
The earnings calendar round-trips on its ``(symbol, date)`` key. External
throwaway PostgreSQL only — no containers; skips when the DSN is unset.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pandas as pd
from sqlalchemy import select

from facdrone_data.acquisition.merge import UpsertPanel
from facdrone_data.acquisition.schema import FMP_CASH_FLOW, FMP_EARNINGS_CALENDAR

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

_AS_OF = datetime(2026, 8, 31, 12, 0, tzinfo=UTC).replace(tzinfo=None)


def _cash_flow_row(filing: date, fcf: float) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "ALV.DE",
                "date": date(2025, 12, 31),
                "fiscal_year": 2025,
                "period": "annual",
                "operating_cash_flow": 100.0,
                "capital_expenditure": -20.0,
                "free_cash_flow": fcf,
                "stock_based_compensation": 0.0,
                "weighted_average_shares": 400_000_000.0,
                "filing_date": filing,
                "_updated_at": _AS_OF,
            }
        ]
    )


class TestCashFlowRestatements:
    def test_a_restatement_is_a_new_row_and_both_vintages_survive(self, pg_engine: Engine) -> None:
        FMP_CASH_FLOW.drop(pg_engine, checkfirst=True)
        FMP_CASH_FLOW.create(pg_engine)
        strategy = UpsertPanel()

        strategy.merge(pg_engine, FMP_CASH_FLOW, _cash_flow_row(date(2026, 2, 27), 80.0))
        strategy.merge(pg_engine, FMP_CASH_FLOW, _cash_flow_row(date(2026, 4, 15), 75.0))
        # The same vintage again — idempotent revise, never a third row.
        strategy.merge(pg_engine, FMP_CASH_FLOW, _cash_flow_row(date(2026, 4, 15), 75.0))

        with pg_engine.connect() as conn:
            rows = conn.execute(
                select(FMP_CASH_FLOW.c.filing_date, FMP_CASH_FLOW.c.free_cash_flow).order_by(
                    FMP_CASH_FLOW.c.filing_date
                )
            ).all()
        assert [(r.filing_date, r.free_cash_flow) for r in rows] == [
            (date(2026, 2, 27), 80.0),
            (date(2026, 4, 15), 75.0),
        ]

    def test_a_present_zero_sbc_survives_as_zero(self, pg_engine: Engine) -> None:
        FMP_CASH_FLOW.drop(pg_engine, checkfirst=True)
        FMP_CASH_FLOW.create(pg_engine)
        UpsertPanel().merge(pg_engine, FMP_CASH_FLOW, _cash_flow_row(date(2026, 2, 27), 80.0))
        with pg_engine.connect() as conn:
            sbc = conn.execute(select(FMP_CASH_FLOW.c.stock_based_compensation)).scalar()
        assert sbc == 0.0  # a vendor zero is a value, not an absence (R1.3)


class TestEarningsCalendarRoundTrip:
    def test_announcements_round_trip_on_their_key(self, pg_engine: Engine) -> None:
        FMP_EARNINGS_CALENDAR.drop(pg_engine, checkfirst=True)
        FMP_EARNINGS_CALENDAR.create(pg_engine)
        frame = pd.DataFrame(
            [
                {
                    "symbol": "ALV.DE",
                    "date": date(2026, 2, 27),
                    "eps_actual": 6.51,
                    "eps_estimate": 6.20,
                    "revenue_actual": None,
                    "revenue_estimate": None,
                    "_updated_at": _AS_OF,
                }
            ]
        )
        UpsertPanel().merge(pg_engine, FMP_EARNINGS_CALENDAR, frame)
        with pg_engine.connect() as conn:
            row = conn.execute(select(FMP_EARNINGS_CALENDAR)).one()
        assert (row.symbol, row.date, row.eps_actual) == ("ALV.DE", date(2026, 2, 27), 6.51)
        assert row.revenue_actual is None  # missing stays missing, never zero
