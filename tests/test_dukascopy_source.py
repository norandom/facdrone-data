# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Dukascopy transport: .bi5 parse + the order-flow client over an injected fetch."""

from __future__ import annotations

import struct
from datetime import date, datetime
from unittest.mock import Mock, call, patch

import pytest

from facdrone_data.providers.dukascopy_source import (
    _HEADERS,
    DukascopyOrderFlowClient,
    _parse_bi5,
    live_dukascopy,
)
from facdrone_data.providers.policy import TIMEOUT_SECONDS
from facdrone_data_types.ports import FetchRequest
from facdrone_data_types.symbols import Provider


def _bi5(records: list[tuple[int, int, int, float, float]]) -> bytes:
    """Pack (ms, ask_raw, bid_raw, ask_vol, bid_vol) records as Dukascopy .bi5."""
    return b"".join(struct.pack(">IIIff", *r) for r in records)


def test_parse_bi5_decodes_records_with_the_divisor() -> None:
    hour = datetime(2025, 6, 2, 14)
    raw = _bi5([(0, 110050, 110030, 1.0, 2.0), (1000, 110060, 110040, 3.0, 1.0)])
    ticks = _parse_bi5(raw, hour, divisor=100000)
    assert len(ticks) == 2
    assert ticks[0]["ask"] == 1.1005 and ticks[0]["bid"] == 1.1003
    assert ticks[0]["spread"] == round(1.1005 - 1.1003, 6)
    assert ticks[0]["ask_vol"] == 1.0 and ticks[0]["bid_vol"] == 2.0
    assert ticks[0]["timestamp"] == hour  # ms=0
    assert ticks[1]["timestamp"] == datetime(2025, 6, 2, 14, 0, 1)  # ms=1000


def test_client_aggregates_weekdays_and_skips_weekends() -> None:
    # one .bi5 hour of ticks; served for hour 0 of any weekday, empty otherwise
    ticks_hour0 = _bi5([(0, 110050, 110030, 1.0, 2.0), (500, 110070, 110050, 2.0, 1.0)])

    def fake_fetch(duka_sym: str, hour_start: datetime) -> bytes | None:
        return ticks_hour0 if hour_start.hour == 0 else None

    client = DukascopyOrderFlowClient(fake_fetch)
    # Mon 2025-06-02 .. Sun 2025-06-08 → 5 weekday rows, weekend skipped
    out = client.fetch(
        FetchRequest("EURUSD", "duka_order_flow", date(2025, 6, 2), date(2025, 6, 8))
    )
    assert client.provider is Provider.DUKASCOPY
    assert len(out) == 5  # Mon–Fri only
    assert set(out["date"]) == {date(2025, 6, d) for d in (2, 3, 4, 5, 6)}
    assert (out["n_ticks"] == 2).all()  # 2 ticks/day
    assert "symbol" in out.columns and set(out["symbol"]) == {"EURUSD"}


def test_client_returns_empty_for_a_non_order_flow_dataset() -> None:
    client = DukascopyOrderFlowClient(lambda s, h: None)
    out = client.fetch(FetchRequest("EURUSD", "daily_prices", date(2025, 6, 2), date(2025, 6, 2)))
    assert out.empty


def test_live_factory_builds_without_network_and_returns_a_callable() -> None:
    transport = live_dukascopy()  # must not touch the network at build time
    assert callable(transport)


def test_live_fetch_hour_retries_then_swallows_a_persistent_fault() -> None:
    """A transient HTTP/decompress fault on one hour must be retried with backoff and then give up
    (return None), NOT propagate — otherwise one bad hour takes down every valid hour in the same
    symbol/window (the client loop has no per-hour guard, by design)."""
    import requests

    sleeps: list[float] = []
    with patch("requests.Session") as session:
        session.return_value.get.side_effect = requests.ConnectionError("network down")
        fetch_hour = live_dukascopy(sleep=sleeps.append)
        assert fetch_hour("XAUUSD", datetime(2024, 1, 9, 10)) is None
        assert session.return_value.get.call_count == 3
    assert sleeps == [1, 2]


