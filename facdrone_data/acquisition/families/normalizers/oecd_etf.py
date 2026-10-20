# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""OECD/ETF-family normalizers + their FamilyPorts (group ``oecd_etf``; R2.1, R2.3, R6.2).

Three ports share a composition root because they form the ETF-adjacent ingest:

* ``refresh_oecd_japan_cpi`` → ``fred_series`` under the **sentinel** series id
  ``OECD_JPN_CPI``. The source is OECD COICOP2018 monthly Japan CPI as SDMX-CSV,
  stored in ``fred_series`` alongside the FRED series.
  ``raw`` here is the already-parsed ``(series_id, date, value)`` frame from the
  SDMX-CSV parser, which anchors ``TIME_PERIOD``
  ``YYYY-MM`` to the first of the month, floats ``OBS_VALUE``, and skips blank /
  non-parseable rows; the normalizer carries the same safe numeric coercion and
  skips any residual bad row. The browser-like ``User-Agent`` / SDMX ``Accept``
  headers are an adapter/transport concern (OECD 403s the default urllib UA) and
  are NOT part of the normalizer.

* ``upsert_etf_meta`` → ``etf_meta``. Syncs the static
  ``ETF_UNIVERSE`` into ``etf_meta``. NATIVE ETF tickers — no
  Yahoo→FMP mapping (the consumer addresses ETFs by their listed exchange ticker).

* ``refresh_etf_daily`` → ``etf_prices``. Works like
  ``refresh_fmp_daily`` but targets ``etf_prices`` and treats inputs as NATIVE FMP
  tickers — the symbol IS the native ETF ticker, never re-keyed to a YF symbol.
  Write-time cleaning: ``int(volume or 0)``, ``pd.to_datetime().dt.date``,
  ``dropna(subset=["close"])``, ``drop_duplicates(["symbol", "date"])``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from facdrone_data.acquisition.families.cursors import FullWindow, MinMaxRangeGapFill
from facdrone_data.acquisition.families.normalizers._base import (
    coerce_date,
    drop_null_and_dedup,
    project_to_table,
    stamp_updated_at,
)
from facdrone_data.acquisition.families.ports import FamilyPort
from facdrone_data.acquisition.merge import UpsertPanel
from facdrone_data.acquisition.schema import ETF_META, ETF_PRICES, FRED_SERIES
from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from datetime import datetime

    from facdrone_data_types.symbols import CanonicalSymbol

# The sentinel series id under
# which OECD Japan CPI is stored in ``fred_series`` (one series, not a universe).
_OECD_JPN_CPI_SERIES_ID = "OECD_JPN_CPI"

