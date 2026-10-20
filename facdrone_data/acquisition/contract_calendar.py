# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Validate the operator's executable-delivery declaration at the YAML boundary."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields
from pathlib import Path
from typing import cast

import yaml
from pydantic import TypeAdapter

from facdrone_data.contract_months import ContractCalendar, ContractCycle, DeliveryDateRule
from facdrone_data.contract_months import contract_months as derive_contract_months

__all__ = ["derive_contract_months", "load_contract_calendar"]


def _fields(
    value: object, model: type[ContractCalendar] | type[ContractCycle] | type[DeliveryDateRule]
) -> Mapping[str, object]:
    """Reject misspelled declarations instead of letting dataclass validation ignore them."""
    if not isinstance(value, Mapping):
        raise ValueError(f"{model.__name__}: declaration must be a mapping")
    unknown = value.keys() - {field.name for field in fields(model)}
    if unknown:
        raise ValueError(f"{model.__name__}: unknown fields {unknown!r}")
    return cast(Mapping[str, object], value)


def _rule_fields(value: object) -> None:
    """Require exact integers; coercing booleans or fractional offsets changes expiry."""
    rule = _fields(value, DeliveryDateRule)
    for name, offset in rule.items():
        if name == "weekday" and offset is None:
            continue
        if type(offset) is not int:
            raise ValueError(f"{name}: date rule requires an integer")


def _cycle_fields(value: object) -> None:
    cycle = _fields(value, ContractCycle)
    for name in ("listed_months", "active_months"):
        months = cycle.get(name)
        if not isinstance(months, list) or any(type(month) is not int for month in months):
            raise ValueError(f"{name}: month cycle requires integers")
    if type(cycle.get("verify")) is not bool:
        raise ValueError("verify: an explicit boolean is required")
    for name in ("last_trade", "roll", "first_notice"):
        rule = cycle.get(name)
        if rule is not None:
            _rule_fields(rule)


def _validate_rule(rule: DeliveryDateRule) -> None:
    """Keep offsets within one year/month of the declared anchor, with bounded scans."""
    bounded = (
        ("month_offset", rule.month_offset, -12, 12),
        ("calendar_days", rule.calendar_days, -31, 31),
        ("sessions", rule.sessions, -31, 31),
    )
    for name, value, lower, upper in bounded:
        if not lower <= value <= upper:
            raise ValueError(f"{name}: date offset must be in {lower}..{upper}")
    if rule.day == 0 or abs(rule.day) > 28:
        raise ValueError("date anchor must be a nonzero day in -28..28")
    if rule.weekday is not None and rule.weekday not in range(7):
        raise ValueError("weekday: must be in 0..6")


def _validate_months(series: str, months: tuple[int, ...]) -> None:
    if not months or len(set(months)) != len(months):
        raise ValueError(f"{series}: month cycle must be nonempty and unique")
    if any(month not in range(1, 13) for month in months):
        raise ValueError(f"{series}: month must be in 1..12")


def _validate_cycle(series: str, cycle: ContractCycle) -> None:
    for months in (cycle.listed_months, cycle.active_months):
        _validate_months(series, months)
    if not set(cycle.active_months) <= set(cycle.listed_months):
        raise ValueError(f"{series}: active months must belong to the listed cycle")
    if not cycle.source.startswith("https://www.cmegroup.com/"):
        raise ValueError(f"{series}: CME source required")
    if not all(text.strip() for text in (series, cycle.listing, cycle.convention)):
        raise ValueError(f"{series}: product, listing and simulation convention required")
    for rule in (cycle.last_trade, cycle.roll, cycle.first_notice):
        if rule is not None:
            _validate_rule(rule)


def load_contract_calendar(path: Path) -> ContractCalendar:
    """Read declared rules only; no provider query or price-derived expiry inference."""
    document = _fields(yaml.safe_load(path.read_text()), ContractCalendar)
    products = document.get("products")
    if not isinstance(products, Mapping):
        raise ValueError("products: declaration must be a mapping")
    for cycle in products.values():
        _cycle_fields(cycle)
    declaration = TypeAdapter(ContractCalendar).validate_python(document)
    if declaration.covered_from > declaration.covered_through or not declaration.products:
        raise ValueError("contract calendar requires products and ordered coverage")
    for series, cycle in declaration.products.items():
        _validate_cycle(series, cycle)
    return declaration
