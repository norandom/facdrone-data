# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 4.1: write-time correction through the validation gate (R6.1, R6.2,
R6.4). Contaminated rows are quarantined BEFORE the panel write; reads see only
corrected data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import cast

import pandas as pd
from sqlalchemy import Engine, select

from facdrone_data.acquisition.families.cursors import MinMaxRangeGapFill
from facdrone_data.acquisition.families.gate import quality_gate
from facdrone_data.acquisition.families.ports import FamilyPort, Normalizer
from facdrone_data.acquisition.families.refresher import DownloadInputs, FamilyRefresher
from facdrone_data.acquisition.families.symbol_mapping import ProviderSymbols
from facdrone_data.acquisition.instrument_table import InstrumentTable
from facdrone_data.acquisition.merge import UpsertPanel
from facdrone_data.acquisition.schema import DAILY_PRICES
from facdrone_data.acquisition.symbol_registry import SymbolConfigError
from facdrone_data.acquisition.validation import KeepAll, QualityPolicy, QuarantineContaminated
from facdrone_data.quality.ensemble import EnsembleValidator, QualityIssue, QualityReport
from facdrone_data.testing import EURUSD
from facdrone_data_types.ports import MarketDataClient, SymbolRegistry
from facdrone_data_types.symbols import CanonicalSymbol, Provider

# The family mappers over an empty identity table: these tests inject their own mappers.
MAPPERS = ProviderSymbols(InstrumentTable(()))

_COLS = ["symbol", "date", "open", "high", "low", "close", "volume"]


@dataclass
class _Issue:
    position: int


@dataclass
class _Report:
    issues: list[_Issue]


@dataclass
class _FlagValidator:
    """Flags fixed row positions as contaminated."""

    positions: tuple[int, ...]

    def validate(
        self, frame: pd.DataFrame, *, symbol: str, dataset: str, asset_class: str
    ) -> QualityReport:
        del frame, symbol, dataset, asset_class
        return cast(QualityReport, _Report([_Issue(p) for p in self.positions]))


def _prices(n: int) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "EURUSD",
                "date": date(2024, 1, 1 + i),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0 + i,
                "volume": 10 + i,
            }
            for i in range(n)
        ]
    )


def _port(policy_default: str) -> FamilyPort:
    return FamilyPort(
        writer="refresh_fmp_daily",
        target_table=DAILY_PRICES,
        natural_key=("symbol", "date"),
        provider=Provider.FMP,
        incremental=MinMaxRangeGapFill(),
        merge_strategy=UpsertPanel(),
        normalizer=cast(Normalizer, object()),  # gate test does not call the normalizer
        availability_column="date",
        quality_policy_default=policy_default,
    )


def _inputs(validator: object, policy: object) -> DownloadInputs:
    return DownloadInputs(
        universe=(EURUSD,),
        families=(),
        want=(date(2024, 1, 1), date(2024, 1, 5)),
        engine=cast(Engine, None),
        clients={},
        registry=cast(SymbolRegistry, None),
        symbols=MAPPERS,
        validator=cast(EnsembleValidator, validator),
        policy=cast(QualityPolicy, policy),
        as_of=datetime(2024, 1, 6, 12, 0),
    )


# ── offline gate behaviour ──────────────────────────────────────────────────
def test_gate_quarantines_flagged_rows_before_merge() -> None:
    inputs = _inputs(_FlagValidator((1,)), QuarantineContaminated())
    out = quality_gate(_prices(3), _port("quarantine"), inputs, EURUSD)
    assert len(out) == 2  # the flagged middle row dropped (R6.4)
    assert list(out.columns) == [c.name for c in DAILY_PRICES.columns]  # projected to table
    assert 2.0 not in set(out["close"])  # row index 1 had close 2.0


def test_gate_keep_all_family_skips_the_ensemble() -> None:
    # A flagging validator is present but keep_all never invokes it.
    inputs = _inputs(_FlagValidator((0, 1, 2)), KeepAll())
    out = quality_gate(_prices(3), _port("keep_all"), inputs, EURUSD)
    assert len(out) == 3  # nothing dropped


