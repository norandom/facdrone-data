# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The live provider wiring units: the dataset router, the FMP-keyed and factor-return clients.

Every client is constructed offline (the transports open nothing until a fetch), so the
tests read the composed maps only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from facdrone_data.acquisition.families.credentials import ProviderCredentials
from facdrone_data.providers.factor_return_clients import factor_return_clients
from facdrone_data.providers.fmp_clients import fmp_clients
from facdrone_data.providers.live_clients import live_clients
from facdrone_data.providers.routing_client import RoutingClient, route
from facdrone_data_types.ports import FetchRequest
from facdrone_data_types.symbols import Provider


@dataclass(frozen=True)
class _Family:
    name: str
    served: tuple[str, ...]
    provider: Provider = Provider.FMP

    def fetch(self, req: FetchRequest) -> pd.DataFrame:
        return pd.DataFrame({"family": [self.name], "dataset": [req.dataset]})

    def datasets(self) -> tuple[str, ...]:
        return self.served


def _request(dataset: str) -> FetchRequest:
    return FetchRequest("SYM", dataset, date(2026, 1, 2), date(2026, 1, 3))


def test_the_router_sends_each_dataset_to_its_family_and_the_later_family_wins() -> None:
    client = route(Provider.FMP, [_Family("a", ("x", "y")), _Family("b", ("y", "z"))])

    assert client.datasets() == ("x", "y", "z")
    assert client.fetch(_request("x"))["family"].tolist() == ["a"]
    assert client.fetch(_request("y"))["family"].tolist() == ["b"]
    assert client.fetch(_request("unrouted")).empty


def test_the_fmp_key_builds_the_routed_fmp_families_and_the_etf_client() -> None:
    clients = fmp_clients("secret")

    assert list(clients) == [Provider.FMP, Provider.ETF]
    fmp = clients[Provider.FMP]
    assert isinstance(fmp, RoutingClient)
    assert len(set(fmp.datasets())) == len(fmp.datasets()) >= 6
    assert clients[Provider.ETF].datasets()


def test_ken_french_routes_both_of_its_datasets_and_aqr_stands_alone() -> None:
    clients = factor_return_clients()

    assert list(clients) == [Provider.KEN_FRENCH, Provider.AQR]
    assert set(clients[Provider.KEN_FRENCH].datasets()) == {"fama_french_factors", "factor_returns"}


def test_the_keyed_providers_are_built_only_when_their_key_is_present() -> None:
    keyless = [
        Provider.YFINANCE,
        Provider.CFTC,
        Provider.KEN_FRENCH,
        Provider.AQR,
        Provider.OECD,
        Provider.DUKASCOPY,
    ]

    assert list(live_clients(ProviderCredentials(None, None))) == keyless
    assert list(live_clients(ProviderCredentials("f", None))) == [
        *keyless,
        Provider.FMP,
        Provider.ETF,
    ]
    assert list(live_clients(ProviderCredentials(None, "r"))) == [*keyless, Provider.FRED]
