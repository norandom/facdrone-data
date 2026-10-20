# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Test support for the market-data store (the ``[testing]`` extra).

An embedded PostgreSQL per test process, an engine migrated by the module's own chain, a
deterministic offline market-data fake and the constants the suites share. A suite loads the
fixtures with ``pytest_plugins = ["facdrone_data.testing.plugin"]``.
"""

from __future__ import annotations

from facdrone_data_types.enums import AssetClass
from facdrone_data_types.symbols import CanonicalSymbol

# The synthetic credential the provider-adapter tests pass.
API_KEY = "test-key"  # noqa: S105 — synthetic

# The forex instrument the acquisition tests use.
EURUSD = CanonicalSymbol(canonical_id="EURUSD", asset_class=AssetClass.FOREX, profit_ccy="USD")
