# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Provider symbol translation (R5.1, R5.2, R5.3).

Price identities come from the instrument table; the other mappings are fixed tables:

* ``to_fmp_price`` (the injected instrument identity table) — YF→FMP price
  endpoint symbol; ``None`` when FMP has no equivalent (e.g. wheat ``ZW=F``), which
  routes the symbol to the yfinance fallback (D8).
* ``to_fmp`` / ``has_cot`` (``_YF_TO_FMP``) — YF→FMP
  fundamentals/COT symbol and the commodity COT-eligibility check.
* ``CFTC_MAP`` / ``cftc_market_to_yf`` — the CFTC market
  name → YF symbol map, deliberately **non-bijective** (crude/natgas names fold to
  ``CL=F``/``NG=F``); kept outside the round-trip-validated registry (D9).

Price symbols not yet in the table use a forex/crypto/identity fallback.
The identity table is injected: the module reads no identity file of its own
(task 1.4). :class:`ProviderSymbols` binds the family mappers to one
table; the families declare which mapper they use, and the run supplies the table.
Explicit table nulls keep the yfinance route.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from facdrone_data.acquisition.instrument_table import InstrumentTable, InstrumentTableError
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from facdrone_data_types.enums import AssetClass

# YF → FMP fundamentals/COT symbol (symbol_map.py:21). Commodities drop the "=F"
# to their COT report symbol; equities/indices are identity.
_YF_TO_FMP: Final[dict[str, str]] = {
    "ALV.DE": "ALV.DE",
    "DBK.DE": "DBK.DE",
    "BAYN.DE": "BAYN.DE",
    "VOW3.DE": "VOW3.DE",
    "AF.PA": "AF.PA",
    "MC.PA": "MC.PA",
    "IBE.MC": "IBE.MC",
    "GOOG": "GOOG",
    "GC=F": "GC",
    "SI=F": "SI",
    "CL=F": "CL",
    "BZ=F": "BZ",
    "NG=F": "NG",
    "HG=F": "HG",
    "PA=F": "PA",
    "PL=F": "PL",
    "KC=F": "KC",
    "SB=F": "SB",
    "CC=F": "CC",
    "CT=F": "CT",
    "ZC=F": "ZC",
    "ZS=F": "ZS",
    "ZW=F": "ZW",
    "HO=F": "HO",
    "^GDAXI": "^GDAXI",
    "^FTSE": "^FTSE",
    "^NDX": "^NDX",
    "^DJI": "^DJI",
    "^GSPC": "^GSPC",
    "^AXJO": "^AXJO",
    "^STOXX50E": "^STOXX50E",
    "^FCHI": "^FCHI",
    "^HSI": "^HSI",
    "^N225": "^N225",
    "^AEX": "^AEX",
    "^IBEX": "^IBEX",
    "^RUT": "^RUT",
    "DX-Y.NYB": "DX-Y.NYB",
}

# CFTC market name → YF symbol. Non-bijective on purpose.
CFTC_MAP: Final[dict[str, str]] = {
    "GOLD - COMMODITY EXCHANGE INC.": "GC=F",
    "SILVER - COMMODITY EXCHANGE INC.": "SI=F",
    "COPPER- #1 - COMMODITY EXCHANGE INC.": "HG=F",
    "CRUDE OIL, LIGHT SWEET - NEW YORK MERCANTILE EXCHANGE": "CL=F",
    "WTI FINANCIAL CRUDE OIL - NEW YORK MERCANTILE EXCHANGE": "CL=F",
    "NATURAL GAS - NEW YORK MERCANTILE EXCHANGE": "NG=F",
    "E-MINI NATURAL GAS - NEW YORK MERCANTILE EXCHANGE": "NG=F",
    "NY HARBOR ULSD - NEW YORK MERCANTILE EXCHANGE": "HO=F",
    "CORN - CHICAGO BOARD OF TRADE": "ZC=F",
    "SOYBEANS - CHICAGO BOARD OF TRADE": "ZS=F",
    "WHEAT-SRW - CHICAGO BOARD OF TRADE": "ZW=F",
    "COTTON NO. 2 - ICE FUTURES U.S.": "CT=F",
    "COFFEE C - ICE FUTURES U.S.": "KC=F",
    "COCOA - ICE FUTURES U.S.": "CC=F",
    "SUGAR NO. 11 - ICE FUTURES U.S.": "SB=F",
    "PLATINUM - NEW YORK MERCANTILE EXCHANGE": "PL=F",
    "PALLADIUM - NEW YORK MERCANTILE EXCHANGE": "PA=F",
}


def to_fmp_price(yf_symbol: str, table: InstrumentTable) -> str | None:
    """Table price symbol (including explicit ``None``), with a fallback for absent rows."""
    try:
        return table.provider_symbol(yf_symbol, Provider.FMP)
    except InstrumentTableError:
        pass  # Symbols not yet in the identity table retain their previous spelling.
    if yf_symbol.endswith("=X"):
        return yf_symbol[:-2]  # forex: EURUSD=X → EURUSD
    if yf_symbol.endswith("-USD"):
        return yf_symbol.replace("-", "")  # crypto: BTC-USD → BTCUSD
    if yf_symbol == "ZW=F":
        return None  # wheat: no FMP price — keep Yahoo
    return yf_symbol  # indices/equities: identity


def to_fmp(yf_symbol: str) -> str:
    """YF → FMP fundamentals/COT symbol (identity when not in the table)."""
    return _YF_TO_FMP.get(yf_symbol, yf_symbol)


