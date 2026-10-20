# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 9.1: incremental gap-fill + idempotent re-runs across families (R7.1–R7.4).

Representative families demonstrate, against real PostgreSQL:
* covered range → zero provider requests,
* gap-only fetch,
* repeat run → no-op (gap-skip families AND full-refetch families via upsert),
* a revised row overwrites under UpsertPanel.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import cast

import pandas as pd
from sqlalchemy import Engine, select

from facdrone_data.acquisition.families.cursors import FullWindow, MinMaxRangeGapFill
from facdrone_data.acquisition.families.normalizers.prices import DailyPricesNormalizer
from facdrone_data.acquisition.families.ports import FamilyPort, IncrementalStrategy, Normalizer
from facdrone_data.acquisition.families.refresher import DownloadInputs, FamilyRefresher
from facdrone_data.acquisition.families.symbol_mapping import ProviderSymbols
from facdrone_data.acquisition.instrument_table import InstrumentTable
from facdrone_data.acquisition.merge import UpsertPanel
from facdrone_data.acquisition.schema import DAILY_PRICES
from facdrone_data.acquisition.validation import KeepAll
from facdrone_data.quality.ensemble import EnsembleValidator
from facdrone_data.testing import EURUSD
from facdrone_data_types.ports import FetchRequest, MarketDataClient, SymbolRegistry
from facdrone_data_types.symbols import CanonicalSymbol, Provider

# The family mappers over an empty identity table: these tests inject their own mappers.
MAPPERS = ProviderSymbols(InstrumentTable(()))


@dataclass
class _RecordingClient:
    """Returns synthetic daily rows for the requested range; records every fetch."""

    provider: Provider = Provider.FMP
    bump: float = 0.0
    calls: list[FetchRequest] = field(default_factory=list)

    def fetch(self, req: FetchRequest) -> pd.DataFrame:
        self.calls.append(req)
        days = (req.end - req.start).days
        return pd.DataFrame(
            [
                {
                    "date": req.start.fromordinal(req.start.toordinal() + i),
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 100.0 + i + self.bump,
                    "volume": 10,
                }
                for i in range(days + 1)
            ]
        )

    def datasets(self) -> tuple[str, ...]:
        return ("daily_prices",)

    @property
    def fetched_days(self) -> int:
        return sum((c.end - c.start).days + 1 for c in self.calls)


class _IdentityRegistry:
    def to_provider(self, sym: CanonicalSymbol, provider: Provider) -> str:
        del provider
        return sym.canonical_id

    def to_canonical(self, provider_symbol: str, provider: Provider) -> CanonicalSymbol:
        raise NotImplementedError

    def all(self) -> tuple[CanonicalSymbol, ...]:
        return (EURUSD,)

    def validate(self) -> None:
        return None


def _port(
    *, cursor: IncrementalStrategy, mapper: Callable[[ProviderSymbols, str], str | None]
) -> FamilyPort:
    return FamilyPort(
        writer="refresh_fmp_daily",
        target_table=DAILY_PRICES,
        natural_key=("symbol", "date"),
        provider=Provider.FMP,
        incremental=cursor,
        merge_strategy=UpsertPanel(),
        normalizer=cast(Normalizer, DailyPricesNormalizer()),
        availability_column="date",
        quality_policy_default="keep_all",
        symbol_mapper=mapper,
    )


def _inputs(
    engine: Engine, client: _RecordingClient, port: FamilyPort, want: tuple[date, date]
) -> DownloadInputs:
    return DownloadInputs(
        universe=(EURUSD,),
        families=(port,),
        want=want,
        engine=engine,
        clients={Provider.FMP: cast(MarketDataClient, client)},
        registry=cast(SymbolRegistry, _IdentityRegistry()),
        symbols=MAPPERS,
        validator=cast(EnsembleValidator, None),
        policy=KeepAll(),
        as_of=datetime(2024, 2, 1, 12, 0),
    )


def _identity(_mappers: ProviderSymbols, yf: str) -> str | None:
    return yf


def test_gapfill_family_covered_then_repeat_makes_no_request(prices_engine: Engine) -> None:
    want = (date(2024, 1, 1), date(2024, 1, 10))
    client = _RecordingClient()
    port = _port(cursor=MinMaxRangeGapFill(), mapper=_identity)

    FamilyRefresher().run(_inputs(prices_engine, client, port, want))
    first_calls = len(client.calls)
    assert first_calls >= 1 and client.fetched_days == 10  # fetched the window

    FamilyRefresher().run(_inputs(prices_engine, client, port, want))  # fully covered
    assert len(client.calls) == first_calls  # R7.1: zero additional provider requests


def test_gapfill_family_fetches_only_the_gap(prices_engine: Engine) -> None:
    client = _RecordingClient()
    port = _port(cursor=MinMaxRangeGapFill(), mapper=_identity)
    FamilyRefresher().run(
        _inputs(prices_engine, client, port, (date(2024, 1, 1), date(2024, 1, 5)))
    )
    client.calls.clear()
    # extend the window forward; only the 6th..10th gap should be fetched (R7.2)
    FamilyRefresher().run(
        _inputs(prices_engine, client, port, (date(2024, 1, 1), date(2024, 1, 10)))
    )
    assert client.fetched_days == 5
    assert all(c.start == date(2024, 1, 6) for c in client.calls)


def test_full_refetch_family_repeat_is_a_noop_via_upsert(prices_engine: Engine) -> None:
    want = (date(2024, 1, 1), date(2024, 1, 5))
    client = _RecordingClient()
    port = _port(cursor=FullWindow(), mapper=_identity)  # always refetches the window

    first = FamilyRefresher().run(_inputs(prices_engine, client, port, want))
    assert first.summaries[0].inserted == 5
    second = FamilyRefresher().run(_inputs(prices_engine, client, port, want))
    # FullWindow refetches, but the upsert writes nothing new (R7.4).
    assert second.summaries[0].inserted == 0 and second.summaries[0].updated == 0


def test_revision_overwrites_under_upsert(prices_engine: Engine) -> None:
    want = (date(2024, 1, 1), date(2024, 1, 3))
    port = _port(cursor=FullWindow(), mapper=_identity)
    FamilyRefresher().run(_inputs(prices_engine, _RecordingClient(bump=0.0), port, want))
    # a later refresh returns revised closes for the same keys
    report = FamilyRefresher().run(_inputs(prices_engine, _RecordingClient(bump=500.0), port, want))
    assert report.summaries[0].updated == 3  # R6.3/R7.3: revised rows overwritten
    stored = pd.read_sql(select(DAILY_PRICES.c.close).order_by(DAILY_PRICES.c.date), prices_engine)
    assert float(stored["close"].iloc[0]) == 600.0  # 100 + 0 + 500 bump
