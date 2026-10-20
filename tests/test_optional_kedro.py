# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The command line runs without Kedro, the optional ``[kedro]`` extra (R3.6).

A child interpreter that cannot import ``kedro`` (or ``kedro_datasets``) imports every
acquisition module the command line touches and renders ``facdrone-data --help``.
"""

from __future__ import annotations

import subprocess
import sys

_PROBE = """
import sys
for name in ("kedro", "kedro_datasets"):
    sys.modules[name] = None  # any import of it now raises ImportError
from typer.testing import CliRunner
import facdrone_data.acquisition.families.registry as registry
from facdrone_data.cli.app import compose_data_cli
registry.download_families()
app = compose_data_cli(lambda options: None, lambda: None)
result = CliRunner().invoke(app, ["--help"])
assert result.exit_code == 0, result.output
for command in ("download", "refetch", "update", "quality", "status"):
    assert command in result.output, command
try:
    import facdrone_data.acquisition as acquisition
    acquisition.create_pipeline()
except ImportError:
    print("pipeline-needs-kedro")
"""


def test_the_command_line_imports_and_runs_without_kedro() -> None:
    done = subprocess.run(  # noqa: S603 — fixed argv: this interpreter and a literal probe
        [sys.executable, "-c", _PROBE], capture_output=True, text=True, check=False, timeout=120
    )
    assert done.returncode == 0, done.stderr
    assert "pipeline-needs-kedro" in done.stdout  # only the pipeline wrapper asks for it
