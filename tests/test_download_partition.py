# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The runner's partition and the declared universe, without a database (data-manager 1.6).

R4.2 and R6.3: before any request, a missing key refuses a family (naming the variable) and
a missing client library makes it unavailable (naming the extra).
"""

from __future__ import annotations

from typing import cast

from sqlalchemy import Engine

from download_support import KNOWN_AT, two_instrument_universe
from facdrone_data.acquisition.families.credentials import ProviderCredentials
from facdrone_data.acquisition.families.refresher import Refusal
from facdrone_data.runner import partition_families, select_families
from facdrone_data_types.symbols import Provider


def test_the_partition_names_the_variable_or_the_extra_before_any_request() -> None:
    families = select_families("fmp,yfinance,dukascopy")
    runnable, refused = partition_families(
        families, ProviderCredentials(None, None), installed=lambda name: name != "requests"
    )
    assert {port.provider for port in runnable} == {Provider.YFINANCE}
    reasons = {(item.refusal, item.needs) for item in refused}
    assert reasons == {(Refusal.UNAVAILABLE, "fmp"), (Refusal.UNAVAILABLE, "dukascopy")}
    _, keyed = partition_families(
        families, ProviderCredentials(None, None), installed=lambda name: True
    )
    assert {(item.refusal, item.needs) for item in keyed} == {(Refusal.REFUSED, "FMP_API_KEY")}


def test_the_declared_universe_derives_the_configured_instruments() -> None:
    requirement = two_instrument_universe().requirement(cast("Engine", None), KNOWN_AT)
    assert {symbol.canonical_id for symbol in requirement.canonical_symbols} == {"AAA", "BBB"}
    assert requirement.provider_symbols.fmp_price("AAA") == "AAA"  # no table row: fallback