# The static ETF universe, order-preserving. Each entry:
# ``(symbol, name, wkn, isin, category, notes)``; ``wkn``/``isin``/``notes`` may be
# ``None``. NATIVE FMP/exchange tickers — no Yahoo→FMP mapping.
_ETF_META_COLUMNS = ("symbol", "name", "wkn", "isin", "category", "notes")
_ETF_UNIVERSE: tuple[tuple[str, str, str | None, str | None, str, str | None], ...] = (
    # ── user-requested ─────────────────────────────────────
    ("SPY", "SPDR S&P 500 ETF Trust", None, "US78462F1030", "user", None),
    ("TLT", "iShares 20+ Year Treasury Bond ETF", None, "US4642874329", "user", None),
    ("GLD", "SPDR Gold Shares", None, "US78463V1070", "user", None),
    ("DBC", "Invesco DB Commodity Index Tracking Fund", None, "US73935S1059", "user", None),
    (
        "FWRG.L",
        "Invesco FTSE All-World UCITS ETF",
        "A3D7QX",
        "IE000716YHJ7",
        "user",
        "LSE GBP listing of the A3D7QX share class",
    ),
    (
        "FTWD.DE",
        "Invesco FTSE All-World UCITS ETF",
        "A3D7QX",
        "IE000716YHJ7",
        "user",
        "Xetra EUR listing of the A3D7QX share class",
    ),
    (
        "CMOD.L",
        "Invesco Bloomberg Commodity UCITS ETF",
        "A2JN3K",
        "IE00BD6FTQ80",
        "user",
        "Unhedged USD share class — FMP has no data for the EUR-hedged CMHG.DE",
    ),
    # ── MSCI World — both US and UCITS variants ────────────
    ("URTH", "iShares MSCI World ETF", None, "US46434V6213", "world", "US-listed MSCI World"),
    (
        "IWDA.L",
        "iShares Core MSCI World UCITS ETF",
        "A0RPWH",
        "IE00B4L5Y983",
        "world",
        "USD acc UCITS",
    ),
    (
        "EUNL.DE",
        "iShares Core MSCI World UCITS ETF",
        "A0RPWH",
        "IE00B4L5Y983",
        "world",
        "Xetra EUR acc",
    ),
    (
        "SWDA.L",
        "iShares Core MSCI World UCITS ETF",
        "A0RPWH",
        "IE00B4L5Y983",
        "world",
        "LSE USD acc",
    ),
    # ── MSCI Emerging Markets ──────────────────────────────
    ("EEM", "iShares MSCI Emerging Markets ETF", None, "US4642872349", "em", None),
    ("IEMG", "iShares Core MSCI Emerging Markets ETF", None, "US46434G1031", "em", None),
    (
        "EIMI.L",
        "iShares Core MSCI EM IMI UCITS ETF",
        "A111X9",
        "IE00BKM4GZ66",
        "em",
        "UCITS USD acc",
    ),
    ("VWO", "Vanguard FTSE Emerging Markets ETF", None, "US9220428588", "em", None),
    # ── bond ETFs (US-listed, broad set) ───────────────────
    ("AGG", "iShares Core US Aggregate Bond ETF", None, "US4642872265", "bond", None),
    ("BND", "Vanguard Total Bond Market ETF", None, "US9219378356", "bond", None),
    ("BNDX", "Vanguard Total International Bond ETF", None, "US92203J4076", "bond", None),
    (
        "LQD",
        "iShares iBoxx $ Investment Grade Corporate Bond ETF",
        None,
        "US4642872422",
        "bond",
        None,
    ),
    ("HYG", "iShares iBoxx $ High Yield Corporate Bond ETF", None, "US4642885135", "bond", None),
    ("JNK", "SPDR Bloomberg High Yield Bond ETF", None, "US78468R6633", "bond", None),
    ("TIP", "iShares TIPS Bond ETF", None, "US4642871762", "bond", None),
    ("SHY", "iShares 1-3 Year Treasury Bond ETF", None, "US4642874576", "bond", None),
    ("IEF", "iShares 7-10 Year Treasury Bond ETF", None, "US4642874402", "bond", None),
    ("IEI", "iShares 3-7 Year Treasury Bond ETF", None, "US4642886612", "bond", None),
    ("SHV", "iShares Short Treasury Bond ETF", None, "US4642886877", "bond", None),
    ("GOVT", "iShares US Treasury Bond ETF", None, "US46429B6691", "bond", None),
    ("MUB", "iShares National Muni Bond ETF", None, "US4642886786", "bond", None),
    ("EMB", "iShares JP Morgan USD Emerging Markets Bond ETF", None, "US4642882819", "bond", None),
    ("MBB", "iShares MBS ETF", None, "US4642886695", "bond", None),
    ("VCIT", "Vanguard Intermediate-Term Corporate Bond ETF", None, "US92206C7034", "bond", None),
    ("VCSH", "Vanguard Short-Term Corporate Bond ETF", None, "US92206C8024", "bond", None),
    ("BIL", "SPDR Bloomberg 1-3 Month T-Bill ETF", None, "US78468R6633", "bond", None),
    # ── broad-market US equity ─────────────────────────────
    ("VOO", "Vanguard S&P 500 ETF", None, "US9229083632", "broad", None),
    ("IVV", "iShares Core S&P 500 ETF", None, "US4642872000", "broad", None),
    ("VTI", "Vanguard Total Stock Market ETF", None, "US9229087690", "broad", None),
    ("ITOT", "iShares Core S&P Total US Stock Market ETF", None, "US4642875565", "broad", None),
    ("SCHB", "Schwab US Broad Market ETF", None, "US8085245052", "broad", None),
    ("SPLG", "SPDR Portfolio S&P 500 ETF", None, "US78464A6982", "broad", None),
    ("QQQ", "Invesco QQQ Trust", None, "US46090E1038", "broad", None),
    ("QQQM", "Invesco NASDAQ 100 ETF", None, "US46138G6492", "broad", None),
    ("DIA", "SPDR Dow Jones Industrial Average ETF", None, "US78467X1090", "broad", None),
    # ── small / mid cap ────────────────────────────────────
    ("IWM", "iShares Russell 2000 ETF", None, "US4642876555", "smid", None),
    ("IJH", "iShares Core S&P Mid-Cap ETF", None, "US4642875078", "smid", None),
    ("IJR", "iShares Core S&P Small-Cap ETF", None, "US4642878502", "smid", None),
    ("VB", "Vanguard Small-Cap ETF", None, "US9229087286", "smid", None),
    ("VO", "Vanguard Mid-Cap ETF", None, "US9229084878", "smid", None),
    # ── style / factor ─────────────────────────────────────
    ("VTV", "Vanguard Value ETF", None, "US9229087443", "factor", None),
    ("VUG", "Vanguard Growth ETF", None, "US9229087369", "factor", None),
    ("IWF", "iShares Russell 1000 Growth ETF", None, "US4642876308", "factor", None),
    ("IWD", "iShares Russell 1000 Value ETF", None, "US4642876407", "factor", None),
    ("MTUM", "iShares MSCI USA Momentum Factor ETF", None, "US46432F8427", "factor", None),
    ("QUAL", "iShares MSCI USA Quality Factor ETF", None, "US46432F8344", "factor", None),
    ("USMV", "iShares MSCI USA Min Vol Factor ETF", None, "US46429B6907", "factor", None),
    ("VLUE", "iShares MSCI USA Value Factor ETF", None, "US46432F8260", "factor", None),
    ("SIZE", "iShares MSCI USA Size Factor ETF", None, "US46432F8500", "factor", None),
    ("SCHD", "Schwab US Dividend Equity ETF", None, "US8085247545", "factor", None),
    ("VIG", "Vanguard Dividend Appreciation ETF", None, "US9219088443", "factor", None),
    ("VYM", "Vanguard High Dividend Yield ETF", None, "US9219088260", "factor", None),
    ("DVY", "iShares Select Dividend ETF", None, "US4642875748", "factor", None),
    # ── international developed ex-US ───────────────────────
    ("VEA", "Vanguard FTSE Developed Markets ETF", None, "US9219438580", "intl", None),
    ("IEFA", "iShares Core MSCI EAFE ETF", None, "US46432F8427", "intl", None),
    ("EFA", "iShares MSCI EAFE ETF", None, "US4642874402", "intl", None),
    ("VXUS", "Vanguard Total International Stock ETF", None, "US9219097683", "intl", None),
    ("IXUS", "iShares Core MSCI Total International Stock ETF", None, "US46434G7657", "intl", None),
    ("EWJ", "iShares MSCI Japan ETF", None, "US4642868487", "intl", None),
    ("EWZ", "iShares MSCI Brazil ETF", None, "US4642864007", "intl", None),
    ("EWG", "iShares MSCI Germany ETF", None, "US4642867638", "intl", None),
    ("EWU", "iShares MSCI United Kingdom ETF", None, "US4642867216", "intl", None),
    ("FXI", "iShares China Large-Cap ETF", None, "US4642873065", "intl", None),
    ("MCHI", "iShares MSCI China ETF", None, "US46429B5984", "intl", None),
    ("INDA", "iShares MSCI India ETF", None, "US46429B6014", "intl", None),
    # ── US sectors (SPDR + iShares) ────────────────────────
    ("XLF", "Financial Select Sector SPDR Fund", None, "US81369Y6059", "sector", None),
    ("XLK", "Technology Select Sector SPDR Fund", None, "US81369Y8030", "sector", None),
    ("XLE", "Energy Select Sector SPDR Fund", None, "US81369Y5069", "sector", None),
    ("XLV", "Health Care Select Sector SPDR Fund", None, "US81369Y2090", "sector", None),
    ("XLI", "Industrial Select Sector SPDR Fund", None, "US81369Y7040", "sector", None),
    ("XLP", "Consumer Staples Select Sector SPDR Fund", None, "US81369Y3080", "sector", None),
    ("XLY", "Consumer Discretionary Select Sector SPDR Fund", None, "US81369Y4070", "sector", None),
    ("XLU", "Utilities Select Sector SPDR Fund", None, "US81369Y8865", "sector", None),
    ("XLB", "Materials Select Sector SPDR Fund", None, "US81369Y1001", "sector", None),
    ("XLRE", "Real Estate Select Sector SPDR Fund", None, "US81369Y8527", "sector", None),
    ("XLC", "Communication Services Select Sector SPDR", None, "US81369Y8600", "sector", None),
    ("SMH", "VanEck Semiconductor ETF", None, "US92189F4280", "sector", None),
    ("SOXX", "iShares Semiconductor ETF", None, "US4642875235", "sector", None),
    ("KRE", "SPDR S&P Regional Banking ETF", None, "US78464A8541", "sector", None),
    ("KBE", "SPDR S&P Bank ETF", None, "US78464A8624", "sector", None),
    ("ITB", "iShares US Home Construction ETF", None, "US4642886190", "sector", None),
    ("XBI", "SPDR S&P Biotech ETF", None, "US78464A6800", "sector", None),
    ("IBB", "iShares Biotechnology ETF", None, "US4642875235", "sector", None),
    ("IYR", "iShares US Real Estate ETF", None, "US4642877397", "sector", None),
    # ── commodities (broad + single) ───────────────────────
    ("IAU", "iShares Gold Trust", None, "US4642851053", "commodity", None),
    ("SLV", "iShares Silver Trust", None, "US46428Q1094", "commodity", None),
    ("GDX", "VanEck Gold Miners ETF", None, "US92189F1066", "commodity", None),
    ("GDXJ", "VanEck Junior Gold Miners ETF", None, "US92189F7253", "commodity", None),
    ("USO", "United States Oil Fund", None, "US91232N1081", "commodity", None),
    ("UNG", "United States Natural Gas Fund", None, "US9123181086", "commodity", None),
    (
        "PDBC",
        "Invesco Optimum Yield Diversified Commodity Strategy",
        None,
        "US46138E5575",
        "commodity",
        None,
    ),
    (
        "DJP",
        "iPath Bloomberg Commodity Index Total Return ETN",
        None,
        "US06739F8825",
        "commodity",
        None,
    ),
    # ── real estate / real assets ──────────────────────────
    ("VNQ", "Vanguard Real Estate ETF", None, "US9229085538", "real", None),
    ("SCHH", "Schwab US REIT ETF", None, "US8085247461", "real", None),
    (
        "REZ",
        "iShares Residential & Multisector Real Estate ETF",
        None,
        "US46428G7574",
        "real",
        None,
    ),
    # ── thematic / innovation ──────────────────────────────
    ("ARKK", "ARK Innovation ETF", None, "US00214Q1040", "thematic", None),
    ("ARKG", "ARK Genomic Revolution ETF", None, "US00214Q3046", "thematic", None),
    ("ARKW", "ARK Next Generation Internet ETF", None, "US00214Q2055", "thematic", None),
    ("ICLN", "iShares Global Clean Energy ETF", None, "US4642882754", "thematic", None),
    ("TAN", "Invesco Solar ETF", None, "US46138G6068", "thematic", None),
    ("LIT", "Global X Lithium & Battery Tech ETF", None, "US37954Y8553", "thematic", None),
    ("ROBO", "ROBO Global Robotics & Automation Index ETF", None, "US37950E5490", "thematic", None),
    (
        "BOTZ",
        "Global X Robotics & Artificial Intelligence ETF",
        None,
        "US37954Y8801",
        "thematic",
        None,
    ),
    # ── volatility / hedge ─────────────────────────────────
    (
        "VXX",
        "iPath Series B S&P 500 VIX Short-Term Futures ETN",
        None,
        "US06746P1075",
        "thematic",
        None,
    ),
    ("UVXY", "ProShares Ultra VIX Short-Term Futures ETF", None, "US74347X8650", "thematic", None),
)
_ETF_SYMBOLS: tuple[str, ...] = tuple(row[0] for row in _ETF_UNIVERSE)


