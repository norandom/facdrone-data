# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""A request URL carrying a key reaches no log record, exception text or evidence row (R4.3)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import pytest

from facdrone_data.acquisition.families.credentials import ProviderCredentials
from facdrone_data.credentials import RedactingFilter, install_redaction, redact
from facdrone_data.providers.fmp_endpoints import FmpRequestError, fmp_get, fmp_records

_KEY = "fmpkey-0123456789abcdef"  # noqa: S105 — synthetic test fixture, not a real key
_URL = (
    f"https://financialmodelingprep.com/stable/historical-price-eod/full?symbol=GCUSD&apikey={_KEY}"
)


class _HttpError(Exception):
    """What a transport raises: its text names the full request URL, as requests' does."""


def _failing_transport(url: str, params: Mapping[str, str]) -> Any:
    raise _HttpError(f"401 Client Error: Unauthorized for url: {url}?apikey={params['apikey']}")


def test_redact_removes_key_parameters_and_configured_values() -> None:
    assert _KEY not in redact(_URL, ())
    assert _KEY not in redact(f"key {_KEY} in prose", (_KEY,))
    assert "symbol=GCUSD" in redact(_URL, ())
    assert redact("token=abc&x=1", ()) == "token=***&x=1"


def test_the_provider_boundary_logs_no_key(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert fmp_get(_failing_transport, "/historical-price-eod/full", api_key=_KEY) == []
    assert caplog.records, "the failed request is reported"
    for record in caplog.records:
        assert _KEY not in record.getMessage()


def test_the_typed_request_error_names_no_key() -> None:
    """The strict wrapper's error is redacted and chains nothing whose text names the key."""
    with pytest.raises(FmpRequestError) as caught:
        fmp_records(_failing_transport, "/historical-price-eod/full", api_key=_KEY)
    assert _KEY not in str(caught.value) and "401" in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_the_logging_filter_redacts_messages_arguments_and_exception_text() -> None:
    logger = logging.getLogger("facdrone_data.test_redaction")
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collect()
    logger.addHandler(handler)
    try:
        install_redaction(ProviderCredentials(_KEY, None), logger)
        logger.warning("fetching %s", _URL)
        try:
            raise _HttpError(f"failed for {_URL}")
        except _HttpError:
            logger.exception("request failed")
    finally:
        logger.removeHandler(handler)
        for item in list(logger.filters):
            logger.removeFilter(item)
    rendered = [logging.Formatter().format(record) for record in records]
    assert len(rendered) == 2
    for text in rendered:
        assert _KEY not in text
    assert "***" in rendered[1]


def test_an_evidence_row_stores_the_redacted_uri() -> None:
    row = {"request_uri": redact(_URL, (_KEY,)), "status": "unauthorized"}
    assert _KEY not in str(row)
    assert isinstance(RedactingFilter((_KEY,)).filter(logging.makeLogRecord({"msg": _URL})), bool)
