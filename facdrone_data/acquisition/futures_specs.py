# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Interfacer: catalog ``futures_contract_specs`` data to validated contract rows.

Use ``load_futures_contract_specs(catalog.load("futures_contract_specs"))`` with
the consumer's parquet catalog dataset. Instrument versioning through
``instrument_metadata_version`` lives in ``futures_spec_versions`` (task 8.3);
this loader validates catalog data before that writer persists it.

The seed's ``verify`` column lists operator checks, not domain fields:
``initial_margin`` is a 0.0 placeholder on every row. ``fees_per_side`` is a flat
conservative exchange+NFA estimate ($1.50 mini, $0.50 micro): the venue does not state
exchange/NFA fee amounts and defers to CME's schedule. Standard commissions use the
operator-directed $0.50 mini rate; the venue does not separately name a standard rate.
MBT/MET ``session_open``/``session_close`` retain flagged legacy 18:00/17:00 ET
bounds: CME moved crypto to 24/7 on 2026-05-29, which two daily time strings do
not describe. Confirm the venue's usable hours before calendar wiring. Sessions
here are product hours, not a venue's own forced-flat deadline (16:10 at one venue).
The supplied outright multipliers/ticks, including M6E 0.0001 -> $1.25, are
seed data; any unconfirmed figures are listed by name in ``verify``. These flags
do not remove sizing coverage or certify that a row is ready for live trading.
MBT/MET are micro-only offerings: ``micro_of=None`` declares no in-table parent,
while their explicit weight and commission still identify them as micros.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from math import isfinite

import pandas as pd
from pydantic import TypeAdapter

from facdrone_data_types.instruments import FuturesContract

_CONTRACT = TypeAdapter(FuturesContract)


@dataclass(frozen=True)
class CoverageReport:
    with_sizing_contract: tuple[str, ...]
    without_sizing_contract: tuple[str, ...]
    outside_universe: tuple[FuturesContract, ...]


@dataclass(frozen=True)
class FuturesSpecTable:
    rows: tuple[FuturesContract, ...]

    def __post_init__(self) -> None:
        by_symbol: dict[str, FuturesContract] = {}
        for row in self.rows:
            if row.symbol in by_symbol:
                raise ValueError(f"{row.symbol}: duplicate contract row")
            by_symbol[row.symbol] = row
        for row in self.rows:
            if row.micro_of is not None and row.micro_of not in by_symbol:
                raise ValueError(f"{row.symbol}: micro_of parent {row.micro_of!r} is absent")

    def coverage(self, universe_series: Iterable[str]) -> CoverageReport:
        """Report sizing coverage by canonical series, with out-of-universe rows."""
        universe = set(universe_series)
        sized = {row.parent_series for row in self.rows if row.sizing_contract}
        return CoverageReport(
            with_sizing_contract=tuple(sorted(universe & sized)),
            without_sizing_contract=tuple(sorted(universe - sized)),
            outside_universe=tuple(row for row in self.rows if row.parent_series not in universe),
        )


def _validate_boundary(row: FuturesContract) -> None:
    for name in ("symbol", "exchange", "parent_series"):
        if not getattr(row, name).strip():
            raise ValueError(f"{name} must not be blank")
    for name in ("session_open", "session_close"):
        if re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", getattr(row, name)) is None:
            raise ValueError(f"{name} must be an HH:MM ET time")
    for name in ("commission_per_side", "fees_per_side", "initial_margin"):
        value = getattr(row, name)
        if not isfinite(value) or value < 0:
            raise ValueError(f"{name} must be nonnegative and finite")


def load_futures_contract_specs(frame: pd.DataFrame) -> FuturesSpecTable:
    """Load the catalog frame, refusing invalid rows with their contract and index.

    All domain fields are required, including an explicit nullable ``micro_of``.
    Pandas missing values become ``None``; only ``micro_of`` permits that value.
    Seed audit columns such as ``verify`` are not copied into the domain record.
    """
    rows: list[FuturesContract] = []
    clean = frame.astype(object).where(pd.notna(frame), None)
    for index, record in zip(frame.index, clean.to_dict(orient="records"), strict=True):
        try:
            row = _CONTRACT.validate_python(record)
            _validate_boundary(row)
        except ValueError as error:
            raise ValueError(
                f"{record.get('symbol', '<missing>')} (row {index}): {error}"
            ) from error
        rows.append(row)
    return FuturesSpecTable(tuple(rows))