def test_gate_projects_to_exactly_the_table_columns() -> None:
    frame = _prices(2)
    frame["knowledge_date"] = pd.Timestamp("2024-01-02")  # transient, must be dropped
    inputs = _inputs(_FlagValidator(()), KeepAll())
    out = quality_gate(frame, _port("keep_all"), inputs, EURUSD)
    assert "knowledge_date" not in out.columns
    assert list(out.columns) == [c.name for c in DAILY_PRICES.columns]


# ── end-to-end: reads see only corrected data ───────────────────────────────
class _IdentityNormalizer:
    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        del as_of, symbol
        return raw[_COLS].copy()


class _OneSymbolRegistry:
    def to_provider(self, sym: CanonicalSymbol, provider: Provider) -> str:
        del provider
        if sym.canonical_id == "EURUSD":
            return "EURUSD"
        raise SymbolConfigError("unmapped")

    def to_canonical(self, provider_symbol: str, provider: Provider) -> CanonicalSymbol:
        raise NotImplementedError

    def all(self) -> tuple[CanonicalSymbol, ...]:
        return (EURUSD,)

    def validate(self) -> None:
        return None


@dataclass
class _ThreeRowClient:
    provider: Provider = Provider.FMP

    def fetch(self, req: object) -> pd.DataFrame:
        del req
        return _prices(3)

    def datasets(self) -> tuple[str, ...]:
        return ("daily_prices",)


def test_refresher_with_gate_persists_only_corrected_rows(prices_engine: Engine) -> None:
    port = FamilyPort(
        writer="refresh_fmp_daily",
        target_table=DAILY_PRICES,
        natural_key=("symbol", "date"),
        provider=Provider.FMP,
        incremental=MinMaxRangeGapFill(),
        merge_strategy=UpsertPanel(),
        normalizer=cast(Normalizer, _IdentityNormalizer()),
        availability_column="date",
        quality_policy_default="quarantine",
        symbol_mapper=lambda _mappers, symbol: symbol,
    )
    inputs = DownloadInputs(
        universe=(EURUSD,),
        families=(port,),
        want=(date(2024, 1, 1), date(2024, 1, 3)),
        engine=prices_engine,
        clients={Provider.FMP: cast(MarketDataClient, _ThreeRowClient())},
        registry=cast(SymbolRegistry, _OneSymbolRegistry()),
        symbols=MAPPERS,
        validator=cast(EnsembleValidator, _FlagValidator((1,))),
        policy=QuarantineContaminated(),
        as_of=datetime(2024, 1, 4, 12, 0),
    )
    FamilyRefresher(gate=quality_gate).run(inputs)
    stored = pd.read_sql(select(DAILY_PRICES).order_by(DAILY_PRICES.c.date), prices_engine)
    assert len(stored) == 2  # the contaminated row never reached the panel (R6.1, R6.4)
    assert 2.0 not in set(stored["close"])


# ── structural integrity vetoes (non-positive prices, sign changes) ──


def _integrity_report(closes: list[float]) -> object:
    from facdrone_data.quality.ensemble import EnsembleValidator

    frame = pd.DataFrame({"close": closes, "volume": [1.0] * len(closes)})
    return EnsembleValidator().validate(
        frame, symbol="TEST", dataset="daily_prices", asset_class="equities"
    )


def _fired(report: object) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in report.issues:  # type: ignore[attr-defined]
        for name in issue.detectors:
            counts[name] = counts.get(name, 0) + 1
    return counts


def test_a_non_positive_close_is_flagged_without_a_vote() -> None:
    """A price that is not positive cannot normalise a return, so it is a
    structural fact rather than a statistical opinion. Four detectors seeing
    nothing unusual must not be able to outvote it — the consensus stays 0.0
    and the row is flagged anyway."""
    report = _integrity_report([100.0, 101.0, -5.0, 102.0, 103.0])
    flagged = {issue.position: issue for issue in report.issues}  # type: ignore[attr-defined]
    assert 2 in flagged
    assert "nonpositive_price" in flagged[2].detectors
    assert flagged[2].consensus == 0.0  # vetoed, not voted


