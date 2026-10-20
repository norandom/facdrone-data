# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The module's own configuration file: what to acquire and where the identities live (R3.5).

The families stay the code-reviewed registry (``download_families()``); this file names the
instruments a stand-alone run acquires, the identity files that map them to provider symbols,
the currency conversions target and the default family selector. It is read from the path the
caller gives (``--config`` or ``FACDRONE_DATA_CONFIG``), never from a consumer's repository,
and identity files are resolved relative to the configuration file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import yaml
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from facdrone_data_types.enums import AssetClass

Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
Token = Annotated[str, StringConstraints(pattern=r"^\S+$")]


class ConfigError(ValueError):
    """The configuration file is unreadable or invalid; the message names the file and field."""


class InstrumentEntry(BaseModel):
    """One instrument the run acquires, in canonical (Yahoo-style) spelling."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    canonical: Token
    asset_class: AssetClass
    profit_ccy: Currency


class DataManagerConfig(BaseModel):
    """The stand-alone universe: instruments, identity files and the conversion target."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity_files: tuple[Path, ...]
    instruments: tuple[InstrumentEntry, ...]
    global_inputs: tuple[Token, ...] = ()
    recorded_series: tuple[Token, ...] = ()
    conversion_currency: Currency
    default_source: Token = "-dukascopy"


def load_config(path: Path) -> DataManagerConfig:
    """Read and validate one configuration file; identity paths become absolute.

    Fails before any connection or request, naming the file and the offending field.
    """
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    try:
        config = DataManagerConfig.model_validate(raw)
    except ValidationError as exc:
        fields = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['msg']}"
            for error in exc.errors()
        )
        raise ConfigError(f"{path}: {fields}") from exc
    base = path.resolve().parent
    return config.model_copy(
        update={"identity_files": tuple(base / item for item in config.identity_files)}
    )