def _int_volume(series: pd.Series) -> pd.Series:
    """Missing/NaN volume becomes 0; values are cast to ``int64``."""
    return pd.to_numeric(series, errors="coerce").fillna(0).astype("int64")


@dataclass(frozen=True)
class OecdJapanCpiNormalizer:
    """Parsed OECD SDMX rows → ``fred_series`` under the ``OECD_JPN_CPI`` sentinel.

    ``raw`` is the ``(series_id, date, value)`` frame from the SDMX-CSV
    parser. Behaviour: key on the sentinel
    ``series_id`` ``OECD_JPN_CPI``, float-coerce ``value`` and skip any row that is
    not a finite number (the SDMX parser already drops blank/non-parseable
    ``OBS_VALUE``; this is the same-safe coercion at the persistence boundary).
    ``symbol`` is ``None`` — this is a series-keyed family, not symbol-keyed."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        del symbol  # OECD CPI is series-keyed (sentinel series_id), never symbol-keyed.
        if raw.empty:
            return pd.DataFrame(columns=[c.name for c in FRED_SERIES.columns])
        out = pd.DataFrame(index=raw.index)
        out["series_id"] = _OECD_JPN_CPI_SERIES_ID  # sentinel — one series id
        out["date"] = coerce_date(raw["date"])
        out["value"] = pd.to_numeric(raw["value"], errors="coerce")
        # Skip bad SDMX rows (non-numeric values coerce to NaN) before persistence.
        out = out.dropna(subset=["value"])
        # Idempotent against a doubled observation on the natural key (R6.2).
        out = out.drop_duplicates(subset=["series_id", "date"], keep="last")
        out = out.reset_index(drop=True)
        stamped = stamp_updated_at(out, FRED_SERIES, as_of)
        return project_to_table(stamped, FRED_SERIES)


@dataclass(frozen=True)
class EtfMetaNormalizer:
    """Static ``ETF_UNIVERSE`` rows → ``etf_meta``.

    ``raw`` carries the universe columns ``(symbol, name, wkn, isin, category,
    notes)``; the symbols are NATIVE ETF tickers (no Yahoo→FMP mapping). The
    frame is built directly from ``ETF_UNIVERSE`` and upserted on ``symbol``."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        del symbol  # native ETF tickers carried in ``raw["symbol"]`` — no mapping.
        if raw.empty:
            return pd.DataFrame(columns=[c.name for c in ETF_META.columns])
        out = pd.DataFrame(index=raw.index)
        for column in _ETF_META_COLUMNS:
            out[column] = raw[column]
        # Idempotent against a doubled ticker on the natural key (R6.2).
        out = out.drop_duplicates(subset=["symbol"], keep="last").reset_index(drop=True)
        stamped = stamp_updated_at(out, ETF_META, as_of)
        return project_to_table(stamped, ETF_META)


