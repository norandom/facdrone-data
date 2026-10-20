# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Point-in-time FX resolution invariants (task 4.1, R6.1-6.5, R12.2/12.5).

A naive resolver converts a source currency to EUR and falls back to a numeric ``1.0``
when the quote is missing, unmapped, or zero. A missing quote then values a
foreign-currency amount as if 1 unit were 1 EUR — silently, with no evidence.
R6.2 forbids exactly that.

These tests pin the replacement:

1. ``per_unit`` is EUR for ONE unit of the source currency and conversions
   MULTIPLY by it — the same number as ``1.0 / close`` of the ``EUR???=X`` quote,
   now with one convention instead of two.
2. Resolution order is deterministic: direct, then inverse, then one two-leg path
   via USD. Longer paths are unavailable, not searched.
3. Every leg carries observation time, knowledge time and a source reference, so a
   PIT adapter can enforce R6.5 and calibration evidence can expose R12.5.
4. Among several quotes for one pair, the LATEST OBSERVATION wins and only then the
   latest knowable revision of it — so a late correction to an older market moment
   cannot displace the current rate (the ``revisioned_market`` reader's rule).
5. A missing or unusable conversion is a TYPED unavailable outcome. Never 1.0.

Quotes carry DISTINCT observation times throughout: a fixture where every quote
shares one hardcoded ``observed_at`` makes the observation-vs-knowledge precedence
unobservable, which is how the inverted ordering survived its first test suite.

``test_a_naive_fallback_would_fabricate_a_rate_where_the_resolver_refuses`` reproduces
the naive fallback rule inline so the assertion is anchored to the real defect rather
than to the new code.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest

from facdrone_data.fx import FxLeg, FxQuote, FxResolution, convert, resolve_per_unit
from facdrone_data_types.fx import FxUnavailable, FxUnavailableReason


def test_identity_currency_is_explicit_and_usd_needs_no_quote() -> None:
    from facdrone_data.fx import FxRates

    rates = FxRates(identity="USD").resolve(("USD", "EUR"), [_quote("EUR/USD", 1.25)])
    usd, eur = rates.resolution("USD"), rates.resolution("EUR")
    assert isinstance(usd, FxResolution) and usd.per_unit == 1.0 and usd.legs == ()
    assert isinstance(eur, FxResolution) and eur.per_unit == 1.25
    assert rates.identity == "USD"
    assert rates.known_rates() == {"USD": 1.0, "EUR": 1.25}


def test_an_fx_carrier_cannot_silently_default_its_identity() -> None:
    from facdrone_data.fx import FxRates

    with pytest.raises(TypeError):
        FxRates()  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="identity"):
        FxRates(identity="usd")


def test_usd_account_resolves_foreign_profit_through_catalog_eur_quotes() -> None:
    quotes = [_quote("EUR/JPY", 160.0), _quote("EUR/USD", 1.25)]
    outcome = resolve_per_unit(("JPY", "USD"), quotes)
    assert isinstance(outcome, FxResolution)
    assert outcome.per_unit == (1.0 / 160.0) * 1.25
    assert outcome.path == ("JPY", "EUR", "USD")
    assert len(outcome.legs) == 2


def _at(day: int, hour: int) -> datetime:
    return datetime(2024, 3, day, hour, 0, tzinfo=UTC)


_OBSERVED = _at(1, 21)
_KNOWN = _at(1, 22)


def _quote(
    pair: str,
    rate: float,
    *,
    observed_at: datetime = _OBSERVED,
    known_at: datetime = _KNOWN,
    source_ref: str = "fx_observation:rev-1",
) -> FxQuote:
    """One quote for ``pair`` in ``BASE/QUOTE`` form, e.g. ``"EUR/USD"``.

    Both PIT moments stay first-class keyword arguments so that any fixture with
    more than one quote has to state WHICH market moment each one describes.
    """
    base, quote = pair.split("/")
    return FxQuote(
        base_ccy=base,
        quote_ccy=quote,
        rate=rate,
        observed_at=observed_at,
        known_at=known_at,
        source_ref=source_ref,
    )


def _resolved(source_ccy: str, quotes: list[FxQuote]) -> FxResolution:
    outcome = resolve_per_unit((source_ccy, "EUR"), quotes)
    assert isinstance(outcome, FxResolution), outcome
    return outcome


