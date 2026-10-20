# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Private literal gate (facdrone-data-manager 5.1).

Scans every text file of the module (code, documents, configuration, examples; ``.gates``
itself excluded) two ways:

* the generic patterns of ``.gates/literal-patterns.toml`` (private addresses, home paths,
  e-mail addresses, account-number shapes, account and credential key names);
* the salted SHA-256 digests of ``.gates/literal-digests.txt``. Each line is lower-cased and
  cut into words (split on non-alphanumerics) and path-like runs (with their ``/``, ``:``,
  ``@`` and ``.`` pieces and ``/`` prefixes); every token is hashed with the file's salt.

A hit prints ``file:line: class`` and never the matched text. The plain literal list stays
with the operator; ``--write-digests LIST`` turns it into a digest file with a fresh salt.
Texts on the patterns file's ``[[allow]]`` list are blanked before matching.

Usage: ``python tools/check_private_literals.py [--root DIR] [--gates DIR]``.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import secrets
import sys
import tomllib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
SALT_PREFIX = "# salt: "
SKIPPED_PARTS = frozenset(
    {".gates", ".git", "__pycache__", ".venv", "build", "dist", ".mypy_cache", ".pytest_cache"}
    | {".ruff_cache"}
)
_WORD_SPLIT = re.compile(r"[^a-z0-9]+")
_RUN = re.compile(r"/?[a-z0-9~][a-z0-9._:/@~-]*")
_PIECE_SPLIT = re.compile(r"[/:@]")


@dataclass(frozen=True)
class Rules:
    """The compiled patterns, the allowed texts and the salted digest set."""

    patterns: tuple[tuple[str, re.Pattern[str]], ...]
    allowed: tuple[str, ...]
    salt: str
    digests: frozenset[str]


def digest(salt: str, token: str) -> str:
    return hashlib.sha256((salt + token).encode("utf-8")).hexdigest()


def tokens(line: str) -> set[str]:
    """Words and path-like runs of the lower-cased line, with their pieces and prefixes."""
    lowered = line.lower()
    found = {word for word in _WORD_SPLIT.split(lowered) if word}
    for run in _RUN.findall(lowered):
        run = run.rstrip("._:/@-")
        found.add(run)
        for piece in _PIECE_SPLIT.split(run):
            found.update({piece, *piece.split(".")} - {""})
        found.update(run[:index] for index in range(1, len(run)) if run[index] == "/")
    return found


def load_rules(gates: Path) -> Rules:
    config = tomllib.loads((gates / "literal-patterns.toml").read_text(encoding="utf-8"))
    patterns = tuple((p["class"], re.compile(p["regex"])) for p in config["pattern"])
    allowed = tuple(entry["text"] for entry in config.get("allow", []))
    lines = (gates / "literal-digests.txt").read_text(encoding="utf-8").splitlines()
    salt = next(line[len(SALT_PREFIX) :] for line in lines if line.startswith(SALT_PREFIX))
    digests = frozenset(line.strip() for line in lines if line.strip() and line[0] != "#")
    return Rules(patterns, allowed, salt.strip(), digests)


def text_files(root: Path) -> Iterator[tuple[Path, str]]:
    """Every UTF-8 text file below ``root`` outside the gate and tool directories."""
    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        if not path.is_file() or SKIPPED_PARTS.intersection(parts):
            continue
        if any(part.endswith(".egg-info") for part in parts):
            continue
        try:
            yield path, path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue


def line_hits(line: str, rules: Rules) -> list[str]:
    """The classes the line matches; an allowed text is blanked first."""
    for text in rules.allowed:
        line = line.replace(text, " ")
    hits = [name for name, pattern in rules.patterns if pattern.search(line)]
    if any(digest(rules.salt, token) in rules.digests for token in tokens(line)):
        hits.append("private-literal")
    return hits


def scan(root: Path, rules: Rules) -> list[str]:
    findings: list[str] = []
    for path, text in text_files(root):
        for number, line in enumerate(text.splitlines(), start=1):
            for name in line_hits(line, rules):
                findings.append(f"{path.relative_to(root)}:{number}: {name}")
    return findings


def write_digests(plain: Path, target: Path) -> int:
    """Write the digest file for the operator's plain list under a fresh salt."""
    lines = plain.read_text(encoding="utf-8").splitlines()
    values = sorted({line.strip().lower() for line in lines if line.strip()[:1] not in ("", "#")})
    salt = secrets.token_hex(16)
    header = [
        "# Salted SHA-256 digests of the private literals (facdrone-data-manager 5.1).",
        "# digest = sha256(salt + token), token lower-cased; the plain list stays with the",
        "# operator. Rebuild: python tools/check_private_literals.py --write-digests LIST",
        f"{SALT_PREFIX}{salt}",
    ]
    body = sorted(digest(salt, value) for value in values)
    target.write_text("\n".join(header + body) + "\n", encoding="utf-8")
    print(f"wrote {len(body)} digests to {target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail on a private literal in the module.")
    parser.add_argument("--root", type=Path, default=MODULE_ROOT, help="directory to scan")
    parser.add_argument("--gates", type=Path, default=MODULE_ROOT / ".gates")
    parser.add_argument("--write-digests", type=Path, metavar="LIST", help="plain list file")
    args = parser.parse_args(argv)
    if args.write_digests is not None:
        return write_digests(args.write_digests, args.gates / "literal-digests.txt")
    findings = scan(args.root.resolve(), load_rules(args.gates))
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        print(f"private literal gate FAILED: {len(findings)} hits", file=sys.stderr)
        return 1
    print("private literal gate: OK (0 hits)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
