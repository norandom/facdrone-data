# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Canonical account-currency conversion math and path selection (task 4.1).

One quantitative fact — "what is one unit of this source currency worth in the account
currency at the as-of moment" — is easy to implement twice with opposite conventions:
invert the ``EUR???=X`` close and MULTIPLY, or keep the raw ``close`` and DIVIDE.

A common shortcut falls back to a numeric ``1.0`` when the currency is unmapped, the
quote row is missing, or the close is zero. A missing quote then values a
foreign-currency amount as if one unit were one EUR, silently and without evidence.
R6.2 forbids that.

The approved convention, computed once, here:

    per_unit = account currency for ONE unit of the source currency; conversions MULTIPLY.

    amount = amount_in_source_currency * resolution.per_unit

Resolution order is deterministic and total (R6.4). For source ``C`` and the
injected identity currency ``I``:

    1. direct   — a quote ``C/I``          -> ``rate``
    2. inverse  — a quote ``I/C``          -> ``1 / rate``
    3. two-leg  — ``C -> pivot`` then ``pivot -> I``, each of which is itself resolved
                  direct-then-inverse       -> the product of the two leg factors

USD is the documented pivot, except a USD account uses EUR to consume the catalog's
EUR crosses without a degenerate USD self-hop. Longer paths are NOT searched: a third leg
multiplies three independent quote errors and makes the executed rate depend on which
crosses a vendor happened to publish, which is the opposite of a stable convention.

When several quotes describe the same ordered pair, the LATEST OBSERVATION wins, and
only within that one observation does the latest knowable revision win. A correction
that arrives late but describes an older market moment therefore never displaces the
current rate — the same precedence the point-in-time reader applies in SQL (partition
by ``(symbol, event_time)``, ``known_at desc`` inside the partition).

The account currency resolves to an explicit identity (``1.0``, no legs). That is the
only legitimate ``1.0`` here; every other missing conversion is a typed ``FxUnavailable``.

PURITY (tech.md): this module is arithmetic over injected quotes. It does no IO, reads
no clock, and performs NO point-in-time filtering — callers hand over quotes that are
already clamped to the as-of moment (the ``fx_observation`` reader is a
pipelines-tier adapter). Knowledge times are carried through as opaque provenance so the
adapter can enforce R6.5 (``resolution.known_at <= as_of``) and calibration evidence can
expose R12.5 with one value per resolution rather than a bare float.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

from facdrone_data_types.fx import FxUnavailable, FxUnavailableReason

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

PIVOT_CCY = "USD"
"""The documented intermediate currency; USD identities use the EUR catalog anchor."""
_PIVOT_BY_IDENTITY = {PIVOT_CCY: "EUR"}

_CCY_CODE_LENGTH = 3


@dataclass(frozen=True)
class FxQuote:
    """One already point-in-time-filtered FX observation.

    ``rate`` is units of ``quote_ccy`` per ONE unit of ``base_ccy`` (a ``EUR/USD`` quote
    of ``1.087`` means one EUR buys 1.087 USD). ``observed_at`` is the market moment,
    ``known_at`` the moment the value became knowable, and ``source_ref`` identifies the
    revision it came from — all three are carried, never interpreted, by this module.
    """

    base_ccy: str
    quote_ccy: str
    rate: float
    observed_at: datetime
    known_at: datetime
    source_ref: str


@dataclass(frozen=True)
class FxLeg:
    """One hop of a resolved path, with the provenance of the quote that produced it.

    ``per_unit`` is the multiplicative factor turning ONE unit of ``base_ccy`` into
    ``quote_ccy`` units. The last leg ends in the injected identity currency;
    the product over all legs equals the resolution's
    ``per_unit``.
    """

    base_ccy: str
    quote_ccy: str
    per_unit: float
    observed_at: datetime
    known_at: datetime
    source_ref: str


@dataclass(frozen=True)
class FxResolution:
    """A usable conversion: the rate, the ordered path, and its PIT provenance."""

    source_ccy: str
    per_unit: float
    legs: tuple[FxLeg, ...]

    @property
    def path(self) -> tuple[str, ...]:
        """The currencies traversed, source first: ``("JPY", "USD", "EUR")``."""
        if not self.legs:
            return (self.source_ccy,)
        return (self.legs[0].base_ccy, *(leg.quote_ccy for leg in self.legs))

    @property
    def observed_at(self) -> datetime | None:
        """Latest market moment behind the rate; ``None`` for the account-currency identity."""
        return max((leg.observed_at for leg in self.legs), default=None)

    @property
    def known_at(self) -> datetime | None:
        """The moment the whole path became knowable — the latest leg's knowledge time.

        This is the single value a PIT adapter compares against the as-of
        moment (R6.5) and calibration evidence records as the FX input date (R12.5).
        ``None`` for the account-currency identity, which rests on no observation.
        """
        return max((leg.known_at for leg in self.legs), default=None)

    @property
    def source_refs(self) -> tuple[str, ...]:
        """Revision references of the quotes used, in path order."""
        return tuple(leg.source_ref for leg in self.legs)


