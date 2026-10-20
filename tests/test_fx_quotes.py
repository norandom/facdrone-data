# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The daily-close FX quote adapter (task 4.2, R6.1/R6.5/R12.2).

``pipelines.data.fx_quotes`` is the ONE place a dated ``EUR<ccy>=X`` panel close becomes
a provenance-stamped :class:`FxQuote` the typed resolver accepts. The pair naming
convention and the ``1.0 / close`` inversion live here, so no composition root repeats
them inline.

The adapter stamps observation and knowledge time as the UTC midnight of the observation
date — the finest provenance the latest-value ``daily_prices`` panel can honestly claim —
so the resolver's tz-aware admissibility rule holds and a PIT boundary can still order
the quote against an as-of moment.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from facdrone_data.acquisition.fx_quotes import (
    eur_pair_symbol,
    fx_pair_symbols,
    quote_from_daily_close,
)
from facdrone_data.fx import FxRates, FxResolution


def test_pair_symbol_convention_is_the_one_yfinance_spelling() -> None:
    assert eur_pair_symbol("USD") == "EURUSD=X"
    assert eur_pair_symbol(" jpy ") == "EURJPY=X"


def test_pair_symbols_cover_only_non_eur_currencies() -> None:
    pairs = fx_pair_symbols(("USD", "EUR", "JPY", "usd"))

    assert pairs == {"USD": "EURUSD=X", "JPY": "EURJPY=X"}


def test_daily_close_quote_carries_utc_provenance_and_the_pair_orientation() -> None:
    quote = quote_from_daily_close(
        "USD", observed_on=date(2024, 3, 4), close=1.0870, source_table="daily_prices"
    )

    assert (quote.base_ccy, quote.quote_ccy) == ("EUR", "USD")
    assert quote.rate == 1.0870
    assert quote.observed_at == datetime(2024, 3, 4, tzinfo=UTC)
    assert quote.known_at == datetime(2024, 3, 4, tzinfo=UTC)
    assert quote.source_ref == "daily_prices:EURUSD=X:2024-03-04"


def test_daily_close_quote_resolves_to_the_inverted_rate() -> None:
    """End to end with the resolver: the adapter's EUR/ccy orientation makes the
    resolver invert, which is exactly ``1.0 / close`` — same value, now typed and provenanced."""
    quote = quote_from_daily_close(
        "USD", observed_on=date(2024, 3, 4), close=1.25, source_table="daily_prices"
    )
    rates = FxRates(identity="EUR").resolve(("USD",), [quote])

    outcome = rates.resolution("USD")
    assert isinstance(outcome, FxResolution)
    assert outcome.per_unit == pytest.approx(1.0 / 1.25)
    assert outcome.source_refs == ("daily_prices:EURUSD=X:2024-03-04",)
