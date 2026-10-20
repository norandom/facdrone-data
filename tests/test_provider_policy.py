# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The provider policy table (facdrone-data-manager 3.4; R6.4).

One row per ``Provider`` member; every adapter's requests carry its row's timeout; and the
policy columns of ``docs/providers.md`` are rendered from the rows, so the document cannot
claim a retry the code does not make (the retry schedules through the adapters are in
``test_provider_retries.py``). No request leaves the machine: every transport is a fake.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock, Mock, patch

import pytest

from facdrone_data.providers import etf_source
from facdrone_data.providers.cftc_source import live_cftc
from facdrone_data.providers.dukascopy_source import live_dukascopy
from facdrone_data.providers.factor_returns_source import US_FF3, live_aqr, live_kenfrench_archive
from facdrone_data.providers.fmp_endpoints import live_http_json
from facdrone_data.providers.fred_source import live_fred
from facdrone_data.providers.kenfrench_source import live_kenfrench
from facdrone_data.providers.oecd_source import live_oecd
from facdrone_data.providers.policy import (
    POLICY,
    THROTTLE_STATUSES,
    TIMEOUT_SECONDS,
    ProviderPolicy,
    Retry,
)
from facdrone_data.providers.yfinance_source import live_yfinance
from facdrone_data_types.symbols import Provider

PROVIDERS_DOC = Path(__file__).resolve().parents[1] / "docs" / "providers.md"
_HOUR = datetime(2026, 10, 7, 10)
_WINDOW = (date(2025, 1, 6), date(2025, 1, 10))


# ── the table ────────────────────────────────────────────────────────────────
def test_every_provider_member_has_exactly_one_policy_row() -> None:
    assert set(POLICY) == set(Provider)
    assert len(POLICY) == len(Provider)


def test_the_rows_keep_todays_timeouts() -> None:
    timeouts = {member: row.timeout_seconds for member, row in POLICY.items()}
    assert timeouts == {
        Provider.FMP: 30,
        Provider.ETF: 30,
        Provider.OECD: 30,
        Provider.KEN_FRENCH: 30,
        Provider.DUKASCOPY: 30,
        Provider.AQR: 120,
        Provider.YFINANCE: None,
        Provider.FRED: None,
        Provider.CFTC: None,
    }
    assert TIMEOUT_SECONDS == 30


def test_the_dukascopy_row_is_the_schedule_the_adapter_had() -> None:
    row = POLICY[Provider.DUKASCOPY]
    assert row.throttle == Retry((1.0, 2.0, 4.0))
    assert row.fault == Retry((1.0, 2.0))
    assert row.other == Retry((0.0, 0.0))
    assert row.requests_per_minute is None


def test_only_fmp_and_dukascopy_retry_and_nothing_throttles_by_count() -> None:
    retrying = {
        member
        for member, row in POLICY.items()
        if row.throttle.waits or row.fault.waits or row.other.waits
    }
    assert retrying == {Provider.FMP, Provider.ETF, Provider.DUKASCOPY}
    assert POLICY[Provider.ETF] is POLICY[Provider.FMP]
    assert POLICY[Provider.FMP].throttle == Retry((1.0, 2.0, 4.0))
    assert POLICY[Provider.FMP].fault == POLICY[Provider.FMP].other == Retry()
    assert all(row.requests_per_minute is None for row in POLICY.values())
    assert frozenset({429, 503}) == THROTTLE_STATUSES


def test_a_negative_wait_is_refused() -> None:
    with pytest.raises(ValueError, match="never negative"):
        Retry((1.0, -1.0))


# ── each adapter's requests carry the row's timeout ───────────────────────────
@pytest.fixture
def urlopen() -> Iterator[MagicMock]:
    with patch("urllib.request.urlopen") as opened:
        opened.return_value.__enter__.return_value.read.return_value = b""
        yield opened


@pytest.mark.parametrize(
    ("member", "call"),
    [
        (Provider.OECD, lambda: live_oecd()()),
        (Provider.KEN_FRENCH, lambda: live_kenfrench()()),
        (Provider.KEN_FRENCH, lambda: live_kenfrench_archive(US_FF3)()),
        (Provider.AQR, lambda: live_aqr()("QMJ")),
    ],
)
def test_urllib_transports_carry_their_rows_timeout(
    urlopen: MagicMock, member: Provider, call: Any
) -> None:
    call()
    assert urlopen.call_args.kwargs == {"timeout": POLICY[member].timeout_seconds}


def test_the_dukascopy_hour_fetch_carries_its_rows_timeout() -> None:
    with patch("requests.Session") as session:
        session.return_value.get.return_value = Mock(status_code=404)
        live_dukascopy(sleep=lambda _s: None)("EURUSD", _HOUR)
    assert session.return_value.get.call_args.kwargs["timeout"] == 30


def test_the_fmp_transport_carries_its_rows_timeout() -> None:
    with patch("requests.Session") as session:
        session.return_value.get.return_value.json.return_value = []
        live_http_json()("https://example.test/x", {})
    assert session.return_value.get.call_args.kwargs["timeout"] == 30


