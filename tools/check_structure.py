# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Marius Ciepluch
"""Structural gate (facdrone-data-manager 7.1): no import cycle, internal fan-out at most 15.

The grimp graph of ``facdrone_data`` and ``facdrone_data_types`` is read from source (nothing
is imported). The gate fails on

- an import cycle between modules of the two packages (a strongly connected component of
  more than one module, or a module importing itself), and
- a module that directly imports more than ``FAN_OUT_LIMIT`` distinct modules of the two
  packages (the main repository's god-file limit is 16, so the module keeps one below it).

The graph is the runtime one: imports under ``TYPE_CHECKING`` are left out, as the main
repository's god-file count leaves them out (they are the standard way to name a type without
a runtime edge, and counting them would flag the cycle-breaking idiom itself).

Usage: ``python tools/check_structure.py [--root DIR]``; ``DIR`` holds ``facdrone_data``
and ``types/facdrone_data_types`` (default: the module directory).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

MODULE_ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("facdrone_data", "facdrone_data_types")
FAN_OUT_LIMIT = 15

Graph = Mapping[str, frozenset[str]]


def internal_graph(root: Path) -> dict[str, frozenset[str]]:
    """Each module of the two packages mapped to the modules of the two packages it imports."""
    # The roots go first on the path, so a checkout is measured rather than an installed copy.
    sys.path[:0] = [str(root), str(root / "types")]
    import grimp

    graph = grimp.build_graph(*PACKAGES, cache_dir=None, exclude_type_checking_imports=True)
    modules = frozenset(graph.modules)
    return {
        module: frozenset(graph.find_modules_directly_imported_by(module) & modules)
        for module in sorted(modules)
    }


def _strong_components(graph: Graph) -> list[list[str]]:
    """Tarjan's algorithm, iterative, over the module graph."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []
    for start in graph:
        if start in index:
            continue
        work: list[tuple[str, list[str]]] = [(start, sorted(graph[start]))]
        index[start] = low[start] = len(index)
        stack.append(start)
        on_stack.add(start)
        while work:
            _visit_next(graph, work, (index, low), (stack, on_stack), components)
    return components


def _visit_next(
    graph: Graph,
    work: list[tuple[str, list[str]]],
    numbers: tuple[dict[str, int], dict[str, int]],
    stacked: tuple[list[str], set[str]],
    components: list[list[str]],
) -> None:
    index, low = numbers
    stack, on_stack = stacked
    node, pending = work[-1]
    if pending:
        child = pending.pop()
        if child not in index:
            index[child] = low[child] = len(index)
            stack.append(child)
            on_stack.add(child)
            work.append((child, sorted(graph[child])))
        elif child in on_stack:
            low[node] = min(low[node], index[child])
        return
    work.pop()
    if work:
        parent = work[-1][0]
        low[parent] = min(low[parent], low[node])
    if low[node] == index[node]:
        component: list[str] = []
        while True:
            member = stack.pop()
            on_stack.discard(member)
            component.append(member)
            if member == node:
                break
        components.append(sorted(component))


def cycles(graph: Graph) -> list[list[str]]:
    """The import cycles: components of more than one module and self-imports."""
    return [
        component
        for component in _strong_components(graph)
        if len(component) > 1 or component[0] in graph[component[0]]
    ]


def fan_out_violations(graph: Graph, limit: int = FAN_OUT_LIMIT) -> list[tuple[str, int]]:
    """The modules importing more than ``limit`` internal modules, with their count."""
    return [(module, len(targets)) for module, targets in graph.items() if len(targets) > limit]


def _report(found_cycles: Iterable[list[str]], fan_out: Iterable[tuple[str, int]]) -> int:
    failed = False
    for component in found_cycles:
        failed = True
        print(f"import cycle: {' <-> '.join(component)}", file=sys.stderr)
    for module, count in fan_out:
        failed = True
        print(f"fan-out {count} > {FAN_OUT_LIMIT}: {module}", file=sys.stderr)
    if failed:
        print("structure gate FAILED", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail on an import cycle or a fan-out above 15.")
    parser.add_argument("--root", type=Path, default=MODULE_ROOT, help="the module directory")
    args = parser.parse_args(argv)
    graph = internal_graph(args.root.resolve())
    status = _report(cycles(graph), fan_out_violations(graph))
    if status == 0:
        widest = max((len(targets) for targets in graph.values()), default=0)
        print(f"structure gate: OK ({len(graph)} modules, no cycle, widest fan-out {widest})")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
