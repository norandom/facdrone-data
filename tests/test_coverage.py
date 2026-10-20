# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 4: coverage interval/gap math (Req 2.1, 2.2).

Pure interval subtraction: given a panel's present contiguous ranges and a
requested range, return only the missing sub-ranges. A fully-covered request
yields ``()`` — the basis for zero downloads (R2.1); a partially-covered
request yields only the gaps (R2.2). Array-in/array-out, no IO.
"""

from __future__ import annotations

from datetime import date

from facdrone_data.coverage import Coverage, missing_ranges


def _d(month: int, day: int) -> date:
    return date(2024, month, day)


def test_fully_covered_request_yields_no_missing_ranges() -> None:
    # The basis for zero downloads (R2.1).
    cov = Coverage(present=((_d(1, 1), _d(1, 31)),))
    assert missing_ranges(cov, (_d(1, 5), _d(1, 20))) == ()


def test_empty_coverage_yields_the_whole_request() -> None:
    cov = Coverage(present=())
    assert missing_ranges(cov, (_d(1, 1), _d(1, 10))) == ((_d(1, 1), _d(1, 10)),)


def test_partial_coverage_yields_only_the_tail_gap() -> None:
    cov = Coverage(present=((_d(1, 1), _d(1, 5)),))
    assert missing_ranges(cov, (_d(1, 1), _d(1, 10))) == ((_d(1, 6), _d(1, 10)),)


def test_partial_coverage_yields_only_the_leading_gap() -> None:
    cov = Coverage(present=((_d(1, 6), _d(1, 10)),))
    assert missing_ranges(cov, (_d(1, 1), _d(1, 10))) == ((_d(1, 1), _d(1, 5)),)


def test_interior_gap_between_two_present_ranges() -> None:
    cov = Coverage(present=((_d(1, 1), _d(1, 5)), (_d(1, 8), _d(1, 10))))
    assert missing_ranges(cov, (_d(1, 1), _d(1, 10))) == ((_d(1, 6), _d(1, 7)),)


def test_multiple_gaps_are_each_returned() -> None:
    cov = Coverage(present=((_d(1, 3), _d(1, 4)), (_d(1, 8), _d(1, 9))))
    assert missing_ranges(cov, (_d(1, 1), _d(1, 12))) == (
        (_d(1, 1), _d(1, 2)),
        (_d(1, 5), _d(1, 7)),
        (_d(1, 10), _d(1, 12)),
    )


def test_adjacent_present_ranges_leave_no_gap() -> None:
    # jan5 then jan6 are consecutive days: contiguous, no gap.
    cov = Coverage(present=((_d(1, 1), _d(1, 5)), (_d(1, 6), _d(1, 10))))
    assert missing_ranges(cov, (_d(1, 1), _d(1, 10))) == ()


def test_present_ranges_outside_the_request_are_ignored() -> None:
    cov = Coverage(present=((_d(1, 1), _d(1, 2)),))
    assert missing_ranges(cov, (_d(2, 1), _d(2, 5))) == ((_d(2, 1), _d(2, 5)),)


def test_unsorted_and_overlapping_present_ranges_are_normalized() -> None:
    cov = Coverage(present=((_d(1, 8), _d(1, 12)), (_d(1, 1), _d(1, 5)), (_d(1, 3), _d(1, 9))))
    # union covers jan1..jan12 → fully covers a jan2..jan11 request.
    assert missing_ranges(cov, (_d(1, 2), _d(1, 11))) == ()


def test_inverted_request_yields_nothing() -> None:
    cov = Coverage(present=())
    assert missing_ranges(cov, (_d(1, 10), _d(1, 1))) == ()


def test_single_day_gap_is_returned() -> None:
    cov = Coverage(present=((_d(1, 1), _d(1, 4)), (_d(1, 6), _d(1, 10))))
    assert missing_ranges(cov, (_d(1, 1), _d(1, 10))) == ((_d(1, 5), _d(1, 5)),)
