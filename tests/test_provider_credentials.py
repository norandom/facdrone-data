# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Provider credentials come from one named source, are never logged, and fail fast naming
the source, the key and its variable (R8.3, R9.2, R9.4; facdrone-data-manager R4.1-R4.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

import facdrone_data
from facdrone_data.acquisition.families.credentials import (
    MissingCredentialError,
    ProviderCredentials,
)
from facdrone_data.credentials import (
    EnvironmentCredentials,
    FileCredentials,
    MissingCredential,
    MissingDatabaseError,
    credential_source,
    database_url,
    missing_credential,
)
from facdrone_data_types.symbols import Provider

_SECRET = "sk-FMP-do-not-leak-123"  # noqa: S105 — synthetic test fixture, not a real key
_FRED_SECRET = "fred-key-do-not-leak-456"  # noqa: S105 — synthetic test fixture, not a real key


def test_repr_and_str_redact_keys() -> None:
    creds = ProviderCredentials(fmp_api_key=_SECRET, fred_api_key=_FRED_SECRET)
    for rendering in (repr(creds), str(creds), f"{creds}", f"{creds!r}"):
        assert _SECRET not in rendering
        assert _FRED_SECRET not in rendering
        assert "redacted" in rendering.lower()


def test_require_returns_the_key_for_a_keyed_source() -> None:
    creds = ProviderCredentials(fmp_api_key=_SECRET, fred_api_key=_FRED_SECRET)
    assert creds.require(Provider.FMP) == _SECRET
    assert creds.require(Provider.FRED) == _FRED_SECRET
    # ETF prices come from FMP endpoints, so they consume the FMP key.
    assert creds.require(Provider.ETF) == _SECRET


def test_require_fails_fast_naming_source_key_and_variable_when_absent() -> None:
    creds = ProviderCredentials(fmp_api_key=None, fred_api_key=None)
    with pytest.raises(MissingCredentialError) as exc:
        creds.require(Provider.FMP)
    message = str(exc.value)
    assert "fmp" in message.lower()  # names the source
    assert "fmp_api_key" in message  # names the missing key
    assert "FMP_API_KEY" in message  # names the variable that would carry it
    assert _SECRET not in message


def test_require_rejects_a_keyless_source() -> None:
    creds = ProviderCredentials(fmp_api_key=_SECRET, fred_api_key=_FRED_SECRET)
    for keyless in (Provider.YFINANCE, Provider.CFTC, Provider.KEN_FRENCH, Provider.OECD):
        with pytest.raises(ValueError, match="keyless"):
            creds.require(keyless)


def test_missing_credential_names_the_variable_only_for_keyed_providers() -> None:
    creds = ProviderCredentials(fmp_api_key=None, fred_api_key=_FRED_SECRET)
    assert missing_credential(Provider.FMP, creds) == MissingCredential(Provider.FMP, "FMP_API_KEY")
    assert missing_credential(Provider.ETF, creds) == MissingCredential(Provider.ETF, "FMP_API_KEY")
    assert missing_credential(Provider.FRED, creds) is None
    assert missing_credential(Provider.YFINANCE, creds) is None


def test_environment_credentials_read_the_documented_variables() -> None:
    creds = EnvironmentCredentials({"FMP_API_KEY": _SECRET, "FRED_API_KEY": ""}).credentials()
    assert creds.require(Provider.FMP) == _SECRET
    assert creds.fred_api_key is None  # empty means not configured


def test_file_credentials_read_one_explicit_file(tmp_path: Path) -> None:
    path = tmp_path / "keys.yml"
    path.write_text(f"fmp_api_key: {_SECRET}\nfred_api_key: {_FRED_SECRET}\n", encoding="utf-8")
    creds = FileCredentials(path).credentials()
    assert creds.require(Provider.FMP) == _SECRET
    assert creds.require(Provider.FRED) == _FRED_SECRET
    absent = FileCredentials(tmp_path / "absent.yml").credentials()
    assert absent.values() == (None, None)


def test_the_file_when_named_is_the_only_source(tmp_path: Path) -> None:
    path = tmp_path / "keys.yml"
    path.write_text("fred_api_key: from-file\n", encoding="utf-8")
    environ = {"FMP_API_KEY": _SECRET, "FACDRONE_DATA_CREDENTIALS_FILE": str(path)}
    assert credential_source(None, environ) == FileCredentials(path)
    assert credential_source(None, environ).credentials().fmp_api_key is None  # never merged
    assert credential_source(tmp_path / "other.yml", environ) == FileCredentials(
        tmp_path / "other.yml"
    )
    assert credential_source(None, {"FMP_API_KEY": _SECRET}) == EnvironmentCredentials(
        {"FMP_API_KEY": _SECRET}
    )


def test_the_database_address_comes_only_from_the_argument_or_its_variable() -> None:
    assert database_url("postgresql://a/b", {"FACDRONE_DATA_DSN": "postgresql://c/d"}) == (
        "postgresql://a/b"
    )
    assert database_url(None, {"FACDRONE_DATA_DSN": "postgresql://c/d"}) == "postgresql://c/d"
    with pytest.raises(MissingDatabaseError, match="FACDRONE_DATA_DSN"):
        database_url(None, {})


def test_the_module_carries_no_database_address_and_no_credential_file_location() -> None:
    package = Path(facdrone_data.__file__).parent
    # The test plugin names the socket of the throwaway server it starts itself; that is the
    # one database address the package may build, and it is never a default for a user.
    started = package / "testing" / "pg_server.py"
    for path in sorted(package.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if path != started:
            assert "postgresql://" not in text and "postgresql+psycopg://" not in text, path
        assert "conf/local" not in text, path


def test_file_credentials_ignore_other_entries(tmp_path: Path) -> None:
    path = tmp_path / "keys.yml"
    path.write_text("other: value\n", encoding="utf-8")
    assert FileCredentials(path).credentials().values() == (None, None)
