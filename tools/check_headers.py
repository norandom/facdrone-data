# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Licence header gate (facdrone-data-manager 5.3).

Every ``.py`` file of the module starts with two comment lines::

    # SPDX-License-Identifier: Apache-2.0
    # Copyright <year> Marius Ciepluch

A shebang line may precede them. The gate lists every file without the header and exits 1.

Usage: ``python tools/check_headers.py [--root DIR]`` (default: the module directory).
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterator
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
SPDX_LINE = "# SPDX-License-Identifier: Apache-2.0"
COPYRIGHT = re.compile(r"^# Copyright \d{4}(-\d{4})? Marius Ciepluch$")
# Generated or tool-owned directories: never the module's own source.
SKIPPED_PARTS = frozenset(
    {"__pycache__", ".venv", "build", "dist", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
)


def python_files(root: Path) -> Iterator[Path]:
    """Every ``.py`` file below ``root`` outside generated and tool directories."""
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root).parts
        if SKIPPED_PARTS.intersection(relative) or any(p.endswith(".egg-info") for p in relative):
            continue
        yield path


def has_header(path: Path) -> bool:
    """True when the file's first lines (after an optional shebang) are the two header lines."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if lines and lines[0].startswith("#!"):
        lines = lines[1:]
    return len(lines) >= 2 and lines[0] == SPDX_LINE and COPYRIGHT.match(lines[1]) is not None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail on a .py file without the licence header.")
    parser.add_argument("--root", type=Path, default=MODULE_ROOT, help="directory to scan")
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()
    files = list(python_files(root))
    missing = [path for path in files if not has_header(path)]
    for path in missing:
        print(f"{path.relative_to(root)}: missing the SPDX and copyright header", file=sys.stderr)
    if missing:
        print(f"header gate FAILED: {len(missing)} of {len(files)} files", file=sys.stderr)
        return 1
    print(f"header gate: OK ({len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
