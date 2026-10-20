# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Credential sources, the database address and redaction (R4.1, R4.2, R4.3, R4.5).

Provider keys come from exactly one source per run, chosen by the composition root:
:class:`EnvironmentCredentials` reads ``FMP_API_KEY`` and ``FRED_API_KEY``;
:class:`FileCredentials` reads one YAML file (keys ``fmp_api_key``, ``fred_api_key``) whose
path the caller gives, by ``--credentials`` or ``FACDRONE_DATA_CREDENTIALS_FILE``. Nothing
merges the two. The module holds no database address: :func:`database_url` takes it from
``--dsn`` or ``FACDRONE_DATA_DSN`` and refuses otherwise.

:func:`redact` removes key-shaped query parameters and every configured key value from a
string; :class:`RedactingFilter` applies it to every log record, its arguments and its
exception text.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NamedTuple, Protocol

import yaml

from facdrone_data.acquisition.families.credentials import KEY_VARIABLES, ProviderCredentials
from facdrone_data_types.symbols import Provider

DSN_VARIABLE: Final = "FACDRONE_DATA_DSN"
CREDENTIALS_FILE_VARIABLE: Final = "FACDRONE_DATA_CREDENTIALS_FILE"
_MASK: Final = "***"
_KEY_PARAMETER = re.compile(r"(?i)\b(apikey|api_key|token|key)=([^&\s'\"]+)")


class CredentialSource(Protocol):
    """Where a run's provider keys come from."""

    def credentials(self) -> ProviderCredentials: ...


class MissingCredential(NamedTuple):
    """A selected provider needs a key the run does not have; names the variable, never a value."""

    provider: Provider
    variable: str


class MissingDatabaseError(RuntimeError):
    """No database address was given; the message names the variable and the option."""


@dataclass(frozen=True)
class EnvironmentCredentials:
    """Keys from ``FMP_API_KEY`` and ``FRED_API_KEY`` (unset or empty means not configured)."""

    environ: Mapping[str, str]

    def credentials(self) -> ProviderCredentials:
        fmp = self.environ.get(KEY_VARIABLES["fmp_api_key"]) or None
        fred = self.environ.get(KEY_VARIABLES["fred_api_key"]) or None
        return ProviderCredentials(fmp_api_key=fmp, fred_api_key=fred)


@dataclass(frozen=True)
class FileCredentials:
    """Keys from one YAML file given by path; an absent file configures no key."""

    path: Path

    def credentials(self) -> ProviderCredentials:
        if not self.path.exists():
            return ProviderCredentials(fmp_api_key=None, fred_api_key=None)
        raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"{self.path}: expected a mapping of credential names")
        return ProviderCredentials(
            fmp_api_key=_optional_text(raw.get("fmp_api_key")),
            fred_api_key=_optional_text(raw.get("fred_api_key")),
        )


def credential_source(argument: Path | None, environ: Mapping[str, str]) -> CredentialSource:
    """The one source a run uses: the file named by argument or variable, else the environment."""
    named = argument or (
        Path(environ[CREDENTIALS_FILE_VARIABLE]) if environ.get(CREDENTIALS_FILE_VARIABLE) else None
    )
    return FileCredentials(named) if named is not None else EnvironmentCredentials(environ)


def database_url(argument: str | None, environ: Mapping[str, str]) -> str:
    """The database address from ``--dsn`` or ``FACDRONE_DATA_DSN``; no default exists."""
    url = argument or environ.get(DSN_VARIABLE)
    if not url:
        raise MissingDatabaseError(f"no database given: pass --dsn or set {DSN_VARIABLE}")
    return url


def missing_credential(
    provider: Provider, credentials: ProviderCredentials
) -> MissingCredential | None:
    """The variable a keyed provider lacks, or ``None`` when it is keyless or configured."""
    variable = credentials.missing_variable(provider)
    return None if variable is None else MissingCredential(provider, variable)


def redact(text: str, secrets: Collection[str]) -> str:
    """``text`` with key-shaped query values and every given secret replaced by a mask."""
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        text = text.replace(secret, _MASK)
    return _KEY_PARAMETER.sub(lambda match: f"{match.group(1)}={_MASK}", text)


class RedactingFilter(logging.Filter):
    """Rewrites every record so no configured key reaches a handler (R4.3)."""

    def __init__(self, secrets: Collection[str]) -> None:
        super().__init__()
        self._secrets = tuple(secrets)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage(), self._secrets)
        record.args = None
        if record.exc_info is not None:
            formatter = logging.Formatter()
            record.exc_text = redact(formatter.formatException(record.exc_info), self._secrets)
            record.exc_info = None
        return True


def install_redaction(credentials: ProviderCredentials, logger: logging.Logger) -> RedactingFilter:
    """Attach a redacting filter for these credentials to ``logger``'s handlers and itself."""
    secrets = tuple(value for value in credentials.values() if value)
    redacting = RedactingFilter(secrets)
    logger.addFilter(redacting)
    for handler in logger.handlers:
        handler.addFilter(redacting)
    return redacting


def process_environment() -> Mapping[str, str]:
    """The process environment, read once at a composition root."""
    return dict(os.environ)


def _optional_text(value: object) -> str | None:
    return None if value is None or value == "" else str(value)
