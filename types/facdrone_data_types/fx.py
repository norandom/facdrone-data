# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
from dataclasses import dataclass
from enum import StrEnum


class FxUnavailableReason(StrEnum):
    """Stable identities for a refused conversion (they land in the caller's audit evidence)."""

    SOURCE_CURRENCY_INVALID = "fx.source_currency_invalid"
    QUOTE_UNUSABLE = "fx.quote_unusable"
    NO_CONVERSION_PATH = "fx.no_conversion_path"


@dataclass(frozen=True)
class FxUnavailable:
    """No conversion could be resolved. Carries no rate — by construction, callers
    cannot read a number out of it and must fail closed (R6.2, R6.3)."""

    source_ccy: str
    reason: str
