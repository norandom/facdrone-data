# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""The module's own gates pass on the module and fail on a planted violation (3.1 to 3.3).

Each gate runs as the CI runs it, as a separate process; a planted violation lives in a
temporary copy, never in the module tree.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tomllib
from importlib.util import find_spec
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1]
SCRIPTS = MODULE / "tools"
HEADER = "# SPDX-License-Identifier: Apache-2.0\n# Copyright 2026 Marius Ciepluch\n"


def _run(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str]:
    result = subprocess.run(  # noqa: S603 - the test's own fixed commands
        command, cwd=cwd, capture_output=True, text=True, check=False, env=env, timeout=240
    )
    return result.returncode, result.stdout + result.stderr


def _script(name: str, *args: str) -> tuple[int, str]:
    return _run([sys.executable, str(SCRIPTS / name), *args], cwd=MODULE)


def _layout(root: Path, data: dict[str, str], types: dict[str, str] | None = None) -> Path:
    """A minimal module directory: ``facdrone_data`` and ``types/facdrone_data_types``."""
    files = {f"facdrone_data/{name}": text for name, text in data.items()}
    files |= {f"types/facdrone_data_types/{n}": t for n, t in (types or {}).items()}
    files.setdefault("facdrone_data/__init__.py", HEADER)
    files.setdefault("types/facdrone_data_types/__init__.py", HEADER)
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    return root


# ── 3.1 licence headers ──────────────────────────────────────────────────────────────────


# The import-graph gates need the static tools (``grimp``, ``import-linter``), which the CI's
# static job installs; a venv with only the test extra runs every other gate.
needs_import_graph = pytest.mark.skipif(
    find_spec("grimp") is None or shutil.which("lint-imports") is None,
    reason="the import-graph tools are not installed here",
)


def test_every_module_file_carries_the_licence_header() -> None:
    status, output = _script("check_headers.py")
    assert status == 0, output


def test_header_gate_fails_on_a_file_without_the_header(tmp_path: Path) -> None:
    _layout(tmp_path, {"good.py": HEADER + "VALUE = 1\n", "bare.py": '"""No header."""\n'})
    status, output = _script("check_headers.py", "--root", str(tmp_path))
    assert status == 1
    assert "facdrone_data/bare.py: missing" in output
    assert "good.py" not in output


def test_header_gate_refuses_a_header_below_the_first_lines(tmp_path: Path) -> None:
    _layout(tmp_path, {"late.py": '"""Docstring first."""\n' + HEADER})
    status, output = _script("check_headers.py", "--root", str(tmp_path))
    assert status == 1 and "late.py" in output


def test_licence_files_name_apache_and_the_copyright_holder() -> None:
    assert "Apache License" in (MODULE / "LICENSE").read_text(encoding="utf-8")
    notice = (MODULE / "NOTICE").read_text(encoding="utf-8")
    assert "Copyright 2026 Marius Ciepluch" in notice


# ── 3.2 private literals: digests and generic patterns ───────────────────────────────


def test_private_literal_gate_passes_on_the_module() -> None:
    status, output = _script("check_private_literals.py")
    assert status == 0, output
    assert "0 hits" in output


def test_private_literal_gate_fails_on_a_planted_literal(tmp_path: Path) -> None:
    gates, tree = tmp_path / "gates", tmp_path / "tree"
    gates.mkdir()
    shutil.copy(MODULE / ".gates/literal-patterns.toml", gates)
    plain = tmp_path / "plain.txt"
    plain.write_text("# operator list\nPlanted-Host-Zeta\n", encoding="utf-8")
    status, output = _script(
        "check_private_literals.py", "--gates", str(gates), "--write-digests", str(plain)
    )
    assert status == 0, output
    # Assembled at run time, so the test's own source holds no address or path shape.
    address, home = ".".join(["10", "1", "2", "3"]), "/" + "home" + "/someone"
    _layout(tree, {"x.py": HEADER + f'HOST = "planted-host-zeta.example"  # {address}\n'})
    (tree / "notes.md").write_text(f"Data under {home}/data.\n", encoding="utf-8")
    status, output = _script(
        "check_private_literals.py", "--root", str(tree), "--gates", str(gates)
    )
    assert status == 1
    assert "facdrone_data/x.py:3: private-ipv4" in output
    assert "facdrone_data/x.py:3: private-literal" in output
    assert "notes.md:1: home-path" in output
    assert "zeta" not in output.lower() and address not in output


