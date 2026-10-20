# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Publication rules: when a family's next observation becomes knowable
(task 2.1; R1.2).

Pure arithmetic over an injected as-of and the observed-union trading calendar:
no IO, no clock, no timer. Identical inputs give identical outputs on every
machine, so the daemon, the status surface and the report agree by construction.

Vocabulary:

* the **expected observation** at an as-of is the latest observation date whose
  publication moment — rule-specific — is at or before the as-of;
* a family is **current** when its expected observation is present; otherwise the
  wait is measured from the publication moment of the EARLIEST missing observation
  (a family three prints behind is three prints old): **current** within the declared
  grace (``warning_after``), **warning** within ``max_age``, **stale** beyond it;
  **unavailable** when nothing is present at all.

Intraday families use opening timestamps: the expected bar has closed by the
as-of, and age is elapsed time since the latest stored bar's timestamp.

Static families have no publication moment: present is current, absent is
unavailable. Vintage families (filings) have no calendar either: the age of the
latest vintage against the declared ages is the whole story.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from facdrone_data_types.freshness import FreshnessState

_WEEKDAYS = 5  # Monday .. Friday


class RuleLike(Protocol):
    """The publication-rule fields this module reads (the declaration's record satisfies it)."""

    @property
    def kind(self) -> str: ...
    @property
    def publish_after_utc(self) -> time | None: ...
    @property
    def release_weekday(self) -> int | None: ...
    @property
    def release_time(self) -> time | None: ...
    @property
    def release_timezone(self) -> str | None: ...
    @property
    def as_of_offset_days(self) -> int: ...
    @property
    def holiday_tolerance_days(self) -> int: ...
    @property
    def bar_minutes(self) -> int | None: ...


@dataclass(frozen=True)
class Ages:
    """The declared grace and limit for one family (timedeltas, positive)."""

    warning_after: timedelta
    max_age: timedelta


@dataclass(frozen=True)
class Freshness:
    """One family's freshness at an as-of: the expected observation, what is present, the
    wait since the expected publication moment, and the state those imply."""

    expected: date | None  # datetime (a date subtype) for bar_interval
    latest_present: date | None  # datetime for bar_interval; never date-reduced
    waited: timedelta | None  # since the expected observation's publication moment
    state: FreshnessState


def _require_aware(as_of: datetime) -> datetime:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return as_of.astimezone(UTC)


def _is_weekday(day: date) -> bool:
    return day.weekday() < _WEEKDAYS


def _next_weekday(day: date) -> date:
    candidate = day + timedelta(days=1)
    while not _is_weekday(candidate):
        candidate += timedelta(days=1)
    return candidate


def _latest_calendar_day(calendar: Sequence[date], at_or_before: date) -> date | None:
    latest: date | None = None
    for day in calendar:
        if day <= at_or_before and (latest is None or day > latest):
            latest = day
    return latest


def _publish_after(rule: RuleLike) -> time:
    if rule.publish_after_utc is None:
        raise ValueError(f"{rule.kind} needs publish_after_utc")
    return rule.publish_after_utc


def _release_clock(rule: RuleLike) -> tuple[int, time, ZoneInfo]:
    if rule.release_weekday is None or rule.release_time is None or rule.release_timezone is None:
        raise ValueError("weekly_release needs release_weekday, release_time and release_timezone")
    return rule.release_weekday, rule.release_time, ZoneInfo(rule.release_timezone)


def publication_moment(rule: RuleLike, observation: date) -> datetime | None:
    """When the observation for ``observation`` becomes knowable, in UTC; ``None`` for a
    rule with no publication moment (static, derived, vintage)."""
    if rule.kind == "bar_interval":
        if not isinstance(observation, datetime):
            raise ValueError("bar_interval observation must be a timestamp")
        return _require_aware(observation) + _bar_interval(rule)
    if rule.kind == "daily_bars":
        return datetime.combine(observation, _publish_after(rule), tzinfo=UTC)
    if rule.kind == "next_business_day":
        return datetime.combine(_next_weekday(observation), _publish_after(rule), tzinfo=UTC)
    if rule.kind == "weekly_release":
        _, release_time, zone = _release_clock(rule)
        release_day = observation + timedelta(days=rule.as_of_offset_days)
        local = datetime.combine(release_day, release_time, tzinfo=zone)
        return local.astimezone(UTC) + timedelta(days=rule.holiday_tolerance_days)
    return None


def _knowable(rule: RuleLike, observation: date, at: datetime) -> bool:
    moment = publication_moment(rule, observation)
    return moment is not None and moment <= at


def _expected_daily(rule: RuleLike, at: datetime, calendar: Sequence[date]) -> date | None:
    candidate = _latest_calendar_day(calendar, at.date())
    while candidate is not None and not _knowable(rule, candidate, at):
        candidate = _latest_calendar_day(calendar, candidate - timedelta(days=1))
    return candidate


def _expected_next_business_day(rule: RuleLike, at: datetime) -> date | None:
    candidate = at.date()
    for _ in range(14):  # two weeks is more than any weekend or holiday run
        if _is_weekday(candidate) and _knowable(rule, candidate, at):
            return candidate
        candidate -= timedelta(days=1)
    return None


