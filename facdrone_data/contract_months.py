# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Pure executable-month selection from declared delivery rules and calendar sessions.

Continuous price series supply no delivery identity. The simulation's roll convention
chooses the next delivery before notice/expiry; it never rolls an open position.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from facdrone_data_types.trading_calendar import CalendarNotCovered, TradingCalendar, trading_days


class ContractMonthNotCovered(ValueError):
    """A product, session, or date needed by its roll rule has no declaration."""


@dataclass(frozen=True)
class DeliveryDateRule:
    """An anchor in the delivery month, followed by calendar and session offsets.

    Negative day counts from month end. A weekday selects the first matching weekday
    on or after the anchor (day 15 + Friday is the third Friday). A non-session anchor
    moves to the preceding declared session before applying the signed session offset.
    """

    month_offset: int
    day: int
    weekday: int | None
    calendar_days: int
    sessions: int


@dataclass(frozen=True)
class ContractCycle:
    listed_months: tuple[int, ...]
    active_months: tuple[int, ...]
    last_trade: DeliveryDateRule
    roll: DeliveryDateRule
    first_notice: DeliveryDateRule | None
    listing: str
    convention: str
    source: str
    verify: bool


@dataclass(frozen=True)
class ContractCalendar:
    covered_from: date
    covered_through: date
    products: Mapping[str, ContractCycle]


def _month(year: int, month: int, offset: int) -> date:
    year, index = divmod(year * 12 + month - 1 + offset, 12)
    return date(year, index + 1, 1)


def delivery_date(rule: DeliveryDateRule, delivery: date, calendar: TradingCalendar) -> date:
    """Evaluate one declared date rule; the calendar must cover every consulted day."""
    first = _month(delivery.year, delivery.month, rule.month_offset)
    anchor = first if rule.day > 0 else _month(first.year, first.month, 1)
    day = anchor + timedelta(days=rule.day - (1 if rule.day > 0 else 0))
    if rule.weekday is not None:
        day += timedelta(days=(rule.weekday - day.weekday()) % 7)
    day += timedelta(days=rule.calendar_days)
    while not calendar.boundaries(day):
        day -= timedelta(days=1)
    direction = 1 if rule.sessions > 0 else -1
    for _ in range(abs(rule.sessions)):
        day += timedelta(days=direction)
        while not calendar.boundaries(day):
            day += timedelta(days=direction)
    return day


def _active(cycle: ContractCycle, day: date, calendar: TradingCalendar) -> str:
    for offset in range(13):
        delivery = _month(day.year, day.month, offset)
        if delivery.month not in cycle.active_months:
            continue
        roll = delivery_date(cycle.roll, delivery, calendar)
        limits = [delivery_date(cycle.last_trade, delivery, calendar)]
        if cycle.first_notice is not None:
            limits.append(delivery_date(cycle.first_notice, delivery, calendar))
        if roll >= min(limits):
            raise ContractMonthNotCovered(
                f"{delivery}: roll must be before last trade and first notice"
            )
        if day < roll:
            return f"{delivery.year:04d}-{delivery.month:02d}"
    raise ContractMonthNotCovered(f"{day}: no active delivery under {cycle.convention}")


def contract_months(
    declaration: ContractCalendar,
    calendar: TradingCalendar,
    contracts: Mapping[str, str],
    window: tuple[date, date],
) -> dict[date, dict[str, str]]:
    """Resolve every executable contract for the window, refusing incomplete coverage."""
    first, last = window
    if first > last or not declaration.covered_from <= first <= last <= declaration.covered_through:
        raise ContractMonthNotCovered(f"{first}..{last}: contract calendar does not cover window")
    missing = set(contracts.values()) - declaration.products.keys()
    if missing:
        raise ContractMonthNotCovered(f"undeclared executable products: {sorted(missing)}")
    try:
        return {
            day: {
                code: _active(declaration.products[series], day, calendar)
                for code, series in contracts.items()
            }
            for day in trading_days(calendar, first, last)
        }
    except CalendarNotCovered as error:
        raise ContractMonthNotCovered(
            f"executable month requires covered calendar: {error}"
        ) from error
