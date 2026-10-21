# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The FMP-keyed live clients: every FMP family routed as one provider, and ETF on the FMP key."""

from __future__ import annotations

from facdrone_data.providers.etf_source import EtfClient, live_etf
from facdrone_data.providers.fmp_corporate import FmpCorporateClient
from facdrone_data.providers.fmp_cot import live_fmp_cot
from facdrone_data.providers.fmp_endpoints import FmpPriceClient, live_http_json
from facdrone_data.providers.fmp_fundamentals import live_fmp_fundamentals
from facdrone_data.providers.fmp_macro import live_fmp_macro
from facdrone_data.providers.fmp_ownership import live_fmp_ownership
from facdrone_data.providers.routing_client import route
from facdrone_data_types.ports import MarketDataClient
from facdrone_data_types.symbols import Provider


def fmp_clients(key: str) -> dict[Provider, MarketDataClient]:
    """The FMP families over one shared transport, and the ETF client, both on ``key``."""
    transport = live_http_json()
    families: list[MarketDataClient] = [
        FmpPriceClient(transport, key),
        live_fmp_fundamentals(key),
        FmpCorporateClient(transport, key),
        live_fmp_ownership(key),
        live_fmp_cot(key),
        live_fmp_macro(key),
    ]
    return {Provider.FMP: route(Provider.FMP, families), Provider.ETF: EtfClient(live_etf(), key)}
