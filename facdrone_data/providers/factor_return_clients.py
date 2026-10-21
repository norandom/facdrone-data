# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The keyless factor-return live clients: Ken French (two datasets, routed) and AQR."""

from __future__ import annotations

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
from facdrone_data.providers.kenfrench_source import KenFrenchClient, live_kenfrench
from facdrone_data.providers.routing_client import route
from facdrone_data_types.ports import MarketDataClient
from facdrone_data_types.symbols import Provider


def factor_return_clients() -> dict[Provider, MarketDataClient]:
    """Ken French serves the FF3 panel and the factor_returns re-projection (MKT/SMB/HML) +
    momentum (MOM), so it routes on ``req.dataset``; AQR serves its workbooks."""
    archives = (US_FF3, US_MOMENTUM, EUROPE_FF3, EUROPE_MOMENTUM)
    returns = KenFrenchFactorReturnsClient({a: live_kenfrench_archive(a) for a in archives})
    ken_french = route(Provider.KEN_FRENCH, [KenFrenchClient(live_kenfrench()), returns])
    return {Provider.KEN_FRENCH: ken_french, Provider.AQR: AqrFactorReturnsClient(live_aqr())}
