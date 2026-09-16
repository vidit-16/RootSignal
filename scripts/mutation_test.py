"""Lightweight mutation testing for core logic.

mutmut does not run natively on Windows, so this does the same job on a small
scale: it applies one AST-level mutation at a time to a target module (swap a
comparison, flip an arithmetic operator, nudge an integer constant), runs the
module's test file, and counts how many mutants the tests kill.

    python scripts/mutation_test.py
    python scripts/mutation_test.py --target src/rootsignal/metrics/kpis.py \
        --tests tests/test_kpis.py tests/test_kpis_edges.py

The target file is always restored, including on Ctrl+C.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

from rootsignal.console import use_utf8_output

ROOT = Path(__file__).resolve().parents[1]

SWAPS: dict[type, type] = {
    ast.Lt: ast.LtE,
    ast.LtE: ast.Lt,
    ast.Gt: ast.GtE,
    ast.GtE: ast.Gt,
    ast.Eq: ast.NotEq,
    ast.NotEq: ast.Eq,
    ast.Add: ast.Sub,
    ast.Sub: ast.Add,
    ast.Mult: ast.Div,
    ast.Div: ast.Mult,
    ast.Mod: ast.FloorDiv,
}


def _sites(tree: ast.AST) -> list[tuple[int, str]]:
    """Every mutable node, identified by its walk position and a label."""
    sites = []
    for index, node in enumerate(ast.walk(tree)):
        if isinstance(node, ast.Compare) and type(node.ops[0]) in SWAPS:
            sites.append((index, f"line {node.lineno}: {type(node.ops[0]).__name__}"))
        elif isinstance(node, ast.BinOp) and type(node.op) in SWAPS:
            sites.append((index, f"line {node.lineno}: {type(node.op).__name__}"))
        elif (
            isinstance(node, ast.Constant)
            and type(node.value) is int
            and hasattr(node, "lineno")
        ):
            sites.append((index, f"line {node.lineno}: int {node.value}"))
    return sites


def _mutate(source: str, target_index: int) -> str:
    tree = ast.parse(source)
    for index, node in enumerate(ast.walk(tree)):
        if index != target_index:
            continue
        if isinstance(node, ast.Compare):
            node.ops[0] = SWAPS[type(node.ops[0])]()
        elif isinstance(node, ast.BinOp):
            node.op = SWAPS[type(node.op)]()
        elif isinstance(node, ast.Constant):
            node.value = node.value + 1
        break
    return ast.unparse(tree)


def main() -> int:
    use_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", default="src/rootsignal/metrics/kpis.py")
    parser.add_argument("--tests", nargs="+", default=["tests/test_kpis.py", "tests/test_kpis_edges.py"])
    args = parser.parse_args()

    target = ROOT / args.target
    if not target.is_file():
        print(f"Target not found: {target}", file=sys.stderr)
        return 2
    original = target.read_text(encoding="utf-8")
    sites = _sites(ast.parse(original))
    killed, survivors = 0, []
    try:
        for index, label in sites:
            target.write_text(_mutate(original, index), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *args.tests],
                cwd=ROOT,
                capture_output=True,
            )
            if result.returncode != 0:
                killed += 1
            else:
                survivors.append(label)
    finally:
        target.write_text(original, encoding="utf-8")

    total = len(sites)
    score = 100.0 * killed / total if total else 0.0
    print(f"{args.target}: {killed}/{total} mutants killed ({score:.1f}%)")
    for label in survivors:
        print(f"  survived: {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
