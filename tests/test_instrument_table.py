# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Series identity refusal, coverage and round trips over injected identity files."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml

import facdrone_data
from facdrone_data.acquisition.families.symbol_mapping import (
    ProviderSymbols,
    to_fmp_price,
)
from facdrone_data.acquisition.instrument_table import InstrumentTable, InstrumentTableError
from facdrone_data_types.enums import AssetClass
from facdrone_data_types.symbols import CanonicalSymbol, Provider
from futures_support import IDENTITY_PATHS

FALLBACK_SAMPLES = {"EURUSD=X": "EURUSD", "BTC-USD": "BTCUSD", "^GSPC": "^GSPC", "ZW=F": None}


def _table(directory: Path | None = None) -> InstrumentTable:
    """The generic fixture identities, or the two files a test wrote into ``directory``."""
    if directory is None:
        return InstrumentTable(IDENTITY_PATHS)
    return InstrumentTable((directory / "cfd.yml", directory / "futures_identities.yml"))


def _row(canonical: str = "ES=F") -> dict[str, object]:
    return {
        "canonical": canonical,
        "kind": "futures",
        "asset_class": "indices",
        "settlement_ccy": "USD",
        "providers": {"fmp": "ESUSD", "yfinance": canonical},
        "intraday": "fmp",
    }


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(yaml.safe_dump(rows, sort_keys=False), encoding="utf-8")


def test_fmp_mapping_fallback_for_symbols_outside_table() -> None:
    # These samples are now in the shipped table; an empty valid table exercises fallback.
    for symbol, expected in (FALLBACK_SAMPLES | {"UNLISTED": "UNLISTED"}).items():
        assert to_fmp_price(symbol, InstrumentTable(())) == expected


def test_fmp_mapping_reads_only_the_injected_table(tmp_path: Path) -> None:
    _write(tmp_path / "one.yml", [_row()])
    injected = InstrumentTable((tmp_path / "one.yml",))
    assert to_fmp_price("ES=F", injected) == "ESUSD"
    assert to_fmp_price("6E=F", injected) == "6E=F"  # outside this table: the pass-through fallback
    assert ProviderSymbols(injected).yfinance_fallback("ES=F") is None
    with pytest.raises(InstrumentTableError):
        InstrumentTable((tmp_path / "absent.yml",))


def test_no_data_module_names_a_consumer_configuration_path() -> None:
    """The module reads files only from paths its caller gives (R3.5): no ``conf/`` path of a
    consumer's repository and no lookup relative to its own install location."""
    package = Path(facdrone_data.__file__).parent
    offenders = [
        str(path.relative_to(package))
        for path in sorted(package.rglob("*.py"))
        if "conf/" in (text := path.read_text(encoding="utf-8")) or "parents[" in text
    ]
    assert offenders == []


def test_all_non_null_provider_symbols_round_trip() -> None:
    table = _table()
    for sym in {*table.by_kind("cfd"), *table.by_kind("futures")}:
        for provider, symbol in table.identity(sym).providers.items():
            if symbol is not None:
                assert table.canonical_for(provider, symbol) == sym
                assert table.provider_symbol(sym, provider) == symbol
    with pytest.raises(InstrumentTableError, match="UNKNOWN"):
        table.identity("UNKNOWN")
    with pytest.raises(InstrumentTableError, match="UNKNOWN"):
        table.canonical_for(Provider.FMP, "UNKNOWN")


def test_duplicate_canonical_across_files_is_refused(tmp_path: Path) -> None:
    _write(tmp_path / "cfd.yml", [_row()])
    _write(tmp_path / "futures_identities.yml", [_row()])
    with pytest.raises(InstrumentTableError, match="duplicate canonical.*ES=F"):
        _table(tmp_path)


@pytest.mark.parametrize("provider", ["fmp", "yfinance"])
def test_duplicate_provider_symbol_is_refused(tmp_path: Path, provider: str) -> None:
    other = _row("NQ=F")
    other["providers"] = {
        "fmp": "NQUSD",
        "yfinance": "NQ=F",
        provider: "ESUSD" if provider == "fmp" else "ES=F",
    }
    _write(tmp_path / "cfd.yml", [_row()])
    _write(tmp_path / "futures_identities.yml", [other])
    with pytest.raises(InstrumentTableError, match=f"NQ=F.*duplicate.*{provider}"):
        _table(tmp_path)