def _unavailable(source_ccy: str, quotes: list[FxQuote]) -> FxUnavailable:
    outcome = resolve_per_unit((source_ccy, "EUR"), quotes)
    assert isinstance(outcome, FxUnavailable), outcome
    return outcome


# ── 1. EUR identity is the one legitimate 1.0 ────────────────────────────────


def test_eur_resolves_to_an_explicit_identity_with_no_evidence() -> None:
    """EUR needs no quote: rate 1.0, no legs, and a one-hop path (R6.1)."""
    outcome = _resolved("EUR", [])

    assert outcome.per_unit == 1.0
    assert outcome.legs == ()
    assert outcome.path == ("EUR",)
    assert outcome.known_at is None  # identity rests on no observation
    assert outcome.observed_at is None
    assert outcome.source_refs == ()


def test_currency_codes_are_normalized_before_resolution() -> None:
    assert _resolved(" eur ", []).source_ccy == "EUR"
    assert _resolved("usd", [_quote("usd/eur", 0.92)]).per_unit == pytest.approx(0.92)


def test_a_malformed_source_currency_is_typed_unavailable() -> None:
    for bad in ("", "   ", "EU", "EURO", "US1"):
        outcome = _unavailable(bad, [_quote("USD/EUR", 0.92)])
        assert outcome.reason == FxUnavailableReason.SOURCE_CURRENCY_INVALID


# ── 2. Deterministic resolution order: direct → inverse → two-leg via USD ────


def test_a_direct_quote_resolves_by_multiplication() -> None:
    """base=USD quote=EUR rate 0.92 means one USD is 0.92 EUR (R6.1, R6.4)."""
    outcome = _resolved("USD", [_quote("USD/EUR", 0.92)])

    assert outcome.per_unit == pytest.approx(0.92)
    assert outcome.path == ("USD", "EUR")
    assert len(outcome.legs) == 1
    assert outcome.legs[0] == FxLeg(
        base_ccy="USD",
        quote_ccy="EUR",
        per_unit=0.92,
        observed_at=_OBSERVED,
        known_at=_KNOWN,
        source_ref="fx_observation:rev-1",
    )
    assert convert(1_000.0, outcome) == pytest.approx(920.0)


def test_an_inverse_quote_is_reciprocated() -> None:
    """The store holds ``EURUSD=X`` (USD per EUR); one USD is ``1 / 1.0870`` EUR."""
    outcome = _resolved("USD", [_quote("EUR/USD", 1.0870)])

    assert outcome.per_unit == pytest.approx(1.0 / 1.0870)
    assert outcome.path == ("USD", "EUR")
    assert outcome.legs[0].base_ccy == "USD"
    assert outcome.legs[0].quote_ccy == "EUR"


def test_the_naive_get_fx_rate_convention_is_preserved() -> None:
    """R6.4: one documented convention. Taking ``1.0 / close``
    of ``EURUSD=X`` and MULTIPLYING, or keeping ``close`` and DIVIDING:
    both give this same number, which is now computed once."""
    eurusd_close = 1.0870  # USD per one EUR, as stored by the EUR???=X family
    naive_get_fx_rate = 1.0 / eurusd_close

    outcome = _resolved("USD", [_quote("EUR/USD", eurusd_close)])

    assert outcome.per_unit == pytest.approx(naive_get_fx_rate)
    atr_usd, contract_size = 1.35, 100.0
    assert convert(atr_usd * contract_size, outcome) == pytest.approx(
        atr_usd * contract_size / eurusd_close  # the keep-close-and-DIVIDE convention
    )


def test_direct_wins_over_inverse_when_both_exist() -> None:
    """Both paths are valid, so the documented order decides — not input order, and
    not recency: the inverse quote here is both newer and more recently known."""
    quotes = [
        _quote("EUR/USD", 2.0, observed_at=_at(2, 21), known_at=_at(2, 22)),
        _quote("USD/EUR", 0.92, observed_at=_at(1, 21), known_at=_at(1, 22)),
    ]

    assert _resolved("USD", quotes).per_unit == pytest.approx(0.92)
    assert _resolved("USD", list(reversed(quotes))).per_unit == pytest.approx(0.92)


