# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The module's configuration file (facdrone-data-manager 1.4; R3.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from facdrone_data.acquisition.instrument_table import InstrumentTable
from facdrone_data.config import ConfigError, load_config

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "facdrone-data.example.yml"


def test_the_example_config_loads_and_its_identity_files_resolve() -> None:
    config = load_config(EXAMPLE)
    assert config.conversion_currency == "USD"
    assert all(path.is_absolute() and path.exists() for path in config.identity_files)
    table = InstrumentTable(config.identity_files)
    assert {entry.canonical for entry in config.instruments} <= {
        symbol.canonical_id for symbol in table.by_kind("cfd")
    }


@pytest.mark.parametrize(
    ("text", "field"),
    [
        ("identity_files: []\ninstruments: []\nconversion_currency: usd\n", "conversion_currency"),
        ("identity_files: []\ninstruments: []\n", "conversion_currency"),
        (
            "identity_files: []\nconversion_currency: USD\n"
            "instruments: [{canonical: X, asset_class: nope, profit_ccy: USD}]\n",
            "instruments.0.asset_class",
        ),
        ("identity_files: []\ninstruments: []\nconversion_currency: USD\nextra: 1\n", "extra"),
    ],
)
def test_a_malformed_config_fails_naming_the_field(tmp_path: Path, text: str, field: str) -> None:
    path = tmp_path / "bad.yml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=field) as raised:
        load_config(path)
    assert str(path) in str(raised.value)


def test_an_unreadable_config_names_the_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="absent.yml"):
        load_config(tmp_path / "absent.yml")