@pytest.mark.parametrize(
    "missing", ["canonical", "kind", "asset_class", "settlement_ccy", "providers", "intraday"]
)
def test_incomplete_row_names_file_and_row(tmp_path: Path, missing: str) -> None:
    row = _row()
    del row[missing]
    _write(tmp_path / "cfd.yml", [row])
    _write(tmp_path / "futures_identities.yml", [])
    with pytest.raises(InstrumentTableError, match=r"cfd.yml.*row 1"):
        _table(tmp_path)


@pytest.mark.parametrize(
    "changes",
    [
        {"providers": {"yfinance": "ES=F"}},
        {"providers": {"fmp": None, "yfinance": "ES=F"}},
        {"providers": {"fmp": "ESUSD", "yfinance": None}},
        {"providers": {"fmp": " ", "yfinance": "ES=F"}},
        {"settlement_ccy": "usd"},
        {"asset_class": "unknown"},
        {"shared_series": True},
        {"kinds": ["cfd", "futures"]},
        {"intraday": "yfinance"},
        {"intraday_zone": "Europe/Atlantis"},
        {"intraday_zone": "europe/london"},
        {"intraday_zone": "../etc/passwd"},
        {"intraday_zone": ""},
    ],
)
def test_invalid_declarations_are_refused(tmp_path: Path, changes: dict[str, object]) -> None:
    _write(tmp_path / "cfd.yml", [_row() | changes])
    _write(tmp_path / "futures_identities.yml", [])
    with pytest.raises(InstrumentTableError, match="ES=F"):
        _table(tmp_path)


def test_coverage_partitions_universe_and_rows_are_frozen() -> None:
    table = _table()
    known = table.identity("GC=F").canonical
    unknown = CanonicalSymbol("UNKNOWN", AssetClass.INDICES, "USD")
    report = table.coverage(iter([unknown, known, known]))
    assert report.with_identity == ("GC=F",)
    assert report.without_identity == ("UNKNOWN",)
    assert set(report.outside) == {
        s.canonical_id for s in (*table.by_kind("cfd"), *table.by_kind("futures"))
    } - {"GC=F"}
    assert table.coverage(["GC=F", "UNKNOWN"]) == report
    with pytest.raises(FrozenInstanceError):
        report.outside = ()  # type: ignore[misc]
    row = table.identity(known)
    with pytest.raises(FrozenInstanceError):
        row.intraday = "unavailable"  # type: ignore[misc]
    with pytest.raises(TypeError):
        row.providers[Provider.FMP] = "wrong"  # type: ignore[index]


def test_without_intraday_reads_the_declared_field(tmp_path: Path) -> None:
    unserved = _row("6E=F") | {"providers": {"fmp": None, "yfinance": "6E=F"}}
    _write(tmp_path / "one.yml", [_row(), unserved | {"intraday": "unavailable"}])
    assert InstrumentTable((tmp_path / "one.yml",)).without_intraday() == frozenset({"6E=F"})
    assert InstrumentTable(()).without_intraday() == frozenset()


def test_intraday_zone_reads_the_row_else_the_provider_default(tmp_path: Path) -> None:
    unserved = _row("6E=F") | {"providers": {"fmp": None, "yfinance": "6E=F"}}
    london = _row("^FTSE") | {"providers": {"fmp": "^FTSE", "yfinance": "^FTSE"}}
    london |= {"intraday_zone": "Europe/London"}
    _write(tmp_path / "one.yml", [_row(), london, unserved | {"intraday": "unavailable"}])
    table = InstrumentTable((tmp_path / "one.yml",))
    assert table.intraday_zone("^FTSE") == "Europe/London"
    assert table.intraday_zone("ES=F") == "America/New_York"  # FMP's declared zone
    assert table.identity("ES=F").intraday_zone is None  # as declared: nothing
    assert table.intraday_zone("6E=F") is None  # no provider stamps its bars
    with pytest.raises(InstrumentTableError, match="no identity row"):
        table.intraday_zone("UNLISTED")
    zones = table.intraday_zones()  # the resolver the readers of a stored stamp take
    assert zones.declared == {"^FTSE": "Europe/London"}
    assert [zones.of(s) for s in ("^FTSE", "ES=F", "UNLISTED")] == [
        "Europe/London",
        "America/New_York",
        "America/New_York",
    ]


def test_a_zone_on_an_unserved_row_is_refused(tmp_path: Path) -> None:
    unserved = _row("6E=F") | {"providers": {"fmp": None, "yfinance": "6E=F"}}
    _write(
        tmp_path / "one.yml",
        [unserved | {"intraday": "unavailable", "intraday_zone": "Europe/Berlin"}],
    )
    with pytest.raises(InstrumentTableError, match=r"(?s)6E=F.*declares no intraday_zone"):
        InstrumentTable((tmp_path / "one.yml",))
