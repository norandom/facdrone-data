# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Live provider-client wiring (extracted from ``download_root``).

The single module that knows every concrete provider source and assembles the
``Provider -> MarketDataClient`` map from loaded credentials. It is intentionally
the one high-fan-out "wiring" module: composing N providers means importing N
provider transports. Keeping it here lets the data composition root
(``download_root``) stay lean orchestration rather than a god file mixing
orchestration with transport construction.

The concrete provider imports stay function-local inside :func:`live_clients` so
importing this module (and therefore the CLI) never eagerly loads every provider
SDK — they load only when the live runner is actually bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from collections.abc import Mapping

    from facdrone_data.acquisition.families.credentials import ProviderCredentials
    from facdrone_data_types.ports import FetchRequest, MarketDataClient


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


def _route(provider: Provider, clients: list[MarketDataClient]) -> RoutingClient:
    routes: dict[str, MarketDataClient] = {}
    for client in clients:
        for dataset in client.datasets():
            routes[dataset] = client
    return RoutingClient(provider, routes)


def live_clients(credentials: ProviderCredentials) -> dict[Provider, MarketDataClient]:
    """Construct every live provider transport from the loaded credentials.

    Keyless providers (yfinance/CFTC/Ken French/OECD) are always built; the
    FMP-keyed providers (all FMP families + ETF, which uses the FMP key) and FRED
    are built only when their key is present. A provider with no client is skipped
    at fetch time — and ``require_credentials`` fails fast first when such a source
    is explicitly selected."""
    from facdrone_data.providers.cftc_source import CftcCotClient, live_cftc
    from facdrone_data.providers.dukascopy_source import (
        DukascopyOrderFlowClient,
        live_dukascopy,
    )
    from facdrone_data.providers.etf_source import EtfClient, live_etf
    from facdrone_data.providers.factor_returns_source import (
        EUROPE_FF3,
        EUROPE_MOMENTUM,
        US_FF3,
        US_MOMENTUM,
        AqrFactorReturnsClient,
        KenFrenchFactorReturnsClient,
        live_aqr,
        live_kenfrench_archive,
    )
    from facdrone_data.providers.fmp_corporate import FmpCorporateClient
    from facdrone_data.providers.fmp_cot import live_fmp_cot
    from facdrone_data.providers.fmp_endpoints import FmpPriceClient, live_http_json
    from facdrone_data.providers.fmp_fundamentals import live_fmp_fundamentals
    from facdrone_data.providers.fmp_macro import live_fmp_macro
    from facdrone_data.providers.fmp_ownership import live_fmp_ownership
    from facdrone_data.providers.fred_source import FredSeriesClient, live_fred
    from facdrone_data.providers.kenfrench_source import KenFrenchClient, live_kenfrench
    from facdrone_data.providers.oecd_source import OecdCpiClient, live_oecd
    from facdrone_data.providers.yfinance_source import YFinanceDailyClient, live_yfinance

    clients: dict[Provider, MarketDataClient] = {
        Provider.YFINANCE: YFinanceDailyClient(live_yfinance()),
        Provider.CFTC: CftcCotClient(live_cftc()),
        # Ken French now serves two datasets — the FF3 panel and the factor_returns
        # re-projection (MKT/SMB/HML) + momentum (MOM) — so it routes on req.dataset.
        Provider.KEN_FRENCH: _route(
            Provider.KEN_FRENCH,
            [
                KenFrenchClient(live_kenfrench()),
                KenFrenchFactorReturnsClient(
                    {
                        archive: live_kenfrench_archive(archive)
                        for archive in (US_FF3, US_MOMENTUM, EUROPE_FF3, EUROPE_MOMENTUM)
                    }
                ),
            ],
        ),
        Provider.AQR: AqrFactorReturnsClient(live_aqr()),
        Provider.OECD: OecdCpiClient(live_oecd()),
        Provider.DUKASCOPY: DukascopyOrderFlowClient(live_dukascopy()),
    }
    if credentials.fmp_api_key is not None:
        key = credentials.fmp_api_key
        transport = live_http_json()
        clients[Provider.FMP] = _route(
            Provider.FMP,
            [
                FmpPriceClient(transport, key),
                live_fmp_fundamentals(key),
                FmpCorporateClient(transport, key),
                live_fmp_ownership(key),
                live_fmp_cot(key),
                live_fmp_macro(key),
            ],
        )
        clients[Provider.ETF] = EtfClient(live_etf(), key)
    if credentials.fred_api_key is not None:
        clients[Provider.FRED] = FredSeriesClient(live_fred(credentials.fred_api_key))
    return clients
