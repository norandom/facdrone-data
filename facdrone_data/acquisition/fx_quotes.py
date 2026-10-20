# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Adapters that turn stored FX rows into typed resolver quotes (task 4.2; R6.1, R6.5, R12.2).

``library.fx`` is pure arithmetic over injected :class:`FxQuote` values; it never decides
what a database row means. This module owns that meaning for the latest-value panel:

* :func:`eur_pair_symbol` — the ONE spelling of the ``EUR<ccy>=X`` pair convention. It
  used to be re-derived inline wherever FX was loaded, which is how a second spelling
  would have silently split the universe from its quotes.
* :func:`quote_from_daily_close` — one dated ``daily_prices`` close becomes one
  provenance-stamped quote. The panel stores calendar-dated closes with no intraday
  moment and no revision history, so the honest stamp for BOTH the observation and the
  knowledge time is the UTC midnight of the observation date: comparable against any
  tz-aware as-of boundary, and never claiming more precision than the source has.

The revisioned ``fx_observation`` table (task 1.3) carries real per-revision knowledge
times; binding it to the resolver belongs to the coherent snapshot loader (task 8.2) and
gets its own adapter there.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import TYPE_CHECKING

from facdrone_data.fx import FxQuote

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import date


def eur_pair_symbol(currency: str) -> str:
    """The panel symbol quoting ``currency`` per one EUR, e.g. ``EURJPY=X``."""
    return fx_pair_symbol(currency, target_currency="EUR")


def fx_pair_symbol(currency: str, *, target_currency: str) -> str:
    """The panel's inverse conversion quote: source units per one target unit."""
    return f"{target_currency.strip().upper()}{currency.strip().upper()}=X"


def fx_pair_symbols(currencies: Iterable[str]) -> dict[str, str]:
    """``currency -> pair symbol`` for every distinct non-EUR currency offered."""
    pairs: dict[str, str] = {}
    for currency in currencies:
        code = currency.strip().upper()
        if code and code != "EUR":
            pairs.setdefault(code, eur_pair_symbol(code))
    return pairs


def conversion_pair_symbols(
    currencies: Iterable[str], *, target_currency: str
) -> dict[tuple[str, str], str]:
    """Read direct account quotes alongside the panel's historical EUR bridge quotes."""
    sources = tuple(currencies)
    pairs = {
        ("EUR", currency): symbol
        for currency, symbol in fx_pair_symbols((*sources, target_currency)).items()
    }
    for currency in sources:
        code = currency.strip().upper()
        if code and code != target_currency:
            pairs[target_currency, code] = fx_pair_symbol(code, target_currency=target_currency)
    return pairs


def quote_from_daily_close(
    quote_ccy: str,
    *,
    observed_on: date,
    close: float,
    source_table: str,
) -> FxQuote:
    """One ``EUR<ccy>=X`` daily close as a typed, provenance-stamped resolver quote."""
    return quote_from_pair_close(
        ("EUR", quote_ccy), observed_on=observed_on, close=close, source_table=source_table
    )


def quote_from_pair_close(
    pair: tuple[str, str], *, observed_on: date, close: float, source_table: str
) -> FxQuote:
    """One explicitly denominated daily pair close, retaining its stored-source provenance."""
    base, code = (currency.strip().upper() for currency in pair)
    stamp = datetime.combine(observed_on, time.min, tzinfo=UTC)
    symbol = fx_pair_symbol(code, target_currency=base)
    return FxQuote(
        base_ccy=base,
        quote_ccy=code,
        rate=close,
        observed_at=stamp,
        known_at=stamp,
        source_ref=f"{source_table}:{symbol}:{observed_on.isoformat()}",
    )
