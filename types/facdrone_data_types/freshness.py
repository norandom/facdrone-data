# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""How much an input's age is allowed to be trusted: the shared freshness state.

The verdicts over live snapshots stay with their consumers; only the state vocabulary,
its severity order and the derived-metrics permission live here.
"""

from __future__ import annotations

from enum import StrEnum


class FreshnessState(StrEnum):
    """How much an input's age is allowed to be trusted.

    Declared worst-last so `max` over the members is the worst-input rule itself.
    """

    CURRENT = "current"
    WARNING = "warning"
    STALE = "stale"
    UNAVAILABLE = "unavailable"

    @property
    def severity(self) -> int:
        return _SEVERITY[self]

    @property
    def permits_derived_metrics(self) -> bool:
        """WARNING still permits derivation; STALE does not.

        A warning says "look at this soon", not "this number is wrong". Refusing to
        compute at the first warning would make the tab useless during ordinary market
        gaps, and operators learn to ignore a control that cries wolf.
        """
        return self in (FreshnessState.CURRENT, FreshnessState.WARNING)


_SEVERITY: dict[FreshnessState, int] = {
    FreshnessState.CURRENT: 0,
    FreshnessState.WARNING: 1,
    FreshnessState.STALE: 2,
    FreshnessState.UNAVAILABLE: 3,
}
