# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""One ``MarketDataClient`` per provider over several family clients, routed on the dataset."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

from facdrone_data_types.ports import FetchRequest, MarketDataClient
from facdrone_data_types.symbols import Provider


@dataclass(frozen=True)
class RoutingClient:
    """A ``MarketDataClient`` that routes ``fetch`` to a per-dataset sub-client.

    Several family clients share one ``Provider`` (every FMP family is
    ``Provider.FMP``), but the refresher's ``clients`` map is keyed by provider —
    so they are composed here into one client that dispatches on ``req.dataset``.
    An unrouted dataset returns empty (fail-open skip)."""

    provider: Provider
    routes: Mapping[str, MarketDataClient]

    def fetch(self, req: FetchRequest) -> pd.DataFrame:
        sub = self.routes.get(req.dataset)
        return sub.fetch(req) if sub is not None else pd.DataFrame()

    def datasets(self) -> tuple[str, ...]:
        return tuple(self.routes)


def route(provider: Provider, clients: list[MarketDataClient]) -> RoutingClient:
    """Each client's datasets routed to it; a later client wins a dataset both serve."""
    routes: dict[str, MarketDataClient] = {}
    for client in clients:
        for dataset in client.datasets():
            routes[dataset] = client
    return RoutingClient(provider, routes)
