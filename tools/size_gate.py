# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Size gate (facdrone-data-manager 7.3): the module does not grow without a stated reason.

Two numbers over the production code of ``facdrone_data`` and ``facdrone_data_types``:

``code_lines``      lines that are not blank, not a comment and not inside a docstring;
``physical_lines``  every line of every ``.py`` file.

They are compared with ``.gates/size-baseline.json``. A change that grows either number fails
unless the commit message carries a ``Size-reason:`` trailer, for example::

    Size-reason: the CFTC disaggregated report adds one normaliser (+120 code lines)

The message is the head commit's (``git log -1``) or the file given by ``--message-file``. A
shrinking number never fails. ``--update --reason TEXT`` records the measured values with the
reason, so a new baseline is a reviewed diff.

Usage: ``python tools/size_gate.py [--root DIR] [--message-file FILE]``.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("facdrone_data", "types/facdrone_data_types")
BASELINE = Path(".gates/size-baseline.json")
ROWS = ("code_lines", "physical_lines")
TRAILER = re.compile(r"^Size-reason:[ \t]*\S", re.MULTILINE)


def _docstring_lines(tree: ast.Module) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        first = node.body[0] if node.body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return lines


def _code_lines(source: str) -> int:
    docstrings = _docstring_lines(ast.parse(source))
    return sum(
        1
        for number, line in enumerate(source.splitlines(), 1)
        if line.strip() and not line.lstrip().startswith("#") and number not in docstrings
    )


def measure(root: Path) -> dict[str, int]:
    """``code_lines`` and ``physical_lines`` over the two packages below ``root``."""
    files = sorted(
        path
        for directory in SOURCE_DIRS
        for path in (root / directory).rglob("*.py")
        if "__pycache__" not in path.parts
    )
    sources = [path.read_text(encoding="utf-8") for path in files]
    return {
        "code_lines": sum(_code_lines(source) for source in sources),
        "physical_lines": sum(len(source.splitlines()) for source in sources),
    }


def _commit_message(root: Path, message_file: Path | None) -> str:
    if message_file is not None:
        return message_file.read_text(encoding="utf-8")
    result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        ["git", "log", "-1", "--format=%B"],  # noqa: S607 - git from the caller's PATH
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else ""


def has_size_reason(message: str) -> bool:
    """True when the message's trailer block (its last paragraph) holds ``Size-reason:``."""
    paragraphs = [block for block in re.split(r"\n\s*\n", message.strip()) if block.strip()]
    return len(paragraphs) > 1 and TRAILER.search(paragraphs[-1]) is not None


def _grown(current: dict[str, int], baseline: dict[str, int]) -> list[str]:
    grown: list[str] = []
    for row in ROWS:
        before = baseline.get(row)
        print(f"size gate: {row:15} {before} -> {current[row]}")
        if before is None or current[row] > before:
            grown.append(f"{row}: {before} -> {current[row]}")
    return grown


def check(root: Path, message_file: Path | None) -> int:
    """0 when nothing grew or the commit message states why; 1 otherwise."""
    current = measure(root)
    path = root / BASELINE
    baseline = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    grown = _grown(current, baseline)
    if not grown:
        print("size gate: OK, no growth")
        return 0
    if has_size_reason(_commit_message(root, message_file)):
        print("size gate: OK, growth explained by the Size-reason trailer: " + ", ".join(grown))
        return 0
    print("size gate FAILED, the module grew: " + ", ".join(grown), file=sys.stderr)
    print(
        "Remove what the change replaced, or state why in the commit message's trailer:\n"
        "  Size-reason: <what the growth buys>",
        file=sys.stderr,
    )
    return 1


def update(root: Path, reason: str) -> int:
    """Write the measured values and the reason as the new baseline."""
    path = root / BASELINE
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {**measure(root), "reason": reason}
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"size gate: baseline written {record}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail on growth without a Size-reason trailer.")
    parser.add_argument("--root", type=Path, default=MODULE_ROOT, help="the module directory")
    parser.add_argument("--message-file", type=Path, help="commit message to read the trailer")
    parser.add_argument("--update", action="store_true", help="record the measured values")
    parser.add_argument("--reason", help="required with --update: why the baseline moves")
    args = parser.parse_args(argv)
    root: Path = args.root.resolve()
    if args.update:
        if not args.reason:
            print("size gate: --update needs --reason", file=sys.stderr)
            return 2
        return update(root, args.reason)
    return check(root, args.message_file)


if __name__ == "__main__":
    raise SystemExit(main())