def test_a_sign_change_separates_corruption_from_a_real_excursion() -> None:
    """Air France-KLM carries 257 consecutive negative closes ending in a jump
    from -229.94 to +61.65 — back-adjustment corruption. WTI's single negative
    print on 2020-04-20 is real. Both are flagged as needing eyes, but the
    COUNT distinguishes them: a sustained run versus one bar."""
    corrupt = _fired(_integrity_report([-229.94, -230.1, -228.0, 61.65, 62.9]))
    real = _fired(_integrity_report([19.87, 18.27, -37.63, 10.01, 13.78]))
    assert corrupt["nonpositive_price"] == 3
    assert real["nonpositive_price"] == 1
    # Each run of negatives is bounded by sign changes at its edges.
    assert corrupt["price_sign_change"] == 1  # only the exit; the run starts the series
    assert real["price_sign_change"] == 2  # into the negative print and back out


def test_a_clean_series_raises_no_integrity_flag() -> None:
    report = _integrity_report([100.0, 100.5, 101.0, 100.8, 101.2])
    assert "nonpositive_price" not in _fired(report)
    assert "price_sign_change" not in _fired(report)


# ── the door: structural vetoes drop rows, statistical opinions stay opinions ──


def test_quarantine_unmeasurable_drops_only_what_the_structural_detectors_veto() -> None:
    """The fence for vendor corruption of the Air France kind (2004-07-22 ..
    2005-07-15: the whole OHLC sign-flipped by naive back-adjustment through the
    KLM exchange offer, served that way to this day). A row whose close is not
    positive cannot be a price, so it never reaches the panel; a row the vote
    merely finds unusual is kept, because an opinion is the report's business,
    not the door's."""
    from facdrone_data.acquisition.validation import QuarantineUnmeasurable

    frame = _prices(5)
    frame.loc[2, "close"] = -221.22
    # Position 2 is vetoed by a structural detector; position 4 is only an opinion.
    issues = (
        QualityIssue(2, "2024-01-03", 0.0, ("nonpositive_price", "price_sign_change")),
        QualityIssue(4, "2024-01-05", 0.65, ("robust_z", "hampel", "isolation_forest")),
    )
    report = QualityReport("EURUSD", "daily_prices", 60.0, 0.4, issues)
    out = QuarantineUnmeasurable().apply(frame, report)
    assert list(out["close"]) == [1.0, 2.0, 4.0, 5.0]


def test_the_exit_edge_of_a_negative_run_is_a_real_print_and_stays() -> None:
    """``price_sign_change`` marks both edges of a fabricated run so the operator
    sees where it begins and ends; the exit edge (Air France's +61.65 on
    2005-07-18) is a genuine price and the door must not drop it."""
    from facdrone_data.acquisition.validation import QuarantineUnmeasurable

    frame = _prices(3)
    frame.loc[0, "close"] = -229.94
    issues = (
        QualityIssue(0, "2024-01-01", 0.0, ("nonpositive_price",)),
        QualityIssue(1, "2024-01-02", 0.0, ("price_sign_change",)),
    )
    report = QualityReport("AF.PA", "daily_prices", 33.3, 0.67, issues)
    out = QuarantineUnmeasurable().apply(frame, report)
    assert list(out["close"]) == [2.0, 3.0]


def test_the_real_gate_with_the_real_ensemble_refuses_air_france_at_the_door() -> None:
    """End to end through ``quality_gate``: the composed validator, a port that
    declares quarantine, and the unmeasurable policy. Positive rows before and
    after the run survive, every negative row is refused, nothing is reordered."""
    from facdrone_data.acquisition.validation import QuarantineUnmeasurable

    closes = [76.19, 76.59, 77.29, -221.22, -216.15, -216.15, 61.65, 63.29]
    frame = _prices(len(closes))
    frame["close"] = closes
    inputs = _inputs(EnsembleValidator(), QuarantineUnmeasurable())
    out = quality_gate(frame, _port("quarantine"), inputs, EURUSD)
    assert list(out["close"]) == [76.19, 76.59, 77.29, 61.65, 63.29]
    assert list(out.columns) == [c.name for c in DAILY_PRICES.columns]