# ── 3.3 structure: no cycle, fan-out at most 15 ──────────────────────────────────────────


@needs_import_graph
def test_structure_gate_passes_on_the_module() -> None:
    status, output = _script("check_structure.py")
    assert status == 0, output


@needs_import_graph
def test_structure_gate_fails_on_an_import_cycle(tmp_path: Path) -> None:
    _layout(
        tmp_path,
        {"a.py": "import facdrone_data.b\n", "b.py": "import facdrone_data.a\n"},
    )
    status, output = _script("check_structure.py", "--root", str(tmp_path))
    assert status == 1
    assert "import cycle: facdrone_data.a <-> facdrone_data.b" in output


@needs_import_graph
def test_structure_gate_fails_on_a_fan_out_above_fifteen(tmp_path: Path) -> None:
    leaves = {f"leaf{n}.py": "" for n in range(16)}
    hub = "".join(f"import facdrone_data.leaf{n}\n" for n in range(16))
    _layout(tmp_path, {**leaves, "hub.py": hub})
    status, output = _script("check_structure.py", "--root", str(tmp_path))
    assert status == 1
    assert "fan-out 16 > 15: facdrone_data.hub" in output


# ── 3.3 size: growth needs a Size-reason trailer ─────────────────────────────────────────


@pytest.fixture
def grown_module(tmp_path: Path) -> Path:
    """A module whose baseline records less code than it holds."""
    _layout(tmp_path, {"x.py": HEADER + "A = 1\nB = 2\n"}, {"y.py": '"""Doc."""\nC = 3\n'})
    status, output = _script("size_gate.py", "--root", str(tmp_path), "--update", "--reason", "t")
    assert status == 0, output
    with (tmp_path / "facdrone_data/x.py").open("a", encoding="utf-8") as handle:
        handle.write("D = 4\n")
    return tmp_path


def test_size_gate_passes_on_the_module() -> None:
    status, output = _script("size_gate.py", "--message-file", os.devnull)
    assert status == 0, output


def test_size_gate_counts_code_without_headers_or_docstrings(grown_module: Path) -> None:
    baseline = (grown_module / ".gates/size-baseline.json").read_text(encoding="utf-8")
    assert '"code_lines": 3' in baseline and '"physical_lines": 10' in baseline


def test_size_gate_fails_on_growth_without_a_reason(grown_module: Path) -> None:
    message = grown_module / "message.txt"
    message.write_text("feat: grow\n\nSize-reason: only in the body\n\nCo-Authored-By: x\n")
    status, output = _script(
        "size_gate.py", "--root", str(grown_module), "--message-file", str(message)
    )
    assert status == 1
    assert "code_lines: 3 -> 4" in output and "physical_lines: 10 -> 11" in output


def test_size_gate_accepts_growth_with_a_size_reason_trailer(grown_module: Path) -> None:
    message = grown_module / "message.txt"
    message.write_text("feat: grow\n\nBody.\n\nSize-reason: one constant\nCo-Authored-By: x\n")
    status, output = _script(
        "size_gate.py", "--root", str(grown_module), "--message-file", str(message)
    )
    assert status == 0, output


# ── 3.3 import contracts ─────────────────────────────────────────────────────────────────


def _lint_imports(root: Path) -> tuple[int, str]:
    executable = str(Path(sys.executable).parent / "lint-imports")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(root), str(root / "types")])}
    command = [executable, "--config", str(MODULE / ".importlinter"), "--no-cache"]
    return _run(command, cwd=root, env=env)


@needs_import_graph
def test_import_contracts_hold_on_the_module() -> None:
    status, output = _lint_imports(MODULE)
    assert status == 0, output
    assert "0 broken" in output


@needs_import_graph
def test_import_contracts_break_on_a_planted_kernel_and_provider_import(tmp_path: Path) -> None:
    shutil.copytree(MODULE / "facdrone_data", tmp_path / "facdrone_data")
    shutil.copytree(MODULE / "types/facdrone_data_types", tmp_path / "types/facdrone_data_types")
    (tmp_path / "facdrone_data/planted.py").write_text(HEADER + "import facdrone_kernel\n")
    (tmp_path / "facdrone_data/bars/planted.py").write_text(
        HEADER + "import facdrone_data.providers.live_clients\n"
    )
    status, output = _lint_imports(tmp_path)
    assert status == 1
    assert "facdrone_data.planted -> facdrone_kernel" in output
    assert "facdrone_data.bars.planted -> facdrone_data.providers.live_clients" in output
    assert "2 broken" in output