def test_two_leg_path_via_usd_is_ordered_and_multiplicative() -> None:
    """JPY has no EUR quote: 1 JPY -> 1/150 USD -> /1.10 EUR (R6.4)."""
    quotes = [
        _quote("USD/JPY", 150.0, observed_at=_at(1, 20), known_at=_at(1, 21)),
        _quote("EUR/USD", 1.10, observed_at=_at(1, 21), known_at=_at(1, 22)),
    ]

    outcome = _resolved("JPY", quotes)

    assert outcome.path == ("JPY", "USD", "EUR")
    assert [(leg.base_ccy, leg.quote_ccy) for leg in outcome.legs] == [
        ("JPY", "USD"),
        ("USD", "EUR"),
    ]
    assert outcome.per_unit == pytest.approx((1.0 / 150.0) * (1.0 / 1.10))
    assert math.prod(leg.per_unit for leg in outcome.legs) == pytest.approx(outcome.per_unit)


def test_a_direct_quote_is_preferred_over_the_usd_pivot() -> None:
    """The pivot legs are the newer observations; the direct quote still wins."""
    quotes = [
        _quote("JPY/EUR", 0.0061, observed_at=_at(1, 21), known_at=_at(1, 22)),
        _quote("USD/JPY", 150.0, observed_at=_at(2, 21), known_at=_at(2, 22)),
        _quote("EUR/USD", 1.10, observed_at=_at(2, 21), known_at=_at(2, 22)),
    ]

    outcome = _resolved("JPY", quotes)

    assert outcome.path == ("JPY", "EUR")
    assert outcome.per_unit == pytest.approx(0.0061)


def test_three_leg_paths_are_unavailable() -> None:
    """GBP -> JPY -> USD -> EUR exists arithmetically; the resolver refuses it."""
    quotes = [
        _quote("GBP/JPY", 190.0, observed_at=_at(1, 19)),
        _quote("USD/JPY", 150.0, observed_at=_at(1, 20)),
        _quote("EUR/USD", 1.10, observed_at=_at(1, 21)),
    ]

    outcome = _unavailable("GBP", quotes)

    assert outcome.source_ccy == "GBP"
    assert outcome.reason == FxUnavailableReason.NO_CONVERSION_PATH


def test_usd_without_a_eur_quote_does_not_pivot_through_itself() -> None:
    outcome = _unavailable("USD", [_quote("USD/JPY", 150.0)])

    assert outcome.reason == FxUnavailableReason.NO_CONVERSION_PATH


def test_resolution_is_independent_of_quote_order() -> None:
    quotes = [
        _quote("USD/JPY", 150.0, observed_at=_at(1, 19), known_at=_at(1, 23)),
        _quote("EUR/USD", 1.10, observed_at=_at(1, 20), known_at=_at(1, 22)),
        _quote("EUR/GBP", 0.85, observed_at=_at(1, 21), known_at=_at(1, 21)),
    ]

    assert _resolved("JPY", quotes) == _resolved("JPY", list(reversed(quotes)))
    assert _resolved("GBP", quotes) == _resolved("GBP", list(reversed(quotes)))


# ── 3. Provenance travels with the rate (R12.2, R12.5) ───────────────────────


def test_every_leg_carries_observation_knowledge_and_source_provenance() -> None:
    quotes = [
        _quote(
            "USD/JPY",
            150.0,
            observed_at=_at(1, 19),
            known_at=_at(1, 20),
            source_ref="fx_observation:usdjpy-r2",
        ),
        _quote(
            "EUR/USD",
            1.10,
            observed_at=_at(1, 21),
            known_at=_at(1, 23),
            source_ref="fx_observation:eurusd-r7",
        ),
    ]

    outcome = _resolved("JPY", quotes)

    assert [leg.observed_at for leg in outcome.legs] == [_at(1, 19), _at(1, 21)]
    assert [leg.known_at for leg in outcome.legs] == [_at(1, 20), _at(1, 23)]
    assert outcome.source_refs == ("fx_observation:usdjpy-r2", "fx_observation:eurusd-r7")
    # The resolution is only knowable once its LAST leg is knowable: this is the
    # single value a PIT adapter compares against the as-of time (R6.5).
    assert outcome.known_at == _at(1, 23)
    # ... and the path prices the latest market moment any of its legs saw.
    assert outcome.observed_at == _at(1, 21)


# ── 3b. Observation before knowledge: the PIT precedence itself ──────────────