def test_the_etf_transport_is_built_from_the_etf_row() -> None:
    with patch("facdrone_data.providers.fmp_endpoints.live_http_json") as built:
        etf_source.live_etf()
    assert built.call_args.kwargs == {"policy": POLICY[Provider.ETF]}


def _sdk(monkeypatch: pytest.MonkeyPatch, name: str, **attributes: object) -> None:
    monkeypatch.setitem(sys.modules, name, types.SimpleNamespace(**attributes))


def test_library_backed_transports_pass_no_timeout_of_their_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rows with ``timeout_seconds=None`` are true: yfinance, fredapi and cot_reports make
    their own requests and the module hands them no timeout."""
    seen: list[dict[str, Any]] = []

    def record(*_args: object, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return None

    fred = Mock()
    fred.return_value.get_series.side_effect = record
    _sdk(monkeypatch, "yfinance", download=record)
    _sdk(monkeypatch, "fredapi", Fred=fred)
    _sdk(monkeypatch, "cot_reports", cot_year=record)
    live_yfinance()("AAA", *_WINDOW)
    live_fred("synthetic-fred-key")("SERIES", *_WINDOW)
    live_cftc()(2025)
    assert len(seen) == 3
    assert all("timeout" not in kwargs for kwargs in seen)
    assert "timeout" not in fred.call_args.kwargs
    assert {POLICY[m].timeout_seconds for m in (Provider.YFINANCE, Provider.FRED)} == {None}
    assert POLICY[Provider.CFTC].timeout_seconds is None


# ── docs/providers.md prints the rows ────────────────────────────────────────
def _waits(waits: tuple[float, ...]) -> str:
    text = [f"{wait:g}" for wait in waits]
    return ", ".join(text[:-1]) + f" and {text[-1]}" if len(text) > 1 else text[0]


def _retry(label: str, rule: Retry) -> str:
    count = len(rule.waits)
    if not count:
        return f"{label}: no retry"
    retries = f"{count} {'retry' if count == 1 else 'retries'}"
    if not any(rule.waits):
        return f"{label}: {retries}, no wait"
    return f"{label}: {retries} after {_waits(rule.waits)} s"


def render_policy(row: ProviderPolicy) -> str:
    """The "Rate limit handled today" cell a row prints as."""
    timeout = (
        "Timeout: the client library's own (the module passes none)"
        if row.timeout_seconds is None
        else f"Timeout {row.timeout_seconds:g} s per request"
    )
    ceiling = (
        "no request-count ceiling"
        if row.requests_per_minute is None
        else f"at most {row.requests_per_minute} requests per minute"
    )
    if not (row.throttle.waits or row.fault.waits or row.other.waits):
        return f"{timeout}; no retry on any outcome; {ceiling}"
    return "; ".join(
        (
            timeout,
            _retry("throttle (HTTP 429/503)", row.throttle),
            _retry("transient fault", row.fault),
            _retry("other status", row.other),
            ceiling,
        )
    )


def render_publication(row: ProviderPolicy) -> str:
    """The "Publication delay stated" cell a row prints as."""
    delay = row.publication.delay
    if delay is None:
        return f"no fixed delay: {row.publication.stated}"
    days = delay.days
    return f"{days} {'day' if days == 1 else 'days'}: {row.publication.stated}"


def _doc_rows() -> dict[Provider, dict[str, str]]:
    lines = PROVIDERS_DOC.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("| Provider |"))
    header = [cell.strip() for cell in lines[start].strip("|").split("|")]
    rows: dict[Provider, dict[str, str]] = {}
    for line in lines[start + 2 :]:
        if not line.startswith("|"):
            break
        cells = dict(zip(header, (c.strip() for c in line.strip("|").split("|")), strict=True))
        members = [m for m in Provider if f"(`{m.name}`)" in cells["Provider"]]
        assert len(members) == 1, cells["Provider"]
        assert members[0] not in rows, f"two rows for {members[0].name}"
        rows[members[0]] = cells
    return rows


def test_the_document_has_one_row_per_provider_member() -> None:
    assert set(_doc_rows()) == set(Provider)


def _expected(member: Provider, render: Any) -> str:
    shared = member is not Provider.FMP and POLICY[member] is POLICY[Provider.FMP]
    return "as FMP" if shared else cast("str", render(POLICY[member]))


@pytest.mark.parametrize("member", list(Provider), ids=lambda m: m.name)
def test_the_documented_rate_limit_is_the_row(member: Provider) -> None:
    cell = _doc_rows()[member]["Rate limit handled today"]
    assert cell == _expected(member, render_policy)


@pytest.mark.parametrize("member", list(Provider), ids=lambda m: m.name)
def test_the_documented_publication_delay_is_the_row(member: Provider) -> None:
    cell = _doc_rows()[member]["Publication delay stated"]
    assert cell == _expected(member, render_publication)