@dataclass(frozen=True)
class EtfDailyNormalizer:
    """ETF daily OHLCV → ``etf_prices`` keyed by the NATIVE ETF ticker.

    Unlike :class:`DailyPricesNormalizer`, the symbol is NOT re-keyed to a canonical
    YF symbol — the ETF ticker IS the native FMP/exchange ticker.
    The native ticker is taken from ``symbol.canonical_id`` when the scope symbol is
    supplied, else from the ``symbol`` column present in ``raw``. Write-time cleaning:
    ``int(volume or 0)``, ``pd.to_datetime().dt.date``, drop null
    close, dedup on ``(symbol, date)``."""

    def normalize(
        self, raw: pd.DataFrame, *, symbol: CanonicalSymbol | None, as_of: datetime
    ) -> pd.DataFrame:
        del as_of  # etf_prices has no _updated_at column
        if raw.empty:
            return pd.DataFrame(columns=[c.name for c in ETF_PRICES.columns])
        out = raw.copy()
        if symbol is not None:
            out["symbol"] = symbol.canonical_id  # native ETF ticker (NOT re-keyed to YF)
        out["date"] = coerce_date(out["date"])
        out["volume"] = _int_volume(out["volume"])
        cleaned = drop_null_and_dedup(out, null_subset=["close"], key=["symbol", "date"])
        return project_to_table(cleaned, ETF_PRICES)