def _expected_weekly(rule: RuleLike, at: datetime) -> date | None:
    weekday, _, _ = _release_clock(rule)
    release_day = at.date()
    while release_day.weekday() != weekday:
        release_day -= timedelta(days=1)
    for _ in range(4):  # at most a few slipped or skipped releases
        observation = release_day - timedelta(days=rule.as_of_offset_days)
        if _knowable(rule, observation, at):
            return observation
        release_day -= timedelta(days=7)
    return None


def expected_observation(rule: RuleLike, as_of: datetime, calendar: Sequence[date]) -> date | None:
    """The latest observation date whose publication moment is at or before ``as_of``.

    ``daily_bars`` walks the trading calendar (the observed-union calendar the reading
    system steps through); ``next_business_day`` walks weekdays; ``weekly_release`` walks the
    release weekday with the as-of offset and the holiday tolerance; the other kinds
    have no expected observation.
    """
    at = _require_aware(as_of)
    if rule.kind == "bar_interval":
        interval = _bar_interval(rule)
        return at - (at - datetime(1970, 1, 1, tzinfo=UTC)) % interval - interval
    if rule.kind == "daily_bars":
        return _expected_daily(rule, at, calendar)
    if rule.kind == "next_business_day":
        return _expected_next_business_day(rule, at)
    if rule.kind == "weekly_release":
        return _expected_weekly(rule, at)
    return None


def _bar_interval(rule: RuleLike) -> timedelta:
    if type(rule.bar_minutes) is not int or rule.bar_minutes <= 0:
        raise ValueError("bar_interval needs positive integer bar_minutes")
    return timedelta(minutes=rule.bar_minutes)


def _first_missing(
    rule: RuleLike, latest_present: date, expected: date, calendar: Sequence[date]
) -> date:
    """The earliest expected observation after what is present — the wait is measured
    from ITS publication moment, so a family three prints behind is three prints old,
    not one."""
    if rule.kind == "daily_bars":
        later = [day for day in calendar if latest_present < day <= expected]
        return min(later) if later else expected
    if rule.kind == "next_business_day":
        return min(_next_weekday(latest_present), expected)
    return min(latest_present + timedelta(days=7), expected)


def freshness_at(
    rule: RuleLike,
    ages: Ages,
    latest_present: date | None,
    as_of: datetime,
    calendar: Sequence[date],
) -> Freshness:
    """The family's state at ``as_of`` given what is present (see the module docstring)."""
    at = _require_aware(as_of)
    if rule.kind == "bar_interval":
        return _bar_freshness(rule, ages, latest_present, at, calendar)
    if rule.kind == "static":
        state = FreshnessState.CURRENT if latest_present is not None else FreshnessState.UNAVAILABLE
        return Freshness(expected=None, latest_present=latest_present, waited=None, state=state)
    if rule.kind in {"statement_vintage", "derived"}:
        return _vintage_freshness(ages, latest_present, at)
    expected = expected_observation(rule, at, calendar)
    if latest_present is None:
        return Freshness(expected, None, None, FreshnessState.UNAVAILABLE)
    if expected is None or latest_present >= expected:
        return Freshness(expected, latest_present, timedelta(0), FreshnessState.CURRENT)
    moment = publication_moment(rule, _first_missing(rule, latest_present, expected, calendar))
    if moment is None:
        return Freshness(expected, latest_present, None, FreshnessState.UNAVAILABLE)
    waited = at - moment
    return Freshness(expected, latest_present, waited, _by_wait(waited, ages))


def _bar_freshness(
    rule: RuleLike,
    ages: Ages,
    latest_present: date | None,
    at: datetime,
    calendar: Sequence[date],
) -> Freshness:
    expected = expected_observation(rule, at, calendar)
    if latest_present is None:
        return Freshness(expected, None, None, FreshnessState.UNAVAILABLE)
    if not isinstance(latest_present, datetime):
        raise ValueError("bar_interval latest_present must be a timestamp")
    waited = max(timedelta(0), at - _require_aware(latest_present))
    return Freshness(expected, latest_present, waited, _by_wait(waited, ages))


def _vintage_freshness(ages: Ages, latest_present: date | None, at: datetime) -> Freshness:
    if latest_present is None:
        return Freshness(None, None, None, FreshnessState.UNAVAILABLE)
    waited = at - datetime.combine(latest_present, time(0), tzinfo=UTC)
    return Freshness(None, latest_present, waited, _by_wait(waited, ages))


def _by_wait(waited: timedelta, ages: Ages) -> FreshnessState:
    if waited <= ages.warning_after:
        return FreshnessState.CURRENT
    if waited <= ages.max_age:
        return FreshnessState.WARNING
    return FreshnessState.STALE


StateName = Literal["current", "warning", "stale", "unavailable"]

__all__ = [
    "Ages",
    "Freshness",
    "RuleLike",
    "StateName",
    "expected_observation",
    "freshness_at",
    "publication_moment",
]