# ── 3.3 credentials: .gitignore and the secret scan ──────────────────────────────────────

_CREDENTIAL_FILES = (
    ".env",
    ".env.local",
    "conf/local/credentials.yml",
    "credentials.yml",
    "ops.credentials.yaml",
    "facdrone-data.credentials.yml",
)
_SOURCE_FILES = ("facdrone_data/credentials.py", ".env.example", "examples/x.example.yml")


def _untracked(repository: Path) -> set[str]:
    git = ["git", "-c", "core.excludesFile=/dev/null", "status", "--porcelain", "-uall"]
    status, output = _run(git, cwd=repository)
    assert status == 0, output
    return {line[3:] for line in output.splitlines()}


def _plant_repository(root: Path, with_ignore: bool) -> Path:
    status, output = _run(["git", "init", "-q", str(root)], cwd=root.parent)
    assert status == 0, output
    if with_ignore:
        shutil.copy(MODULE / ".gitignore", root / ".gitignore")
    for name in (*_CREDENTIAL_FILES, *_SOURCE_FILES):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("planted\n", encoding="utf-8")
    return root


def test_gitignore_keeps_planted_credential_files_out(tmp_path: Path) -> None:
    untracked = _untracked(_plant_repository(tmp_path / "repo", with_ignore=True))
    assert untracked.isdisjoint(_CREDENTIAL_FILES)
    assert set(_SOURCE_FILES) <= untracked


def test_without_the_gitignore_the_planted_credentials_would_be_tracked(tmp_path: Path) -> None:
    untracked = _untracked(_plant_repository(tmp_path / "repo", with_ignore=False))
    assert set(_CREDENTIAL_FILES) <= untracked


def _leaks(text: str) -> list[str]:
    """The module's own gitleaks rules over ``text``, with its allowlist (Python ``re``)."""
    config = tomllib.loads((MODULE / ".gitleaks.toml").read_text(encoding="utf-8"))
    allowed = [re.compile(pattern) for pattern in config["allowlist"]["regexes"]]
    hits: list[str] = []
    for rule in config["rules"]:
        for match in re.finditer(rule["regex"], text):
            secret = match.group(rule.get("secretGroup", 0))
            if not any(pattern.search(secret) for pattern in allowed):
                hits.append(rule["id"])
    return hits


def _planted_secrets() -> str:
    # Assembled at run time, so the test's own source holds no credential-shaped literal.
    key, password = "Q7w" * 10 + "Zz", "hunter" + "2" * 6
    return (
        f"FMP_API_KEY={key}\n"
        f"fred_api_key: {'0a1b2c3d' * 4}\n"
        f"url = 'postgresql+psycopg://operator:{password}@db.internal:5432/market'\n"
    )


def test_secret_rules_flag_planted_keys_and_a_dsn_password() -> None:
    # The FMP rule's generic ``api_key`` also fires on the FRED line; each rule must fire.
    assert set(_leaks(_planted_secrets())) == {
        "fmp-api-key",
        "fred-api-key",
        "postgres-dsn-password",
    }
    assert _leaks("raise RuntimeError('postgresql://user:secret@host/db')") == []


def test_secret_rules_find_nothing_in_the_module_tree() -> None:
    suffixes = {".py", ".toml", ".yml", ".yaml", ".md", ".txt", ".json", ".cfg", ""}
    skipped = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
    hits = {
        str(path.relative_to(MODULE)): _leaks(path.read_text(encoding="utf-8", errors="replace"))
        for path in MODULE.rglob("*")
        if path.is_file() and path.suffix in suffixes and skipped.isdisjoint(path.parts)
    }
    assert {name: found for name, found in hits.items() if found} == {}


@pytest.mark.skipif(shutil.which("gitleaks") is None, reason="gitleaks is not installed here")
def test_gitleaks_fails_on_a_planted_secret(tmp_path: Path) -> None:
    (tmp_path / "settings.env.txt").write_text(_planted_secrets(), encoding="utf-8")
    config = str(MODULE / ".gitleaks.toml")
    status, output = _run(["gitleaks", "dir", str(tmp_path), "--config", config], cwd=tmp_path)
    assert status == 1, output
    status, output = _run(["gitleaks", "dir", str(MODULE), "--config", config], cwd=MODULE)
    assert status == 0, output
