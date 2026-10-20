# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The asset-class vocabulary shared by symbol identity and the instrument tables.

Values are pinned: persisted artifacts (run records, state snapshots) store
them, so changing a value breaks comparison with earlier stored rows.
"""

from __future__ import annotations

from enum import StrEnum


class AssetClass(StrEnum):
    """The asset-class keys of the reference implementation's market mapping."""

    FOREX = "forex"
    INDICES = "indices"
    COMMODITIES = "commodities"
    AGRICULTURE = "agriculture"
    PRECIOUS_METALS = "precious metals"
    EXOTICS = "exotics"
    EQUITIES_US = "equities us"
    EQUITIES_I18N = "equities i18n"
    CRYPTO = "crypto"