def oecd_etf_families() -> list[FamilyPort]:
    """The OECD Japan CPI + ETF meta/daily ports (group ``oecd_etf``; R2.1).

    * OECD CPI: a single-series full-history upsert into ``fred_series`` under the
      sentinel ``OECD_JPN_CPI`` (``scopes`` carries the one series id).
    * ETF meta: a static-universe full-history upsert into ``etf_meta`` (no date
      axis → availability stamps on the ``symbol`` PK column).
    * ETF daily: a MIN/MAX two-range gap-fill into ``etf_prices``, keyed by the
      NATIVE ETF ticker (no ``symbol_mapper`` — the ticker is already native)."""
    return [
        FamilyPort(
            writer="refresh_oecd_japan_cpi",
            target_table=FRED_SERIES,
            natural_key=("series_id", "date"),
            provider=Provider.OECD,
            incremental=FullWindow(),
            merge_strategy=UpsertPanel(),
            normalizer=OecdJapanCpiNormalizer(),
            availability_column="date",
            quality_policy_default="keep_all",
            scopes=(_OECD_JPN_CPI_SERIES_ID,),
        ),
        FamilyPort(
            writer="upsert_etf_meta",
            target_table=ETF_META,
            natural_key=("symbol",),
            provider=Provider.ETF,
            incremental=FullWindow(),
            merge_strategy=UpsertPanel(),
            normalizer=EtfMetaNormalizer(),
            availability_column="symbol",  # no date axis — PK column carries availability
            quality_policy_default="keep_all",
            scopes=_ETF_SYMBOLS,
        ),
        FamilyPort(
            writer="refresh_etf_daily",
            target_table=ETF_PRICES,
            natural_key=("symbol", "date"),
            provider=Provider.ETF,
            incremental=MinMaxRangeGapFill(date_column="date", scope_column="symbol"),
            merge_strategy=UpsertPanel(),
            normalizer=EtfDailyNormalizer(),
            availability_column="date",
            quality_policy_default="keep_all",
            scopes=_ETF_SYMBOLS,
        ),
    ]
