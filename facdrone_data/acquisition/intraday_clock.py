# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Timestamp declarations for intraday sources, applied only at the panel read boundary."""

from __future__ import annotations

from dataclasses import dataclass
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


def intraday_timestamp(moment: datetime, provider: Provider) -> datetime:
    """Localize a naive provider opening once; aware input keeps its actual instant.

    DST folds and gaps cannot be inferred from a timezone-free stored value. Refuse
    them rather than silently guessing a fold or moving a nonexistent bar forward.
    """
    if moment.utcoffset() is not None:
        return moment.astimezone(UTC)
    zone = INTRADAY_TIMESTAMP_ZONES.get(provider)
    if zone is None:
        raise IntradayTimestampError(f"undeclared intraday timestamp zone: {provider}")
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


def intraday_or_skip(moment: datetime, provider: Provider) -> datetime | SkippedStamp:
    """The intraday load rule: localize as ``intraday_timestamp`` does, but a fold or
    gap stamp becomes a ``SkippedStamp`` for the caller to drop and count, never a bar
    at a guessed instant. An undeclared provider still refuses."""
    try:
        return intraday_timestamp(moment, provider)
    except IntradayFoldError:
        zone = ZoneInfo(INTRADAY_TIMESTAMP_ZONES[provider])
        earliest, latest = sorted(
            moment.replace(tzinfo=zone, fold=fold).astimezone(UTC) for fold in (0, 1)
        )
        return SkippedStamp(moment, earliest, latest)
