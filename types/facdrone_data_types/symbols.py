# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Canonical instrument identity and the provider enumeration (task 1.1).

One canonical id bridges every provider's symbol (yfinance ``EURUSD=X``,
Dukascopy ``EURUSD``, FMP, a broker venue). The value object carries the
asset class/group and profit currency that downstream sizing and FX
conversion need, in one place instead of ad-hoc lookups scattered across
modules (R3.1, R3.5).

Frozen, hashable, stdlib-only — this is the typed core (no IO, no
framework imports). Translation lives in the registry (pipelines/data);
this module only defines the identity it translates to and from.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from facdrone_data_types.enums import AssetClass


class Provider(StrEnum):
    """Every external data/broker source behind the acquisition contract.

    The acquisition port extends this single source-of-truth enum additively
    (facdrone-data-acquisition D10): ``CFTC``/``KEN_FRENCH``/``OECD``/``ETF`` are
    the distinct non-FMP/non-FRED sources. No parallel ``Source`` enum exists —
    ``DownloadInputs.clients`` and the symbol registry key on this one enum.
    """

    YFINANCE = "yfinance"
    FMP = "fmp"
    DUKASCOPY = "dukascopy"
    FRED = "fred"
    CFTC = "cftc"
    KEN_FRENCH = "ken_french"
    OECD = "oecd"
    ETF = "etf"
    AQR = "aqr"  # AQR Data Library — Quality-Minus-Junk (QMJ) and Betting-Against-Beta (BAB)


@dataclass(frozen=True)
class CanonicalSymbol:
    """The single canonical identifier for one tradeable instrument.

    ``profit_ccy`` is the currency positions settle in (the quote currency
    of an FX pair, the contract currency otherwise) — the input to FX-to-EUR
    conversion. ``asset_class`` reuses the existing domain enum so sizing and
    threshold logic share one vocabulary.
    """

    canonical_id: str
    asset_class: AssetClass
    profit_ccy: str

    def __post_init__(self) -> None:
        if not self.canonical_id:
            raise ValueError("canonical_id must be non-empty")
        if not (
            len(self.profit_ccy) == 3 and self.profit_ccy.isalpha() and self.profit_ccy.isupper()
        ):
            raise ValueError(
                f"profit_ccy must be a 3-letter uppercase ISO code, got {self.profit_ccy!r}"
            )
