# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The explicit family registry (R1.2, R2.1, R2.5).

Mirrors ``factors.registry.FactorRegistry``: a private list, mutation only
through :meth:`register`, and ``download_families()`` returns THE explicit,
code-reviewed set — never discovery or a naming convention. The coverage test
(``tests/unit/test_data_acquisition_inventory.py``) asserts, against the §0 Port
Disposition Ledger, that every registered ``writer`` is a PORT writer (no
phantom ports) and — once all ports land (task 11.1) — that every PORT writer is
registered.

Port families are added to ``download_families()`` as tasks 5–8 implement them;
the full set is consolidated and the coverage gate turned green at task 11.1.
"""

from __future__ import annotations

from facdrone_data.acquisition.families.ports import FamilyPort


class FamilyRegistry:
    """Explicit registry; mutation only through :meth:`register`."""

    def __init__(self) -> None:
        self._families: list[FamilyPort] = []

    def register(self, port: FamilyPort) -> None:
        if port.natural_key != tuple(c.name for c in port.target_table.primary_key.columns):
            raise ValueError(
                f"{port.writer}: natural_key {port.natural_key} does not match "
                f"{port.target_table.name} primary key"
            )
        existing = {p.writer for p in self._families}
        if port.writer in existing:
            raise ValueError(f"family {port.writer!r} already registered")
        self._families.append(port)

    def families(self) -> tuple[FamilyPort, ...]:
        return tuple(self._families)


def download_families() -> FamilyRegistry:
    """THE explicit set of acquisition family ports (R1.2, R2.5).

    All 26 PORT writers from the §0 ledger are registered here (task 11.1); the
    coverage gate (``tests/unit/test_data_acquisition_inventory.py``) asserts the
    set is complete and free of phantom ports."""
    from facdrone_data.acquisition.families.normalizers.analyst import analyst_families
    from facdrone_data.acquisition.families.normalizers.cftc import cftc_families
    from facdrone_data.acquisition.families.normalizers.cot import cot_families
    from facdrone_data.acquisition.families.normalizers.dukascopy import dukascopy_families
    from facdrone_data.acquisition.families.normalizers.factor_returns import (
        factor_returns_families,
    )
    from facdrone_data.acquisition.families.normalizers.fama_french import fama_french_families
    from facdrone_data.acquisition.families.normalizers.fred import fred_families
    from facdrone_data.acquisition.families.normalizers.fundamentals import fundamentals_families
    from facdrone_data.acquisition.families.normalizers.macro import macro_families
    from facdrone_data.acquisition.families.normalizers.news import news_families
    from facdrone_data.acquisition.families.normalizers.oecd_etf import oecd_etf_families
    from facdrone_data.acquisition.families.normalizers.ownership import ownership_families
    from facdrone_data.acquisition.families.normalizers.prices import price_families

    registry = FamilyRegistry()
    for factory in (
        price_families,
        fundamentals_families,
        analyst_families,
        ownership_families,
        news_families,
        cot_families,
        macro_families,
        fred_families,
        cftc_families,
        fama_french_families,
        factor_returns_families,
        oecd_etf_families,
        dukascopy_families,
    ):
        for port in factory():
            registry.register(port)
    return registry
