# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Shared fixtures of the download-runner tests: two synthetic families, their clients and
the declared two-instrument universe (facdrone-data-manager 1.6)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import pandas as pd

from facdrone_data.acquisition.families.cursors import MinMaxRangeGapFill
from facdrone_data.acquisition.families.normalizers.prices import DailyPricesNormalizer
from facdrone_data.acquisition.families.ports import FamilyPort
from facdrone_data.acquisition.families.symbol_mapping import ProviderSymbols
from facdrone_data.acquisition.merge import UpsertPanel
from facdrone_data.acquisition.schema import DAILY_PRICES
from facdrone_data.config import DataManagerConfig, InstrumentEntry
from facdrone_data.testing.fakes import synthetic_daily_rows
from facdrone_data.universe import DeclaredUniverse
from facdrone_data_types.enums import AssetClass
from facdrone_data_types.ports import FetchRequest
from facdrone_data_types.symbols import Provider

WINDOW = (date(2025, 1, 6), date(2025, 1, 10))
KNOWN_AT = pd.Timestamp("2025-01-11").to_pydatetime()


def only_symbol(symbol: str) -> Callable[[ProviderSymbols, str], str | None]:
    """A family mapper that maps exactly one canonical symbol to itself."""

    def _map(_mappers: ProviderSymbols, canonical: str) -> str | None:
        return canonical if canonical == symbol else None

    return _map


def daily_port(writer: str, provider: Provider, symbol: str) -> FamilyPort:
    return FamilyPort(
        writer=writer,
        target_table=DAILY_PRICES,
        natural_key=("symbol", "date"),
        provider=provider,
        incremental=MinMaxRangeGapFill(),
        merge_strategy=UpsertPanel(),
        normalizer=DailyPricesNormalizer(),
        availability_column="date",
        quality_policy_default="keep_all",
        symbol_mapper=only_symbol(symbol),
    )


# A keyless family for AAA and a keyed (FMP) family for BBB.
PORTS = (
    daily_port("free_prices", Provider.YFINANCE, "AAA"),
    daily_port("keyed_prices", Provider.FMP, "BBB"),
)


@dataclass
class SyntheticClient:
    """Serves synthetic daily rows; raises ``interrupt`` instead when it is set."""

    provider: Provider
    interrupt: BaseException | None = None

    def fetch(self, req: FetchRequest) -> pd.DataFrame:
        if self.interrupt is not None:
            raise self.interrupt
        return synthetic_daily_rows(req.provider_symbol, req.start, req.end)

    def datasets(self) -> tuple[str, ...]:
        return ("daily_prices",)


def two_instrument_universe() -> DeclaredUniverse:
    return DeclaredUniverse(
        DataManagerConfig(
            identity_files=(),
            instruments=(
                InstrumentEntry(canonical="AAA", asset_class=AssetClass.INDICES, profit_ccy="USD"),
                InstrumentEntry(canonical="BBB", asset_class=AssetClass.INDICES, profit_ccy="USD"),
            ),
            conversion_currency="USD",
        )
    )
