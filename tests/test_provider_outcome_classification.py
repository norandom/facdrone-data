# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""A symbol with nothing to fetch is up to date, not a provider failure.

``_provider_outcome`` classified a family scope from three counts. When the incremental
strategy found NO gaps — the panel already covers the requested window — the fetch loop
never runs, all three counts stay zero, and the classification fell through to
``PROVIDER_UNAVAILABLE``.

The cost was not cosmetic. A refresh of the full universe reported 581 of 945 scopes as
``provider_unavailable``, which reads as a provider outage; the true state was that most
symbols were already current. Worse, ``_available_required_prices`` treats only
``provider_verified`` and ``already_landed`` as establishing coverage, so ``^GSPC`` and
``^VIX`` — both declared required price inputs, both holding data through the provider's
own latest date — were reported as ``uncovered_symbol``. A direct probe of the provider
returned real OHLC for both, so the data and the provider were fine and only the label
was wrong.

That is the expensive kind of wrong: it makes a healthy refresh indistinguishable from a
broken one, and it points an investigation at the provider instead of at the classifier.
"""

from __future__ import annotations

from facdrone_data.acquisition.families.refresher import ProviderOutcome, _provider_outcome


def test_no_gaps_means_the_panel_already_holds_it() -> None:
    """The case that was misreported: nothing requested because nothing was missing."""
    assert _provider_outcome(0, 0, 0, 0) is ProviderOutcome.ALREADY_LANDED


def test_every_requested_gap_answered_is_verified() -> None:
    assert _provider_outcome(3, 3, 3, 0) is ProviderOutcome.PROVIDER_VERIFIED


def test_some_gaps_answered_is_partial() -> None:
    assert _provider_outcome(1, 3, 3, 0) is ProviderOutcome.PARTIAL_PROVIDER_COVERAGE


def test_responses_that_no_gap_could_use_are_quality_rejected() -> None:
    """The provider answered; the answer did not survive the quality gate."""
    assert _provider_outcome(0, 2, 2, 0) is ProviderOutcome.QUALITY_REJECTED


def test_gaps_requested_and_nothing_came_back_is_a_real_provider_failure() -> None:
    """The meaning the label should have kept: asked, and got nothing."""
    assert _provider_outcome(0, 0, 2, 0) is ProviderOutcome.PROVIDER_UNAVAILABLE


def test_up_to_date_and_unavailable_are_distinguishable() -> None:
    """The whole point. These two states must never share a label again."""
    nothing_to_do = _provider_outcome(0, 0, 0, 0)
    asked_and_failed = _provider_outcome(0, 0, 2, 0)

    assert nothing_to_do is not asked_and_failed


def test_every_gap_answered_with_no_rows_is_empty_not_an_outage() -> None:
    """A closed market answers the window with nothing: asked, answered, no rows. Only a
    client that says so (``ProviderAnsweredEmpty``) earns the label; any failed gap among
    them keeps the outage, and an empty frame from a client that cannot tell does too."""
    assert _provider_outcome(0, 0, 2, 2) is ProviderOutcome.PROVIDER_EMPTY
    assert _provider_outcome(0, 0, 2, 1) is ProviderOutcome.PROVIDER_UNAVAILABLE
    assert _provider_outcome(0, 0, 2, 0) is ProviderOutcome.PROVIDER_UNAVAILABLE
    assert _provider_outcome(1, 1, 2, 1) is ProviderOutcome.PARTIAL_PROVIDER_COVERAGE
    assert _provider_outcome(0, 1, 2, 1) is ProviderOutcome.QUALITY_REJECTED