def test_the_latest_revision_of_one_observation_wins_deterministically() -> None:
    """Within a SINGLE market moment, a later-known revision supersedes the first
    print — the ``known_at desc`` ordering inside one ``event_time`` partition."""
    at_21h = _at(1, 21)  # one market moment, two prints of it
    first_print = _quote("USD/EUR", 0.80, observed_at=at_21h, known_at=_at(1, 22), source_ref="r1")
    correction = _quote("USD/EUR", 0.92, observed_at=at_21h, known_at=_at(1, 23), source_ref="r2")

    for quotes in ([first_print, correction], [correction, first_print]):
        outcome = _resolved("USD", quotes)
        assert outcome.per_unit == pytest.approx(0.92)
        assert outcome.known_at == _at(1, 23)
        assert outcome.source_refs == ("r2",)


def test_a_late_correction_to_an_older_observation_never_beats_a_newer_one() -> None:
    """The defect this file previously could not see. A vendor restates the 1 Mar fix
    on 3 Mar, after the 2 Mar fix has already printed. The restatement has the LATEST
    knowledge time, so ranking by knowledge first hands back the 1 Mar rate and sizes
    today's exposure at yesterday's price. Observation time decides first."""
    mar02_print = _quote(
        "USD/EUR", 0.92, observed_at=_at(2, 21), known_at=_at(2, 22), source_ref="mar02-r1"
    )
    mar01_restated_on_mar03 = _quote(
        "USD/EUR", 0.80, observed_at=_at(1, 21), known_at=_at(3, 9), source_ref="mar01-r2"
    )

    for quotes in ([mar02_print, mar01_restated_on_mar03], [mar01_restated_on_mar03, mar02_print]):
        outcome = _resolved("USD", quotes)

        assert outcome.per_unit == pytest.approx(0.92)
        assert outcome.observed_at == _at(2, 21)
        assert outcome.known_at == _at(2, 22)
        assert outcome.source_refs == ("mar02-r1",)


def test_observation_precedence_matches_the_point_in_time_reader() -> None:
    """``pipelines.data.revisioned_market`` partitions by ``(symbol, event_time)``,
    orders ``known_at desc`` inside the partition and takes the latest event. Ranked
    against that reference implementation, the resolver must pick the same revision."""
    quotes = [
        _quote("USD/EUR", 0.80, observed_at=_at(1, 21), known_at=_at(1, 22), source_ref="a"),
        # The globally latest knowledge time sits on the OLDER observation on purpose.
        _quote("USD/EUR", 0.81, observed_at=_at(1, 21), known_at=_at(4, 9), source_ref="b"),
        _quote("USD/EUR", 0.90, observed_at=_at(2, 21), known_at=_at(2, 22), source_ref="c"),
        _quote("USD/EUR", 0.92, observed_at=_at(2, 21), known_at=_at(3, 10), source_ref="d"),
    ]

    # The reader's rule, reproduced here over the same rows.
    latest_event = max(quote.observed_at for quote in quotes)
    expected = max(
        (quote for quote in quotes if quote.observed_at == latest_event),
        key=lambda quote: (quote.known_at, quote.source_ref),
    )

    outcome = _resolved("USD", quotes)

    assert expected.source_ref == "d"
    assert outcome.per_unit == pytest.approx(expected.rate)
    assert outcome.source_refs == (expected.source_ref,)


def test_a_stale_correction_on_a_pivot_leg_does_not_infect_the_two_leg_path() -> None:
    """The precedence has to hold per leg, not only for direct quotes."""
    quotes = [
        _quote("USD/JPY", 150.0, observed_at=_at(2, 21), known_at=_at(2, 22)),
        _quote("USD/JPY", 120.0, observed_at=_at(1, 21), known_at=_at(3, 9)),
        _quote("EUR/USD", 1.10, observed_at=_at(2, 21), known_at=_at(2, 22)),
        _quote("EUR/USD", 1.30, observed_at=_at(1, 21), known_at=_at(3, 9)),
    ]

    outcome = _resolved("JPY", quotes)

    assert outcome.per_unit == pytest.approx((1.0 / 150.0) * (1.0 / 1.10))
    assert outcome.observed_at == _at(2, 21)
    assert outcome.known_at == _at(2, 22)


def test_quotes_without_timezone_aware_provenance_are_unusable() -> None:
    """A naive timestamp cannot be compared against an as-of moment, so it
    is not admissible PIT evidence and must not silently become a rate."""
    naive = FxQuote(
        base_ccy="USD",
        quote_ccy="EUR",
        rate=0.92,
        observed_at=datetime(2024, 3, 1, 21, 0),  # deliberately naive
        known_at=datetime(2024, 3, 1, 22, 0),  # deliberately naive
        source_ref="fx_observation:naive",
    )

    outcome = _unavailable("USD", [naive])

    assert outcome.reason == FxUnavailableReason.QUOTE_UNUSABLE


