# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Task 5.1: incremental acquisition node (Req 1.2, 1.3, 1.4, 2.1, 2.2).

Resolve canonical symbols through the registry, DERIVE coverage from the stored
panel (per-symbol MIN/MAX + interior gap scan, never a side index), fetch only
the missing sub-ranges, and schema-validate each fetched frame. Fully offline:
a FakeMarketDataClient records every download so we can assert zero downloads
over a covered range and gap-only fetches otherwise.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from facdrone_data.acquisition.acquisition import (
    AcquireDeps,
    acquire,
    derive_coverage,
)
from facdrone_data.acquisition.schemas import DATASET_SCHEMAS, SchemaViolationError, validate_frame
from facdrone_data.acquisition.symbol_registry import (
    InMemorySymbolRegistry,
    SymbolConfigError,
    SymbolMapping,
)
from facdrone_data.testing import EURUSD
from facdrone_data.testing.fakes import FakeMarketDataClient, synthetic_daily_rows
from facdrone_data_types.enums import AssetClass
from facdrone_data_types.ports import FetchRequest
from facdrone_data_types.symbols import CanonicalSymbol, Provider

GBPUSD = CanonicalSymbol(canonical_id="GBPUSD", asset_class=AssetClass.FOREX, profit_ccy="USD")
UNMAPPED = CanonicalSymbol(canonical_id="AUDUSD", asset_class=AssetClass.FOREX, profit_ccy="USD")


def _registry() -> InMemorySymbolRegistry:
    return InMemorySymbolRegistry(
        [
            SymbolMapping(EURUSD, {Provider.YFINANCE: "EURUSD=X", Provider.DUKASCOPY: "EURUSD"}),
            SymbolMapping(GBPUSD, {Provider.YFINANCE: "GBPUSD=X", Provider.DUKASCOPY: "GBPUSD"}),
        ]
    )


def _setup() -> tuple[FakeMarketDataClient, AcquireDeps]:
    client = FakeMarketDataClient(provider=Provider.YFINANCE)
    return client, AcquireDeps(client=client, registry=_registry())


def _panel(canonical_id: str, start: date, end: date) -> pd.DataFrame:
    # Build the stored canonical panel keyed by canonical_id (the source of truth).
    rows = synthetic_daily_rows(canonical_id, start, end)
    rows["symbol"] = canonical_id
    return rows


def _two_run_panel(canonical_id: str) -> pd.DataFrame:
    first = synthetic_daily_rows(canonical_id, date(2024, 1, 1), date(2024, 1, 3))
    second = synthetic_daily_rows(canonical_id, date(2024, 1, 7), date(2024, 1, 11))
    panel = pd.concat([first, second], ignore_index=True)
    panel["symbol"] = canonical_id
    return panel


def test_fully_present_range_issues_zero_downloads() -> None:
    client, deps = _setup()
    panel = _panel("EURUSD", date(2024, 1, 1), date(2024, 1, 10))
    result = acquire((EURUSD,), "daily_prices", (date(2024, 1, 1), date(2024, 1, 10)), deps, panel)
    assert client.calls == []  # zero provider calls (R2.1)
    assert result.frames == ()
    assert result.audit.skipped == (("EURUSD=X", date(2024, 1, 1), date(2024, 1, 10)),)


def test_partially_present_range_fetches_only_the_gap() -> None:
    client, deps = _setup()
    panel = _panel("EURUSD", date(2024, 1, 1), date(2024, 1, 4))
    result = acquire((EURUSD,), "daily_prices", (date(2024, 1, 1), date(2024, 1, 10)), deps, panel)
    assert client.calls == [
        FetchRequest("EURUSD=X", "daily_prices", date(2024, 1, 5), date(2024, 1, 10))
    ]
    assert client.fetched_days == 6
    assert result.audit.fetched == (("EURUSD=X", date(2024, 1, 5), date(2024, 1, 10)),)
    assert len(result.frames) == 1
    # the fetched frame is re-keyed to the canonical id, not the provider symbol
    assert set(result.frames[0]["symbol"]) == {"EURUSD"}


def test_interior_hole_is_detected_and_fetched() -> None:
    # Closes the MAX(date) blind spot: a hole between two present runs is fetched.
    client, deps = _setup()
    panel = _two_run_panel("EURUSD")  # present [d1,d3] and [d7,d11]; missing d4..d6
    result = acquire((EURUSD,), "daily_prices", (date(2024, 1, 1), date(2024, 1, 11)), deps, panel)
    assert client.calls == [
        FetchRequest("EURUSD=X", "daily_prices", date(2024, 1, 4), date(2024, 1, 6))
    ]
    assert result.audit.fetched == (("EURUSD=X", date(2024, 1, 4), date(2024, 1, 6)),)


