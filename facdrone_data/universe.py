# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""What a run acquires, chosen at the composition root (R3.2, R3.4, R3.5).

:class:`UniverseSource` is the one seam both entry points bind: the module's
:class:`DeclaredUniverse` derives the requirement from its configuration file; a consuming
system binds its own universe derivation. Both return the same ``DataRequirement``, so the
runner cannot tell them apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from facdrone_data.acquisition.families.registry import download_families
from facdrone_data.acquisition.instrument_table import InstrumentTable
from facdrone_data.acquisition.requirement import DataRequirementService, InstrumentRecord

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import Engine

    from facdrone_data.acquisition.requirement import DataRequirement
    from facdrone_data.config import DataManagerConfig
    from facdrone_data_types.enums import AssetClass


class UniverseSource(Protocol):
    """The requirement one run acquires, as known at ``known_at``."""

    def requirement(self, engine: Engine, known_at: datetime) -> DataRequirement: ...


@dataclass(frozen=True)
class _Admission:
    asset_class: AssetClass
    groups: frozenset[str]
    symbols: frozenset[str] | None = None


@dataclass(frozen=True)
class _Admits:
    admits: tuple[_Admission, ...]


@dataclass(frozen=True)
class _DeclaredClaim:
    universe: _Admits


@dataclass(frozen=True)
class DeclaredUniverse:
    """The universe a configuration file declares: each instrument admitted by its class."""

    config: DataManagerConfig

    def requirement(self, engine: Engine, known_at: datetime) -> DataRequirement:
        """Derive the requirement; a declared universe reads nothing from the database."""
        del engine, known_at
        instruments = self.config.instruments
        records = tuple(
            InstrumentRecord(
                record_id=entry.canonical,
                group=entry.asset_class.value,
                acquisition_symbol=entry.canonical,
                profit_currency=entry.profit_ccy,
            )
            for entry in instruments
        )
        classes = sorted({entry.asset_class for entry in instruments})
        claim = _DeclaredClaim(
            _Admits(tuple(_Admission(item, frozenset({item.value})) for item in classes))
        )
        feeds = tuple(
            port.writer
            for port in download_families().families()
            if "symbol" not in port.natural_key
        )
        return DataRequirementService(
            records,
            global_inputs=self.config.global_inputs,
            configured_feeds=feeds,
            recorded_series=self.config.recorded_series,
            identities=InstrumentTable(self.config.identity_files),
            conversion_target_currency=self.config.conversion_currency,
        ).derive((claim,))