# ── 4. Never a numeric fallback (R6.2) ───────────────────────────────────────


def test_a_missing_conversion_is_typed_unavailable_and_never_one() -> None:
    outcome = resolve_per_unit(("GBP", "EUR"), [])

    assert isinstance(outcome, FxUnavailable)
    assert outcome.source_ccy == "GBP"
    assert outcome.reason == FxUnavailableReason.NO_CONVERSION_PATH
    assert not hasattr(outcome, "per_unit")


@pytest.mark.parametrize("bad_rate", [0.0, -1.1, float("nan"), float("inf")])
def test_an_unusable_quote_rate_never_becomes_a_number(bad_rate: float) -> None:
    outcome = _unavailable("USD", [_quote("EUR/USD", bad_rate)])

    assert outcome.reason == FxUnavailableReason.QUOTE_UNUSABLE


def test_an_unusable_quote_for_another_currency_does_not_mask_the_real_reason() -> None:
    outcome = _unavailable("GBP", [_quote("EUR/USD", 0.0)])

    assert outcome.reason == FxUnavailableReason.NO_CONVERSION_PATH


def test_a_usable_quote_still_resolves_alongside_an_unusable_one() -> None:
    """The broken quote is the newest observation, so it is dropped for being
    unusable rather than merely for being old."""
    quotes = [
        _quote("EUR/USD", 0.0, observed_at=_at(3, 21), known_at=_at(3, 22), source_ref="broken"),
        _quote("USD/EUR", 0.92, observed_at=_at(1, 21), known_at=_at(1, 22)),
    ]

    assert _resolved("USD", quotes).per_unit == pytest.approx(0.92)


def test_an_unusable_newer_revision_does_not_resurrect_a_stale_observation() -> None:
    """A corrupt latest revision of the newest observation must not silently fall
    back to an older market moment: the usable revision of that same observation is
    the answer, even when an older observation carries a more recent restatement."""
    quotes = [
        _quote("USD/EUR", 0.92, observed_at=_at(2, 21), known_at=_at(2, 22), source_ref="good"),
        _quote("USD/EUR", 0.0, observed_at=_at(2, 21), known_at=_at(3, 9), source_ref="corrupt"),
        _quote("USD/EUR", 0.70, observed_at=_at(1, 21), known_at=_at(4, 9), source_ref="stale"),
    ]

    outcome = _resolved("USD", quotes)

    assert outcome.per_unit == pytest.approx(0.92)
    assert outcome.source_refs == ("good",)


def test_a_naive_fallback_would_fabricate_a_rate_where_the_resolver_refuses() -> None:
    """The naive fallback, reproduced inline: map a source currency through
    ``FX_TO_EUR`` and return ``1.0`` whenever the symbol is unmapped or the close is
    missing/zero. A conversion then treats 1 TRY as 1 EUR. The resolver must diverge here."""
    fx_to_eur = {"USD": "EURUSD=X"}  # TRY is not in the naive map
    closes: dict[str, float] = {"EURUSD=X": 1.0870}

    def naive_get_fx_rate(currency_profit: str) -> float:
        if currency_profit == "EUR":
            return 1.0
        fx_sym = fx_to_eur.get(currency_profit)
        if not fx_sym:
            return 1.0  # <- the defect R6.2 forbids
        close = closes.get(fx_sym, 0.0)
        return (1.0 / close) if close > 0 else 1.0  # <- and again

    quotes = [_quote("EUR/USD", 1.0870)]

    assert naive_get_fx_rate("TRY") == 1.0
    assert naive_get_fx_rate("JPY") == 1.0
    assert isinstance(resolve_per_unit(("TRY", "EUR"), quotes), FxUnavailable)
    assert isinstance(resolve_per_unit(("JPY", "EUR"), quotes), FxUnavailable)
    # ... while the currency the naive map could resolve keeps its exact number.
    assert _resolved("USD", quotes).per_unit == pytest.approx(naive_get_fx_rate("USD"))


def test_to_eur_multiplies_and_only_accepts_a_resolution() -> None:
    outcome = _resolved("USD", [_quote("USD/EUR", 0.92)])

    assert convert(0.0, outcome) == 0.0
    assert convert(-250.0, outcome) == pytest.approx(-230.0)
    assert convert(1.0, _resolved("EUR", [])) == 1.0
    # `convert` is typed against FxResolution: an unavailable outcome cannot reach a
    # conversion at all, which is the fail-closed ergonomic R6.3 relies on.
    assert not hasattr(_unavailable("GBP", []), "per_unit")


