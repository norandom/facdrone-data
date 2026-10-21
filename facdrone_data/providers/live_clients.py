# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Live provider-client wiring (extracted from ``download_root``): the ``Provider ->
MarketDataClient`` map from loaded credentials. The FMP-keyed clients are composed in
``fmp_clients`` and the factor-return clients in ``factor_return_clients``.

The provider imports stay function-local inside :func:`live_clients` so importing this
module (and therefore the CLI) never eagerly loads every provider SDK — they load only
when the live runner is actually bound.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from facdrone_data_types.symbols import Provider

if TYPE_CHECKING:
    from facdrone_data.acquisition.families.credentials import ProviderCredentials
    from facdrone_data_types.ports import MarketDataClient


def live_clients(credentials: ProviderCredentials) -> dict[Provider, MarketDataClient]:
    """Construct every live provider transport from the loaded credentials.

    Keyless providers (yfinance/CFTC/Ken French/AQR/OECD/Dukascopy) are always built; the
    FMP-keyed providers (all FMP families + ETF, which uses the FMP key) and FRED
    are built only when their key is present. A provider with no client is skipped
    at fetch time — and ``require_credentials`` fails fast first when such a source
    is explicitly selected."""
    from facdrone_data.providers.cftc_source import CftcCotClient, live_cftc
    from facdrone_data.providers.dukascopy_source import DukascopyOrderFlowClient, live_dukascopy
    from facdrone_data.providers.factor_return_clients import factor_return_clients
    from facdrone_data.providers.fmp_clients import fmp_clients
    from facdrone_data.providers.fred_source import FredSeriesClient, live_fred
    from facdrone_data.providers.oecd_source import OecdCpiClient, live_oecd
    from facdrone_data.providers.yfinance_source import YFinanceDailyClient, live_yfinance

    clients: dict[Provider, MarketDataClient] = {
        Provider.YFINANCE: YFinanceDailyClient(live_yfinance()),
        Provider.CFTC: CftcCotClient(live_cftc()),
        **factor_return_clients(),
        Provider.OECD: OecdCpiClient(live_oecd()),
        Provider.DUKASCOPY: DukascopyOrderFlowClient(live_dukascopy()),
    }
    if credentials.fmp_api_key is not None:
        clients.update(fmp_clients(credentials.fmp_api_key))
    if credentials.fred_api_key is not None:
        clients[Provider.FRED] = FredSeriesClient(live_fred(credentials.fred_api_key))
    return clients
