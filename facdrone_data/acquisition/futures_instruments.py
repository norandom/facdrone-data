# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Interfacer: project futures sizing contracts into acquisition instrument records."""

from __future__ import annotations

from facdrone_data.acquisition.futures_specs import FuturesSpecTable as FuturesSpecTable
from facdrone_data.acquisition.instrument_table import InstrumentTable
from facdrone_data.acquisition.requirement import InstrumentRecord
from facdrone_data_types.enums import AssetClass


def futures_instrument_records(
    contracts: FuturesSpecTable, identities: InstrumentTable
) -> tuple[InstrumentRecord, ...]:
    """One record per sized series; no separate series for its other contracts.

    The seed usually selects a micro for sizing.
    Its code identifies the record, while acquisition always uses the parent
    series. Groups follow identity asset classes: the current table calls both
    metals and energy commodities, so both are ``Futures Commodities``.
    """
    records: dict[str, InstrumentRecord] = {}
    # The classes the futures book trades (operator, 2026-09-24): no crypto. A series of any
    # other class is refused here, so an unclassified or crypto row never reaches acquisition.
    supported = {AssetClass.INDICES, AssetClass.COMMODITIES, AssetClass.FOREX}
    for contract in contracts.rows:
        if not contract.sizing_contract:
            continue
        series = contract.parent_series
        if series in records:
            raise ValueError(f"{series}: multiple sizing contracts")
        identity = identities.identity(series)
        if "futures" not in identity.kinds:
            raise ValueError(f"{series}: sizing contract requires a futures identity")
        if identity.asset_class not in supported:
            raise ValueError(
                f"{series}: class {identity.asset_class.value!r} is not traded by the futures book"
            )
        label = {
            AssetClass.INDICES: "Index",
            AssetClass.FOREX: "FX",
            AssetClass.PRECIOUS_METALS: "Metals",
        }.get(identity.asset_class, identity.asset_class.value.title())
        records[series] = InstrumentRecord(contract.symbol, f"Futures {label}", series, "USD")
    return tuple(records[series] for series in sorted(records))