def test_empty_panel_fetches_the_whole_range() -> None:
    client, deps = _setup()
    empty = synthetic_daily_rows("EURUSD", date(2024, 1, 10), date(2024, 1, 1))  # empty
    result = acquire((EURUSD,), "daily_prices", (date(2024, 1, 1), date(2024, 1, 5)), deps, empty)
    assert client.calls == [
        FetchRequest("EURUSD=X", "daily_prices", date(2024, 1, 1), date(2024, 1, 5))
    ]
    assert len(result.frames) == 1


def test_schema_violating_frame_is_rejected_loudly() -> None:
    class _DroppingClient:
        provider = Provider.YFINANCE

        def fetch(self, req: FetchRequest) -> pd.DataFrame:
            return synthetic_daily_rows(req.provider_symbol, req.start, req.end).drop(
                columns=["knowledge_date"]
            )

        def datasets(self) -> tuple[str, ...]:
            return ("daily_prices",)

    deps = AcquireDeps(client=_DroppingClient(), registry=_registry())
    empty = synthetic_daily_rows("EURUSD", date(2024, 1, 10), date(2024, 1, 1))
    with pytest.raises(SchemaViolationError) as exc:
        acquire((EURUSD,), "daily_prices", (date(2024, 1, 1), date(2024, 1, 3)), deps, empty)
    assert "daily_prices" in str(exc.value) and "knowledge_date" in str(exc.value)


def test_unmapped_symbol_fails_loud_before_any_fetch() -> None:
    client, deps = _setup()  # registry does not know AUDUSD
    empty = synthetic_daily_rows("AUDUSD", date(2024, 1, 10), date(2024, 1, 1))
    with pytest.raises(SymbolConfigError):
        acquire((UNMAPPED,), "daily_prices", (date(2024, 1, 1), date(2024, 1, 5)), deps, empty)
    assert client.calls == []  # fail-loud before downloading


def test_unknown_dataset_is_rejected() -> None:
    _client, deps = _setup()
    empty = synthetic_daily_rows("EURUSD", date(2024, 1, 10), date(2024, 1, 1))
    with pytest.raises(SchemaViolationError):
        acquire((EURUSD,), "no_such_dataset", (date(2024, 1, 1), date(2024, 1, 5)), deps, empty)


def test_derive_coverage_emits_multiple_contiguous_ranges() -> None:
    cov = derive_coverage(_two_run_panel("EURUSD"), "EURUSD")
    assert cov.present == (
        (date(2024, 1, 1), date(2024, 1, 3)),
        (date(2024, 1, 7), date(2024, 1, 11)),
    )


def test_derive_coverage_on_empty_panel_is_empty() -> None:
    empty = synthetic_daily_rows("EURUSD", date(2024, 1, 10), date(2024, 1, 1))
    assert derive_coverage(empty, "EURUSD").present == ()


def test_validate_frame_passes_a_conformant_daily_frame() -> None:
    frame = synthetic_daily_rows("EURUSD=X", date(2024, 1, 1), date(2024, 1, 5))
    out = validate_frame(frame, DATASET_SCHEMAS["daily_prices"])
    assert out is frame  # no copy — the validator is a pass-through gate
    pd.testing.assert_frame_equal(out, frame)


def test_validate_frame_rejects_a_non_date_event_date() -> None:
    frame = synthetic_daily_rows("EURUSD=X", date(2024, 1, 1), date(2024, 1, 3))
    frame["event_date"] = "not-a-date"
    with pytest.raises(SchemaViolationError, match="event_date"):
        validate_frame(frame, DATASET_SCHEMAS["daily_prices"])


def test_validate_frame_rejects_a_non_date_knowledge_date() -> None:
    # knowledge_date is the PIT-filter field (R3.6) — a malformed value must
    # fail loud at the boundary, not mis-filter downstream.
    frame = synthetic_daily_rows("EURUSD=X", date(2024, 1, 1), date(2024, 1, 3))
    frame["knowledge_date"] = "not-a-date"
    with pytest.raises(SchemaViolationError, match="knowledge_date"):
        validate_frame(frame, DATASET_SCHEMAS["daily_prices"])


def test_validate_frame_rejects_an_all_null_knowledge_date() -> None:
    # pd.to_datetime coerces None to NaT silently; the boundary must still reject
    # an all-null PIT field (R3.6), not only a non-date string.
    frame = synthetic_daily_rows("EURUSD=X", date(2024, 1, 1), date(2024, 1, 3))
    frame["knowledge_date"] = None
    with pytest.raises(SchemaViolationError, match="knowledge_date"):
        validate_frame(frame, DATASET_SCHEMAS["daily_prices"])