@pytest.mark.parametrize("status", [503, 429])
def test_throttle_retries_with_bounded_backoff_then_gives_up(status: int) -> None:
    sleeps: list[float] = []
    with patch("requests.Session") as session:
        session.return_value.get.return_value = Mock(status_code=status)
        fetch_hour = live_dukascopy(sleep=sleeps.append)
        assert fetch_hour("EURUSD", datetime(2026, 10, 7, 10)) is None
        assert session.return_value.get.call_count == 4
    assert sleeps == [1, 2, 4]


@pytest.mark.parametrize("status, attempts", [(404, 1), (403, 3), (500, 3), (204, 3)])
def test_other_statuses_keep_their_existing_attempts(status: int, attempts: int) -> None:
    sleeps: list[float] = []
    with patch("requests.Session") as session:
        session.return_value.get.return_value = Mock(status_code=status)
        assert live_dukascopy(sleep=sleeps.append)("EURUSD", datetime(2026, 10, 7, 10)) is None
        assert session.return_value.get.call_count == attempts
    assert sleeps == []


@pytest.mark.parametrize("content", [b"", b"short", b"invalid bi5" * 10])
def test_empty_and_corrupt_payloads_still_give_up_per_hour(content: bytes) -> None:
    sleeps: list[float] = []
    with patch("requests.Session") as session:
        session.return_value.get.return_value = Mock(status_code=200, content=content)
        assert live_dukascopy(sleep=sleeps.append)("EURUSD", datetime(2026, 10, 7, 10)) is None
        assert session.return_value.get.call_count == 3
    assert sleeps == ([1, 2] if len(content) > 50 else [])


@pytest.mark.parametrize("status", [503, 429])
def test_throttle_recovers_and_reuses_session_across_hours(status: int) -> None:
    import lzma

    raw = _bi5([(0, 110050, 110030, 1.0, 2.0)])
    payload = Mock(status_code=200, content=lzma.compress(raw))
    sleeps: list[float] = []
    with patch("requests.Session") as session:
        session.return_value.get.side_effect = [Mock(status_code=status), payload, payload]
        fetch_hour = live_dukascopy(sleep=sleeps.append)
        assert fetch_hour("EURUSD", datetime(2026, 10, 7, 10)) == raw
        assert fetch_hour("EURUSD", datetime(2026, 10, 7, 11)) == raw
        session.assert_called_once_with()
        session.return_value.get.assert_has_calls(
            [
                call(
                    f"https://www.dukascopy.com/datafeed/EURUSD/2026/09/07/{hour}h_ticks.bi5",
                    headers=_HEADERS,
                    timeout=TIMEOUT_SECONDS,
                )
                for hour in (10, 10, 11)
            ]
        )
    assert sleeps == [1]


def test_friday_to_monday_requests_only_open_feed_hours() -> None:
    requested: list[datetime] = []
    raw = _bi5([(0, 110050, 110030, 1.0, 2.0)])

    def fetch_hour(symbol: str, hour_start: datetime) -> bytes:
        requested.append(hour_start)
        return raw

    out = DukascopyOrderFlowClient(fetch_hour).fetch(
        FetchRequest("EURUSD", "duka_order_flow", date(2026, 10, 2), date(2026, 10, 5))
    )
    assert requested == [
        *(datetime(2026, 10, 2, hour) for hour in range(22)),
        datetime(2026, 10, 4, 22),
        datetime(2026, 10, 4, 23),
        *(datetime(2026, 10, 5, hour) for hour in range(24)),
    ]
    assert len(requested) == 48
    assert list(out["date"]) == [date(2026, 10, 2), date(2026, 10, 4), date(2026, 10, 5)]
    assert list(out["n_ticks"]) == [22, 2, 24]


@pytest.mark.parametrize(
    "hour",
    [datetime(2026, 10, 2, 22), datetime(2026, 10, 3, 12), datetime(2026, 10, 4, 21)],
)
def test_live_fetch_does_not_request_a_closed_hour(hour: datetime) -> None:
    sleeps: list[float] = []
    with patch("requests.Session") as session:
        assert live_dukascopy(sleep=sleeps.append)("EURUSD", hour) is None
        session.return_value.get.assert_not_called()
    assert sleeps == []
