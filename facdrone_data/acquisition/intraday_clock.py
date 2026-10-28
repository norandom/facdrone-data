# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Timestamp declarations for intraday sources, applied only at the panel read boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from zoneinfo import ZoneInfo

from facdrone_data_types.symbols import Provider

# FMP stores local Eastern bar openings unchanged. The operator measured the
# 17:00 maintenance gap in winter AND summer. Other registered providers write
# daily prices/order flow only; they have no declared naive intraday convention.
INTRADAY_TIMESTAMP_ZONES = MappingProxyType({Provider.FMP: "America/New_York"})


class IntradayTimestampError(ValueError):
    """An intraday opening cannot be resolved to one declared, unambiguous instant."""


class IntradayFoldError(IntradayTimestampError):
    """A naive opening falls in a DST fold or gap of its declared zone."""


def provider_zone(provider: Provider) -> str:
    """The zone a provider stamps a row in when the row declares none of its own."""
    zone = INTRADAY_TIMESTAMP_ZONES.get(provider)
    if zone is None:
        raise IntradayTimestampError(f"undeclared intraday timestamp zone: {provider}")
    return zone


@dataclass(frozen=True)
class IntradayZones:
    """The stamp zone of each symbol's stored openings: the symbol's declared zone (the
    catalog's ``intraday_zone``), else the provider's ``default``. Data a root reads from
    its identity table and hands every reader of a stored stamp."""

    default: str
    declared: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def provider(cls, provider: Provider) -> IntradayZones:
        """Every symbol in the provider's declared zone: what a reader without a table knows."""
        return cls(provider_zone(provider))

    def of(self, symbol: str) -> str:
        return self.declared.get(symbol, self.default)

    def __hash__(self) -> int:  # requests and run inputs that carry it stay hashable
        return hash((self.default, tuple(sorted(self.declared.items()))))


def intraday_timestamp(moment: datetime, zone: str) -> datetime:
    """Localize a naive opening stamped in ``zone`` once; aware input keeps its instant.

    DST folds and gaps cannot be inferred from a timezone-free stored value. Refuse
    them rather than silently guessing a fold or moving a nonexistent bar forward.
    """
    if moment.utcoffset() is not None:
        return moment.astimezone(UTC)
    local = moment.replace(tzinfo=ZoneInfo(zone))
    instant = local.astimezone(UTC)
    if (
        instant.astimezone(local.tzinfo).replace(tzinfo=None) != moment
        or local.utcoffset() != local.replace(fold=1).utcoffset()
    ):
        raise IntradayFoldError(f"ambiguous or nonexistent {zone} intraday stamp: {moment}")
    return instant


@dataclass(frozen=True)
class SkippedStamp:
    """A stored naive opening that names no single instant, so no bar was built from it.

    In the fall-back hour the same naive stamp occurs twice and the table keeps one
    row per stamp, so which real bar survived is unknowable; in the spring-forward
    gap the stamp never happened. ``earliest``/``latest`` are the UTC instants the
    stamp would mean under each of the zone's two offsets: they bound where the
    missing bar belongs without choosing one.
    """

    local: datetime
    earliest: datetime
    latest: datetime

    def within(self, start: datetime, end: datetime, interval: timedelta) -> bool:
        """Whether either reading would have placed the whole bar inside ``[start, end]``."""
        return any(start <= t and t + interval <= end for t in (self.earliest, self.latest))


def intraday_or_skip(moment: datetime, zone: str) -> datetime | SkippedStamp:
    """The intraday load rule: localize as ``intraday_timestamp`` does, but a fold or
    gap stamp of ``zone`` becomes a ``SkippedStamp`` for the caller to drop and count,
    never a bar at a guessed instant."""
    try:
        return intraday_timestamp(moment, zone)
    except IntradayFoldError:
        local = ZoneInfo(zone)
        earliest, latest = sorted(
            moment.replace(tzinfo=local, fold=fold).astimezone(UTC) for fold in (0, 1)
        )
        return SkippedStamp(moment, earliest, latest)