@dataclass(frozen=True)
class FxRates:
    """Every currency's typed conversion outcome for ONE as-of moment (task 4.2).

    This is the carrier a composition root hands the read path in place of a bare
    ``Mapping[str, float]``. The float map had two silent failure shapes — a currency
    could be absent, and every consumer independently chose what absence meant (three of
    them chose ``1.0``). Here absence IS the answer: :meth:`resolution` returns a typed
    ``FxUnavailable`` for any currency that was not resolved, and the only way to read a
    number out is to hold an ``FxResolution``.

    Keys are normalized source currency codes. The injected identity needs no quote.
    Roots must pass it explicitly from their configured account currency.
    """

    identity: str = field(kw_only=True)
    resolutions: Mapping[str, FxResolution | FxUnavailable] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _is_currency_code(self.identity) or self.identity != _normalized(self.identity):
            raise ValueError(f"invalid FX identity currency: {self.identity!r}")

    def resolution(self, source_ccy: str) -> FxResolution | FxUnavailable:
        """The typed outcome for one currency; absent means unavailable, never 1.0."""
        code = _normalized(source_ccy)
        if not _is_currency_code(code):
            return FxUnavailable(
                source_ccy=code, reason=FxUnavailableReason.SOURCE_CURRENCY_INVALID
            )
        if code == self.identity:
            return FxResolution(source_ccy=self.identity, per_unit=1.0, legs=())
        found = self.resolutions.get(code)
        if found is None:
            return FxUnavailable(source_ccy=code, reason=FxUnavailableReason.NO_CONVERSION_PATH)
        return found

    def known_rates(self) -> dict[str, float]:
        """``currency -> per_unit`` for every RESOLVED conversion, plus the account
        identity — the accounting projection (sim-broker marking, universe admission).
        An unavailable currency is not a key, so a ``.get(ccy, 1.0)`` downstream would
        have to be written on purpose; nothing here supplies the fabricated parity."""
        rates: dict[str, float] = {self.identity: 1.0}
        for code, outcome in self.resolutions.items():
            if isinstance(outcome, FxResolution):
                rates[code] = outcome.per_unit
        return rates

    def resolve(self, currencies: Iterable[str], quotes: Sequence[FxQuote]) -> FxRates:
        """Resolve each currency once against already-PIT-filtered ``quotes``."""
        outcomes: dict[str, FxResolution | FxUnavailable] = {}
        for currency in currencies:
            code = _normalized(currency)
            if code == self.identity or code in outcomes:
                continue
            outcomes[code] = resolve_per_unit((code, self.identity), quotes)
        return FxRates(resolutions=outcomes, identity=self.identity)


def resolve_per_unit(
    pair: tuple[str, str],
    quotes: Sequence[FxQuote],
) -> FxResolution | FxUnavailable:
    """Resolve the ``(source, identity)`` pair against already-PIT-filtered ``quotes``.

    Returns an ``FxResolution`` carrying the rate, the ordered legs and their
    provenance, or an ``FxUnavailable`` naming why no path exists. Never returns a
    fabricated rate.
    """
    code, identity = map(_normalized, pair)
    if not _is_currency_code(code):
        return FxUnavailable(source_ccy=code, reason=FxUnavailableReason.SOURCE_CURRENCY_INVALID)
    if code == identity:
        return FxResolution(source_ccy=identity, per_unit=1.0, legs=())

    usable = tuple(quote for quote in quotes if _is_usable(quote))
    resolution = _resolve_path((code, identity), usable)
    if resolution is not None:
        return resolution
    return FxUnavailable(source_ccy=code, reason=_failure_reason(code, quotes))


def convert(amount: float, resolution: FxResolution) -> float:
    """Convert ``amount``, denominated in ``resolution.source_ccy``, into the resolved identity.

    The one place the MULTIPLY convention is executed; it accepts only a resolved
    conversion, so an unavailable one cannot reach a sizing, margin or P&L number.
    """
    return amount * resolution.per_unit


def _normalized(code: str) -> str:
    return code.strip().upper()


def _is_currency_code(code: str) -> bool:
    return len(code) == _CCY_CODE_LENGTH and code.isalpha()


