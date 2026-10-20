# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Coverage interval/gap math (task 4; R2.1, R2.2).

Pure interval subtraction over inclusive day ranges. The acquisition node
DERIVES a panel's present contiguous ranges on read (per-symbol
``MIN/MAX(date)`` + a gap scan — never a separate index that could drift) and
hands them here as an array; this function returns only the sub-ranges of the
requested window not already covered. A fully-covered request yields ``()`` —
the structural basis for zero downloads (R2.1); a partially-covered request
yields only the gaps (R2.2).

Library tier: stdlib only, array-in/array-out, no IO and no knowledge dates.
Whether two ranges are *contiguous* (e.g. across a weekend) is decided upstream
by the gap scan that knows the trading calendar; here a range is a closed
``[start, end]`` interval and adjacency is one calendar day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

_ONE_DAY = timedelta(days=1)


@dataclass(frozen=True)
class Coverage:
    """The contiguous covered ranges of one (symbol, dataset) panel.

    ``present`` is a tuple of inclusive ``(start, end)`` day ranges derived
    from the stored panel. Need not be sorted or disjoint on input —
    :func:`missing_ranges` normalizes it.
    """

    present: tuple[tuple[date, date], ...]


def missing_ranges(coverage: Coverage, want: tuple[date, date]) -> tuple[tuple[date, date], ...]:
    """Return the inclusive sub-ranges of ``want`` not covered by ``coverage``.

    The result is the set difference ``want \\ union(coverage.present)`` at
    one-day granularity, as a sorted tuple of disjoint inclusive ranges. An
    inverted request (``start > end``) and a fully-covered request both yield
    ``()``.
    """
    want_start, want_end = want
    if want_start > want_end:
        return ()

    ranges = sorted((a, b) for (a, b) in coverage.present if a <= b)
    missing: list[tuple[date, date]] = []
    cursor = want_start
    for a, b in ranges:
        if b < want_start:
            continue
        if a > want_end:
            break
        a = max(a, want_start)
        b = min(b, want_end)
        if a > cursor:
            missing.append((cursor, a - _ONE_DAY))
        cursor = max(cursor, b + _ONE_DAY)
        if cursor > want_end:
            break
    if cursor <= want_end:
        missing.append((cursor, want_end))
    return tuple(missing)
