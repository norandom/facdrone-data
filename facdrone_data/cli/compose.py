# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The module's composition root: configuration, credentials, database and live clients.

The only module that brings the configuration loader and the live provider clients
together. A run's options name the configuration file, the database and the credential
file; each falls back to its documented environment variable, and none has a default.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from facdrone_data.acquisition.families.gate import quality_gate
from facdrone_data.config import load_config
from facdrone_data.credentials import (
    credential_source,
    database_url,
    install_redaction,
)
from facdrone_data.quality.ensemble import EnsembleValidator
from facdrone_data.runner import (
    DataRootDeps,
    compose_download_runner,
    panel_coverage_ends,
    resume_point,
    select_families,
)
from facdrone_data.universe import DeclaredUniverse

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import date, datetime

    from sqlalchemy import Engine

    from facdrone_data.acquisition.families.refresher import DownloadReport
    from facdrone_data.runner import DownloadRequest
    from facdrone_data.universe import UniverseSource

CONFIG_VARIABLE = "FACDRONE_DATA_CONFIG"


class ConfigMissingError(RuntimeError):
    """No configuration file was named."""


@dataclass(frozen=True)
class DataOptions:
    """The run-level options of ``facdrone-data`` (``None``: use the environment variable)."""

    config: Path | None = None
    dsn: str | None = None
    credentials: Path | None = None


@dataclass(frozen=True)
class DataBackend:
    """What the commands run against: the runner, the panel resume point and the database."""

    download: Callable[[DownloadRequest], DownloadReport]
    resume: Callable[[], date | None]
    engine: Callable[[], Engine]
    default_source: str


def universe_resume(
    universe: UniverseSource, engine: Engine, clock: Callable[[], datetime]
) -> Callable[[], date | None]:
    """The resume point over the universe's acquisition symbols, read at call time."""

    def _resume() -> date | None:
        required = universe.requirement(engine, clock()).acquisition_symbols
        return resume_point(panel_coverage_ends(engine, required), required)

    return _resume


def live_backend(
    options: DataOptions, environ: Mapping[str, str], clock: Callable[[], datetime]
) -> DataBackend:
    """Bind the stand-alone backend: declared universe, chosen credentials, live clients."""
    from sqlalchemy import create_engine

    from facdrone_data.providers.live_clients import live_clients

    named = options.config or (
        Path(environ[CONFIG_VARIABLE]) if environ.get(CONFIG_VARIABLE) else None
    )
    if named is None:
        raise ConfigMissingError(f"no configuration given: pass --config or set {CONFIG_VARIABLE}")
    config = load_config(named)
    credentials = credential_source(options.credentials, environ).credentials()
    install_redaction(credentials, logging.getLogger())
    engine = create_engine(database_url(options.dsn, environ))
    universe = DeclaredUniverse(config)
    deps = DataRootDeps(
        engine=engine,
        credentials=credentials,
        requirement=lambda: universe.requirement(engine, clock()),
        clients=live_clients(credentials),
        validator=EnsembleValidator(),
    )
    return DataBackend(
        download=compose_download_runner(deps, family_selector=select_families, gate=quality_gate),
        resume=universe_resume(universe, engine, clock),
        engine=lambda: engine,
        default_source=config.default_source,
    )