def _concat_runs(canonical_id: str, runs: list[tuple[int, int]]) -> pd.DataFrame:
    parts = [
        synthetic_daily_rows(canonical_id, date(2024, 1, a), date(2024, 1, b)) for a, b in runs
    ]
    panel = pd.concat(parts, ignore_index=True)
    panel["symbol"] = canonical_id
    return panel


def test_weekend_is_not_a_gap_so_a_covered_weekday_panel_refetches_nothing() -> None:
    # R2.1 regression guard: Fri 2024-01-05 + Mon 2024-01-08, weekend closed.
    client, deps = _setup()
    panel = _concat_runs("EURUSD", [(5, 5), (8, 8)])
    result = acquire((EURUSD,), "daily_prices", (date(2024, 1, 5), date(2024, 1, 8)), deps, panel)
    assert client.calls == []  # the closed weekend is not an interior gap
    assert result.frames == ()


def test_derive_coverage_treats_a_weekend_as_contiguous() -> None:
    panel = _concat_runs("EURUSD", [(5, 5), (8, 8)])
    assert derive_coverage(panel, "EURUSD").present == ((date(2024, 1, 5), date(2024, 1, 8)),)


def test_derive_coverage_without_a_symbol_column_is_empty() -> None:
    panel = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=3)})
    assert derive_coverage(panel, "EURUSD").present == ()


def test_derive_coverage_filters_by_symbol_in_a_mixed_panel() -> None:
    eur = synthetic_daily_rows("EURUSD", date(2024, 1, 1), date(2024, 1, 3))
    gbp = synthetic_daily_rows("GBPUSD", date(2024, 1, 1), date(2024, 1, 10))
    panel = pd.concat([eur, gbp], ignore_index=True)
    assert derive_coverage(panel, "EURUSD").present == ((date(2024, 1, 1), date(2024, 1, 3)),)
    assert derive_coverage(panel, "GBPUSD").present == ((date(2024, 1, 1), date(2024, 1, 10)),)


def test_multi_symbol_acquire_fetches_only_the_symbol_with_a_gap() -> None:
    client, deps = _setup()
    eur = _panel("EURUSD", date(2024, 1, 1), date(2024, 1, 10))  # fully present
    gbp = synthetic_daily_rows("GBPUSD", date(2024, 1, 1), date(2024, 1, 4))  # partial
    panel = pd.concat([eur, gbp], ignore_index=True)
    result = acquire(
        (EURUSD, GBPUSD), "daily_prices", (date(2024, 1, 1), date(2024, 1, 10)), deps, panel
    )
    assert client.calls == [
        FetchRequest("GBPUSD=X", "daily_prices", date(2024, 1, 5), date(2024, 1, 10))
    ]
    assert ("EURUSD=X", date(2024, 1, 1), date(2024, 1, 10)) in result.audit.skipped
    assert ("GBPUSD=X", date(2024, 1, 5), date(2024, 1, 10)) in result.audit.fetched
    assert len(result.frames) == 1


def test_audit_records_present_fetched_and_skipped_tiling_the_request() -> None:
    client, deps = _setup()
    panel = _panel("EURUSD", date(2024, 1, 1), date(2024, 1, 4))  # covers d1-d4
    result = acquire((EURUSD,), "daily_prices", (date(2024, 1, 1), date(2024, 1, 10)), deps, panel)
    # fetched ∪ skipped tile the requested window; present is the panel coverage.
    assert result.audit.fetched == (("EURUSD=X", date(2024, 1, 5), date(2024, 1, 10)),)
    assert result.audit.skipped == (("EURUSD=X", date(2024, 1, 1), date(2024, 1, 4)),)
    assert result.audit.present == (("EURUSD=X", date(2024, 1, 1), date(2024, 1, 4)),)


def test_multiple_interior_gaps_are_each_fetched_in_order() -> None:
    client, deps = _setup()
    # present 1/1-1/2, 1/5-1/6, 1/9-1/10 → missing business days in 1/3-1/4 and 1/7-1/8
    panel = _concat_runs("EURUSD", [(1, 2), (5, 6), (9, 10)])
    result = acquire((EURUSD,), "daily_prices", (date(2024, 1, 1), date(2024, 1, 10)), deps, panel)
    assert client.calls == [
        FetchRequest("EURUSD=X", "daily_prices", date(2024, 1, 3), date(2024, 1, 4)),
        FetchRequest("EURUSD=X", "daily_prices", date(2024, 1, 7), date(2024, 1, 8)),
    ]
    assert len(result.frames) == 2
