# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Price-family normalizers + the price FamilyPorts (R2.1, R4.x, R6.2; D5, D8).

Both FMP and yfinance write ``daily_prices`` keyed by the **canonical YF symbol**
(R4.3) so the panel is not fragmented by provider. Write-time cleaning for daily and
intraday bars from both providers: drop null-close rows, ``int(volume or 0)``, dedup on
the natural key, normalize dates.

Precedence (D8) is structural, not a dispatcher: the FMP price port maps via
``to_fmp_price``; the yfinance fallback port maps a symbol **only when FMP cannot**
(``to_fmp_price`` is ``None``, e.g. wheat ``ZW=F``), so yfinance never bulk-ingests
an FMP-covered symbol (R4.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pandas as pd

from facdrone_data.acquisition.families.cursors import MinMaxRangeGapFill, YfMaxDateLastBar
from facdrone_data.acquisition.families.normalizers._base import (
    coerce_date,
    drop_null_and_dedup,
    project_to_table,
)
from facdrone_data.acquisition.families.ports import FamilyPort
from facdrone_data.acquisition.families.symbol_mapping import ProviderSymbols
from facdrone_data.acquisition.intraday_clock import IntradayZones, intraday_or_skip
from facdrone_data.acquisition.merge import UpsertPanel
from facdrone_data.acquisition.schema import DAILY_PRICES, INTRADAY_PRICES
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from facdrone_data_types.symbols import CanonicalSymbol


def _int_volume(series: pd.Series) -> pd.Series:
    """``int(r.get("volume", 0) or 0)`` semantics: missing/NaN volume becomes 0."""
    return pd.to_numeric(series, errors="coerce").fillna(0).astype("int64")


def _drop_zero_close(frame: pd.DataFrame) -> pd.DataFrame:
    """A close of exactly zero is not a price. It is the shape of a bar the vendor had
    not finished: on 2026-09-07 the dollar index's Sunday session, cut short by Labor
    Day, arrived with its open and high filled and its low and close at exactly zero,
    passed the null filter because zero is not null, and the next day's return from it
    was infinite. That reached a downstream model, and the consuming service answered 500
    on every request for an hour.

    Zero, not "zero or less". WTI crude settled at -37.63 on 2020-04-20 and that row is
    in the panel as the real price it was; a rule that dropped it would be altering the
    record to fit an assumption. Dropped here, not in the shared helper, because other
    families hold legitimate zeros."""
    if frame.empty or "close" not in frame.columns:
        return frame
    zero = pd.to_numeric(frame["close"], errors="coerce") == 0
    return frame.loc[~zero].reset_index(drop=True)


@dataclass(frozen=True)
class DailyPricesNormalizer:
    """FMP/yfinance daily OHLCV → ``daily_prices``, re-keyed to the canonical symbol."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        del as_of
        if raw.empty or symbol is None:
            return pd.DataFrame(columns=[c.name for c in DAILY_PRICES.columns])
        out = raw.copy()
        out["symbol"] = symbol.canonical_id  # store YF symbol for both providers (R4.3)
        out["date"] = coerce_date(out["date"])
        out["volume"] = _int_volume(out["volume"])
        cleaned = drop_null_and_dedup(out, null_subset=["close"], key=["symbol", "date"])
        return project_to_table(_drop_zero_close(cleaned), DAILY_PRICES)


def _fmp_zones() -> IntradayZones:
    return IntradayZones.provider(Provider.FMP)


@dataclass(frozen=True)
class IntradayPricesNormalizer:
    """FMP intraday OHLCV → ``intraday_prices`` (datetime kept, not truncated).

    A bar lands once complete: its opening, read in its symbol's stamp zone, plus the
    interval is no later than the as-of. FMP stamps each index in its exchange's time, so
    the port binds ``zones`` from the run's identity table (``over``); the default reads
    every symbol in FMP's declared zone, what a caller without a table knows."""

    interval: str = "15min"
    zones: IntradayZones = field(default_factory=_fmp_zones)

    @classmethod
    def over(cls, symbols: ProviderSymbols) -> IntradayPricesNormalizer:
        """The normalizer reading each symbol's stamp zone from the run's identity table."""
        return cls(zones=symbols.identities.intraday_zones())

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        if raw.empty or symbol is None:
            return pd.DataFrame(columns=[c.name for c in INTRADAY_PRICES.columns])
        out = raw.copy()
        out["symbol"] = symbol.canonical_id
        out["datetime"] = pd.to_datetime(out["datetime"])
        # Stored FMP openings stay local; only the completion comparison uses UTC.
        interval = timedelta(minutes=60 if self.interval == "1hour" else int(self.interval[:-3]))
        at = as_of.replace(tzinfo=UTC) if as_of.tzinfo is None else as_of.astimezone(UTC)
        zone = self.zones.of(symbol.canonical_id)
        openings = [intraday_or_skip(ts.to_pydatetime(), zone) for ts in out["datetime"]]
        out = out.loc[[isinstance(ts, datetime) and ts + interval <= at for ts in openings]].copy()
        out["interval"] = self.interval
        out["volume"] = _int_volume(out["volume"])
        cleaned = drop_null_and_dedup(
            out, null_subset=["close"], key=["symbol", "datetime", "interval"]
        )
        return project_to_table(_drop_zero_close(cleaned), INTRADAY_PRICES)


def price_families() -> list[FamilyPort]:
    """The FMP daily/intraday ports (R2.1) and the yfinance fallback port (R4.2)."""
    return [
        FamilyPort(
            writer="refresh_fmp_daily",
            target_table=DAILY_PRICES,
            natural_key=("symbol", "date"),
            provider=Provider.FMP,
            incremental=MinMaxRangeGapFill(
                date_column="date",
                scope_column="symbol",
                # Re-fetch the held tail so a bar the vendor served provisionally is
                # corrected once final (D5, revised 2026-09-08). Five calendar days
                # covers a long weekend's stub sessions; the intraday port keeps zero.
                overlap_days=5,
            ),
            merge_strategy=UpsertPanel(),
            normalizer=DailyPricesNormalizer(),
            availability_column="date",
            quality_policy_default="quarantine",  # the door: QuarantineUnmeasurable at the root
            symbol_mapper=ProviderSymbols.fmp_price,
        ),
        FamilyPort(
            writer="refresh_fmp_intraday",
            target_table=INTRADAY_PRICES,
            natural_key=("symbol", "datetime", "interval"),
            provider=Provider.FMP,
            incremental=MinMaxRangeGapFill(date_column="datetime", scope_column="symbol"),
            merge_strategy=UpsertPanel(),
            normalizer=IntradayPricesNormalizer(),
            availability_column="datetime",
            quality_policy_default="quarantine",  # the door: QuarantineUnmeasurable at the root
            symbol_mapper=ProviderSymbols.fmp_price,
            bind_normalizer=IntradayPricesNormalizer.over,
        ),
        FamilyPort(
            writer="refresh_daily_prices",
            target_table=DAILY_PRICES,
            natural_key=("symbol", "date"),
            provider=Provider.YFINANCE,
            incremental=YfMaxDateLastBar(),  # start = last (partial-bar refetch)
            merge_strategy=UpsertPanel(),
            normalizer=DailyPricesNormalizer(),
            availability_column="date",
            quality_policy_default="quarantine",  # the door: QuarantineUnmeasurable at the root
            symbol_mapper=ProviderSymbols.yfinance_fallback,
        ),
    ]