def _has_valid_rate(quote: FxQuote) -> bool:
    return math.isfinite(quote.rate) and quote.rate > 0.0


def _has_valid_pair(quote: FxQuote) -> bool:
    base, quoted = _normalized(quote.base_ccy), _normalized(quote.quote_ccy)
    return _is_currency_code(base) and _is_currency_code(quoted) and base != quoted


def _has_comparable_provenance(quote: FxQuote) -> bool:
    """Naive timestamps cannot be ordered against an as-of moment, so they are
    not admissible evidence and must not become a rate by default."""
    return quote.observed_at.tzinfo is not None and quote.known_at.tzinfo is not None


def _is_usable(quote: FxQuote) -> bool:
    return _has_valid_pair(quote) and _has_valid_rate(quote) and _has_comparable_provenance(quote)


def _select(quotes: Sequence[FxQuote], base_ccy: str, quote_ccy: str) -> FxQuote | None:
    """The one quote for an ordered pair: LATEST OBSERVATION first, then — among the
    quotes describing that same observation — the latest knowable revision, with the
    source reference as the final tiebreak so the choice never depends on input order.

    Observation before knowledge is not a stylistic ordering, it is the point-in-time
    rule. Callers hand over quotes already clamped to the as-of moment, so
    every candidate is knowable; what remains to choose is WHICH MARKET MOMENT to
    price at, and that is always the most recent one. Ranking by knowledge time first
    would let a late-arriving correction to an OLDER market moment outrank the current
    observation and hand back a stale rate.

    This is the same rule the point-in-time reader applies in SQL: partition by
    ``(symbol, event_time)``, order by ``known_at desc`` inside the partition, then
    take the latest event (``pipelines.data.revisioned_market``).
    """
    candidates = [
        quote
        for quote in quotes
        if _normalized(quote.base_ccy) == base_ccy and _normalized(quote.quote_ccy) == quote_ccy
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda quote: (quote.observed_at, quote.known_at, quote.source_ref))


def _as_leg(quote: FxQuote, from_ccy: str, to_ccy: str, factor: float) -> FxLeg:
    return FxLeg(
        base_ccy=from_ccy,
        quote_ccy=to_ccy,
        per_unit=factor,
        observed_at=quote.observed_at,
        known_at=quote.known_at,
        source_ref=quote.source_ref,
    )


def _leg(quotes: Sequence[FxQuote], from_ccy: str, to_ccy: str) -> FxLeg | None:
    """One hop, resolved direct first and inverse second (the documented order)."""
    direct = _select(quotes, from_ccy, to_ccy)
    if direct is not None:
        return _as_leg(direct, from_ccy, to_ccy, direct.rate)
    inverse = _select(quotes, to_ccy, from_ccy)
    if inverse is not None:
        return _as_leg(inverse, from_ccy, to_ccy, 1.0 / inverse.rate)
    return None


def _resolve_path(pair: tuple[str, str], quotes: Sequence[FxQuote]) -> FxResolution | None:
    code, identity = pair
    single = _leg(quotes, code, identity)
    if single is not None:
        # The inverse of a usable-but-subnormal quote can overflow to infinity; a
        # resolution must carry a usable NUMBER, not merely derive from a usable quote.
        if not (math.isfinite(single.per_unit) and single.per_unit > 0.0):
            return None
        return FxResolution(source_ccy=code, per_unit=single.per_unit, legs=(single,))
    pivot = _PIVOT_BY_IDENTITY.get(identity, PIVOT_CCY)
    if code == pivot:
        return None  # a pivot equal to the source would be a degenerate self-hop
    to_pivot = _leg(quotes, code, pivot)
    pivot_to_identity = _leg(quotes, pivot, identity)
    if to_pivot is None or pivot_to_identity is None:
        return None  # three-leg paths are deliberately not searched
    rate = to_pivot.per_unit * pivot_to_identity.per_unit
    if not (math.isfinite(rate) and rate > 0.0):
        return None
    return FxResolution(source_ccy=code, per_unit=rate, legs=(to_pivot, pivot_to_identity))


def _failure_reason(code: str, quotes: Sequence[FxQuote]) -> FxUnavailableReason:
    """Distinguish "the evidence for this currency was broken" from "there was none",
    so an operator sees a bad revision rather than a generic gap."""
    for quote in quotes:
        if _is_usable(quote):
            continue
        if code in (_normalized(quote.base_ccy), _normalized(quote.quote_ccy)):
            return FxUnavailableReason.QUOTE_UNUSABLE
    return FxUnavailableReason.NO_CONVERSION_PATH