# ── 6. FxRates: the per-as-of typed carrier (task 4.2) ───────────────────────
# One as-of moment resolves every currency ONCE; consumers then read typed
# outcomes from the carrier instead of a bare float map with a `.get(ccy, 1.0)`.


def test_fx_rates_resolves_each_currency_through_the_one_resolver() -> None:
    from facdrone_data.fx import FxRates

    quotes = [_quote("EUR/USD", 1.25), _quote("JPY/USD", 0.0080, source_ref="jpyusd")]
    rates = FxRates(identity="EUR").resolve(("USD", "JPY", "EUR"), quotes)

    usd = rates.resolution("USD")
    assert isinstance(usd, FxResolution)
    assert usd.per_unit == pytest.approx(1.0 / 1.25)
    jpy = rates.resolution("JPY")
    assert isinstance(jpy, FxResolution)
    assert jpy.path == ("JPY", "USD", "EUR")
    assert jpy.per_unit == pytest.approx(0.0080 * (1.0 / 1.25))


def test_fx_rates_eur_is_the_identity_even_when_never_offered() -> None:
    from facdrone_data.fx import FxRates

    rates = FxRates(identity="EUR").resolve((), [])

    eur = rates.resolution("EUR")
    assert isinstance(eur, FxResolution)
    assert eur.per_unit == 1.0
    assert eur.legs == ()


def test_fx_rates_absent_currency_is_typed_unavailable_never_a_number() -> None:
    from facdrone_data.fx import FxRates

    rates = FxRates(identity="EUR").resolve(("USD",), [_quote("EUR/USD", 1.25)])

    chf = rates.resolution("CHF")
    assert isinstance(chf, FxUnavailable)
    assert chf.reason == FxUnavailableReason.NO_CONVERSION_PATH
    assert not hasattr(chf, "per_unit")
    bad = rates.resolution("E1")
    assert isinstance(bad, FxUnavailable)
    assert bad.reason == FxUnavailableReason.SOURCE_CURRENCY_INVALID


def test_fx_rates_unresolvable_offered_currency_keeps_its_typed_reason() -> None:
    from facdrone_data.fx import FxRates

    rates = FxRates(identity="EUR").resolve(("TRY",), [_quote("EUR/TRY", 0.0)])  # unusable quote

    outcome = rates.resolution("TRY")
    assert isinstance(outcome, FxUnavailable)
    assert outcome.reason == FxUnavailableReason.QUOTE_UNUSABLE


def test_fx_rates_known_rates_carries_only_resolved_conversions() -> None:
    """The accounting projection for the sim broker: every value is a RESOLVED
    per_unit (plus the EUR identity); an unavailable currency is simply not
    a key — so no downstream `.get(ccy, 1.0)` can quietly resurrect parity."""
    from facdrone_data.fx import FxRates

    rates = FxRates(identity="EUR").resolve(("USD", "TRY"), [_quote("EUR/USD", 1.25)])

    known = rates.known_rates()
    assert known == {"EUR": 1.0, "USD": pytest.approx(1.0 / 1.25)}
    assert "TRY" not in known


def test_fx_rates_identity_carries_nothing_but_eur() -> None:
    from facdrone_data.fx import FxRates

    rates = FxRates(identity="EUR")

    assert rates.known_rates() == {"EUR": 1.0}
    assert isinstance(rates.resolution("USD"), FxUnavailable)


def test_fx_rates_normalizes_currency_codes_on_both_sides() -> None:
    from facdrone_data.fx import FxRates

    rates = FxRates(identity="EUR").resolve((" usd ",), [_quote("EUR/USD", 1.25)])

    outcome = rates.resolution("usd")
    assert isinstance(outcome, FxResolution)
    assert outcome.per_unit == pytest.approx(1.0 / 1.25)


def test_a_subnormal_inverse_quote_cannot_overflow_into_a_resolution() -> None:
    """1 / 5e-324 is infinity: a usable-looking quote whose INVERSE is not a number
    must refuse, never hand downstream evidence an infinite per_unit."""
    outcome = resolve_per_unit(("USD", "EUR"), [_quote("EUR/USD", 5e-324)])

    assert isinstance(outcome, FxUnavailable)