def has_cot(yf_symbol: str, asset_class: AssetClass) -> bool:
    """Commodity COT eligibility: the symbol is in the FMP table
    AND it is a commodity."""
    from facdrone_data_types.enums import AssetClass

    return yf_symbol in _YF_TO_FMP and asset_class == AssetClass.COMMODITIES


def cftc_market_to_yf(market_name: str) -> str | None:
    """CFTC market name → YF symbol, or ``None`` for an unmapped market (skip)."""
    return CFTC_MAP.get(market_name)


# ── Dukascopy tick feed ─────────────────────────────────────────────────────
# YF/futures → Dukascopy symbol. facdrone's canonical forex/
# metals (EURUSD, XAUUSD) are already valid Dukascopy names, so `to_duka` resolves
# them directly and uses this table only for the futures-style canonicals (GC=F).
_YF_TO_DUKA: Final[dict[str, str]] = {
    "GC=F": "XAUUSD",
    "SI=F": "XAGUSD",
    "PL=F": "XPTUSD",
    "PA=F": "XPDUSD",
    "HG=F": "XCUUSD",
    "CL=F": "USOUSD",
    "NG=F": "NGUSD",
    "HO=F": "HOOUSD",
    "CC=F": "COCOUSD",
    "KC=F": "COFUSD",
    "CT=F": "COTUSD",
    "SB=F": "SUGUSD",
    "ZC=F": "CORNUSD",
    "ZS=F": "SOYUSD",
    "ZW=F": "WHTUSD",
    "EURUSD=X": "EURUSD",
    "GBPUSD=X": "GBPUSD",
    "USDJPY=X": "USDJPY",
    "USDCHF=X": "USDCHF",
    "AUDUSD=X": "AUDUSD",
    "NZDUSD=X": "NZDUSD",
    "USDCAD=X": "USDCAD",
    "EURJPY=X": "EURJPY",
    "GBPJPY=X": "GBPJPY",
    "EURGBP=X": "EURGBP",
    "EURAUD=X": "EURAUD",
    "EURCHF=X": "EURCHF",
}

# Dukascopy symbols actually carried on the tick feed; an
# unmapped/off-feed symbol is skipped rather than fetched.
_DUKA_TICK_FEED: Final[frozenset[str]] = frozenset(
    {
        "AUDCAD",
        "AUDCHF",
        "AUDJPY",
        "AUDNZD",
        "AUDUSD",
        "CADCHF",
        "CADJPY",
        "CHFJPY",
        "EURAUD",
        "EURCAD",
        "EURCHF",
        "EURCZK",
        "EURGBP",
        "EURHUF",
        "EURJPY",
        "EURNOK",
        "EURNZD",
        "EURPLN",
        "EURUSD",
        "GBPAUD",
        "GBPCAD",
        "GBPCHF",
        "GBPJPY",
        "GBPNZD",
        "GBPUSD",
        "NZDCAD",
        "NZDCHF",
        "NZDJPY",
        "NZDUSD",
        "USDCAD",
        "USDCHF",
        "USDCNH",
        "USDCZK",
        "USDHKD",
        "USDHUF",
        "USDILS",
        "USDJPY",
        "USDMXN",
        "USDNOK",
        "USDPLN",
        "USDSEK",
        "USDSGD",
        "USDZAR",
        "XAGUSD",
        "XAUUSD",
        "AUSIDXAUD",
        "DEUIDXEUR",
        "DOLLARIDXUSD",
        "ESPIDXEUR",
        "EUSIDXEUR",
        "FRAIDXEUR",
        "GBRIDXGBP",
        "HKGIDXHKD",
        "JPNIDXJPY",
        "NLDIDXEUR",
        "USA30IDXUSD",
        "USA500IDXUSD",
        "USATECHIDXUSD",
    }
)


def to_duka(canonical: str) -> str | None:
    """Canonical/YF symbol → Dukascopy tick symbol, or ``None`` if off-feed (skip).

    facdrone canonical forex/metals (``EURUSD``/``XAUUSD``) are already Dukascopy
    names, so they resolve directly; futures-style symbols (``GC=F``) map through
    ``_YF_TO_DUKA``. Anything not on the tick feed returns ``None``."""
    if canonical in _DUKA_TICK_FEED:
        return canonical
    duka = _YF_TO_DUKA.get(canonical)
    return duka if duka is not None and duka in _DUKA_TICK_FEED else None


@dataclass(frozen=True)
class ProviderSymbols:
    """The family symbol mappers bound to one injected identity table.

    A family port names its mapper as an unbound method (``ProviderSymbols.fmp_price``);
    the refresher calls it with the run's instance, so the table is data the composition
    root supplies, never a file this module finds.
    """

    identities: InstrumentTable

    def fmp_price(self, yf_symbol: str) -> str | None:
        """The FMP daily/intraday price symbol (R2.1)."""
        return to_fmp_price(yf_symbol, self.identities)

    def yfinance_fallback(self, yf_symbol: str) -> str | None:
        """yfinance covers ONLY the symbols FMP cannot price (R4.4, D8)."""
        return yf_symbol if self.fmp_price(yf_symbol) is None else None

    def fmp(self, yf_symbol: str) -> str | None:
        """The FMP fundamentals/COT symbol."""
        return to_fmp(yf_symbol)

    def duka(self, canonical: str) -> str | None:
        """The Dukascopy tick symbol, or ``None`` off the feed."""
        return to_duka(canonical)
