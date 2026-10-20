# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Provider credentials: fail-fast, redacted (R8.3, R9.2, R9.4; facdrone-data-manager R4).

The value object carries the keys of one run, whichever source the composition root chose
(:mod:`facdrone_data.credentials`: the environment or one explicit file). It redacts its
keys in every string rendering (R9.2), and ``require`` fails fast, naming the source, the
key *name* and the environment variable (never a value), before any provider request is
made (R8.3). Keyless sources (yfinance/CFTC/Ken French/OECD) carry no key and never call
``require`` (R9.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from facdrone_data_types.symbols import Provider

# The key NAME each keyed source consumes. ETF prices are fetched from FMP endpoints, so the
# ETF source consumes the FMP key. Every other provider is keyless and must NOT appear here
# (``require`` rejects keyless sources).
_KEY_FOR: Final[dict[Provider, str]] = {
    Provider.FMP: "fmp_api_key",
    Provider.ETF: "fmp_api_key",
    Provider.FRED: "fred_api_key",
}
# The environment variable that carries each key name.
KEY_VARIABLES: Final[dict[str, str]] = {
    "fmp_api_key": "FMP_API_KEY",
    "fred_api_key": "FRED_API_KEY",
}


class MissingCredentialError(RuntimeError):
    """A selected source needs a credential the run's source does not provide.

    Raised as a precondition BEFORE any provider request (R8.3) so a partial run
    can never leak a key in a downstream stack trace."""


@dataclass(frozen=True)
class ProviderCredentials:
    """Redacted holder for the API keys (R9.2). ``None`` means "not configured"."""

    fmp_api_key: str | None
    fred_api_key: str | None

    def __repr__(self) -> str:
        # NEVER render a key — str() falls back to this too (R9.2).
        return "ProviderCredentials(redacted)"

    def values(self) -> tuple[str | None, ...]:
        """The configured key values, for redaction only."""
        return (self.fmp_api_key, self.fred_api_key)

    def missing_variable(self, provider: Provider) -> str | None:
        """The variable a keyed provider lacks; ``None`` when keyless or configured."""
        key_name = _KEY_FOR.get(provider)
        if key_name is None or getattr(self, key_name) is not None:
            return None
        return KEY_VARIABLES[key_name]

    def require(self, provider: Provider) -> str:
        """Return the key for a keyed ``provider`` or fail fast naming it (R8.3).

        A keyless provider is a programming error here (its adapter must never
        call ``require``); we reject it loudly rather than return an empty key."""
        key_name = _KEY_FOR.get(provider)
        if key_name is None:
            raise ValueError(
                f"{provider.value!r} is a keyless source; it has no credential to require"
            )
        value = getattr(self, key_name)
        if value is None:
            raise MissingCredentialError(
                f"source {provider.value!r} requires credential {key_name!r} "
                f"({KEY_VARIABLES[key_name]}), which is not configured"
            )
        return str(value)
